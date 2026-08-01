"""
Verificador final del prospecto adecuado (paso 5, opcional).

Es el único agente del pipeline que NO usa OpenAI: revisa con Claude el trabajo
que hicieron los agentes anteriores. Un segundo modelo, de otro proveedor, es
más probable que note lo que el primero dio por bueno — que es justamente el
punto de una revisión independiente.

Recibe en UNA llamada las reglas, el informe de cumplimiento, el prospecto
original y el adecuado, y devuelve un JSON con dos cosas:

  1. si cada adecuación resuelve de verdad la regla que la motivó, y
  2. qué reglas no debería estar cerrando una IA y necesitan una persona.

Notas de implementación:

- Structured outputs (`output_config.format`) garantiza que la respuesta cumpla
  el schema; no hay que sanear texto ni recortar ```json.
- Se usa streaming porque el input es grande (reglas + informe + dos versiones
  del prospecto) y una llamada no-streaming de este tamaño puede chocar contra
  el timeout HTTP del SDK.
- El razonamiento se configura distinto según la generación del modelo, así que
  la request se arma por capacidades (ver `_capabilities`): los modelos 5 / 4.6+
  usan adaptive thinking y `effort`; Haiku 4.5 y anteriores no aceptan `effort`
  —devuelven 400— y usan un presupuesto fijo de tokens de razonamiento.
- Los clasificadores de seguridad de los modelos Opus 5 / Fable 5 pueden declinar
  una request devolviendo un 200 con `stop_reason="refusal"`, así que se chequea
  ANTES de leer el contenido, y en esos modelos se activan los fallbacks del
  servidor para que un rechazo se reintente solo en vez de perder la corrida.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.prompts import FINAL_VERIFIER_INSTRUCTIONS
from core import console
from core.config import VerifierConfig, settings
from core.usage import tracker

logger = logging.getLogger(__name__)

# Beta que habilita `fallbacks: "default"`: si los clasificadores declinan la
# request, el servidor la reintenta solo en el modelo que Anthropic recomiende
# para esa categoría, en la misma llamada.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Modelos que aceptan adaptive thinking + `effort`. En los anteriores (Haiku 4.5,
# Sonnet 4.5) mandar `effort` es un 400, y el razonamiento se pide con un
# presupuesto fijo de tokens.
_MODELOS_CON_EFFORT = (
    "claude-fable-5",
    "claude-mythos-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
)

# Modelos cuyos clasificadores pueden declinar la request y que por eso admiten
# el reintento server-side.
_MODELOS_CON_FALLBACK = ("claude-fable-5", "claude-mythos-5", "claude-opus-5")

# Tokens de razonamiento en los modelos de generación anterior. El mínimo que
# acepta la API es 1024 y tiene que ser menor que `max_tokens`.
_THINKING_BUDGET = 8000

VEREDICTOS = ("correcta", "correcta_con_observaciones", "incorrecta")

# `pendiente_de_dato` es un resultado bueno: el paso 4 dejó un [COMPLETAR ACÁ!]
# donde hace falta un dato que solo tiene una persona (el expediente, el lote).
EVALUACIONES = (
    "resuelta",
    "pendiente_de_dato",
    "parcial",
    "no_resuelta",
    "introduce_error",
)

MOTIVOS_HUMANOS = (
    "ambigua",
    "no_verificable_por_ia",
    "requiere_dato_externo",
    "conflicto_entre_normas",
    "criterio_profesional",
)


def _obj(properties: Dict[str, Any]) -> Dict[str, Any]:
    """
    Objeto del schema con todas sus claves obligatorias.

    Structured outputs exige `additionalProperties: false` y que `required`
    liste todas las propiedades: no hay campos opcionales.
    """
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


# Schema de la respuesta. Es el contrato del paso 5: si se toca acá, hay que
# tocar también el render de texto y la web.
OUTPUT_SCHEMA: Dict[str, Any] = _obj({
    "veredicto": {
        "type": "string",
        "enum": list(VEREDICTOS),
        "description": "Conclusión global sobre la adecuación.",
    },
    "confianza": {
        "type": "number",
        "description": "Confianza del verificador en su propio veredicto (0.0 a 1.0).",
    },
    "resumen": {
        "type": "string",
        "description": "Dos o tres oraciones en español con la conclusión y por qué.",
    },
    "adecuaciones": {
        "type": "array",
        "description": "Una entrada por cada regla que el paso 4 intentó adecuar.",
        "items": _obj({
            "disposition_id": {"type": "string"},
            "rule_id": {"type": "string", "description": "Id de la regla, como texto."},
            "objetivo_regla": {"type": "string", "description": "Qué pedía la regla."},
            "evaluacion": {"type": "string", "enum": list(EVALUACIONES)},
            "fundamento": {
                "type": "string",
                "description": "Por qué, citando el texto del prospecto adecuado.",
            },
            "correccion_sugerida": {
                "type": "string",
                "description": "Redacción concreta a corregir, o cadena vacía si no aplica.",
            },
        }),
    },
    "requiere_revision_humana": {
        "type": "array",
        "description": "Reglas que una IA no debería cerrar sola.",
        "items": _obj({
            "disposition_id": {"type": "string"},
            "rule_id": {"type": "string"},
            "motivo": {"type": "string", "enum": list(MOTIVOS_HUMANOS)},
            "explicacion": {"type": "string"},
            "que_debe_decidir_la_persona": {
                "type": "string",
                "description": "La decisión concreta, no 'revisar esto'.",
            },
        }),
    },
    "riesgos": {
        "type": "array",
        "description": "Problemas que el veredicto no captura por sí solo.",
        "items": _obj({
            "severidad": {"type": "string", "enum": ["alta", "media", "baja"]},
            "descripcion": {"type": "string"},
            "donde": {"type": "string", "description": "Sección o frase del prospecto."},
        }),
    },
    "notas_del_verificador": {
        "type": "string",
        "description": "Limitaciones de esta revisión: qué no se pudo evaluar y por qué.",
    },
})


class FinalVerifier:
    """Revisa con Claude la adecuación producida por el paso 4."""

    def __init__(
        self,
        config: Optional[VerifierConfig] = None,
        instructions: Optional[str] = None,
    ):
        """
        Args:
            config: Parámetros del verificador; por defecto los de `settings.verifier`.
            instructions: System prompt alternativo. Lo usa el laboratorio web
                para probar cambios en el prompt sin tocar el archivo.

        Raises:
            RuntimeError: Si falta la credencial de Anthropic.
        """
        self.config = config or settings.verifier
        self.instructions = instructions or FINAL_VERIFIER_INSTRUCTIONS

        import anthropic  # import perezoso: el SDK solo hace falta en este paso

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(
            api_key=settings.require_anthropic_key(),
            max_retries=settings.max_retries,
        )
        logger.info(f"FinalVerifier listo (model={self.config.model}, effort={self.config.effort})")

    # ---- Armado del pedido --------------------------------------------------

    def _capabilities(self) -> Dict[str, bool]:
        """Qué admite el modelo elegido, para no mandarle parámetros que rechaza."""
        model = self.config.model
        return {
            "effort": model.startswith(_MODELOS_CON_EFFORT),
            "fallbacks": model.startswith(_MODELOS_CON_FALLBACK),
        }

    def _request_params(self, payload: str) -> Dict[str, Any]:
        """Arma los parámetros de la llamada según lo que soporte el modelo."""
        caps = self._capabilities()
        output_config: Dict[str, Any] = {
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        }
        params: Dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "system": self.instructions,
            "messages": [{"role": "user", "content": payload}],
        }

        if caps["effort"]:
            output_config["effort"] = self.config.effort
            params["thinking"] = {"type": "adaptive"}
        else:
            # Generación anterior: sin `effort`, y el razonamiento con presupuesto.
            budget = min(_THINKING_BUDGET, max(self.config.max_output_tokens - 1024, 1024))
            params["thinking"] = {"type": "enabled", "budget_tokens": budget}

        params["output_config"] = output_config

        if caps["fallbacks"]:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    @staticmethod
    def _payload(
        rules: List[Dict[str, Any]],
        compliance_report: Dict[str, Any],
        original_prospect: str,
        adequated_prospect: str,
    ) -> str:
        """Arma el único mensaje de usuario con todo el material a revisar."""
        return json.dumps(
            {
                "rules": rules,
                "compliance_report": compliance_report,
                "original_prospect": original_prospect,
                "adequated_prospect": adequated_prospect,
            },
            ensure_ascii=False,
            indent=2,
        )

    def verify(
        self,
        rules: List[Dict[str, Any]],
        compliance_report: Dict[str, Any],
        original_prospect: str,
        adequated_prospect: str,
    ) -> Dict[str, Any]:
        """
        Revisa la adecuación y devuelve el informe estructurado.

        Returns:
            El dict que cumple `OUTPUT_SCHEMA`, más `model` y `stop_reason`.

        Raises:
            RuntimeError: Si el modelo declina la request o la respuesta no sirve.
        """
        payload = self._payload(rules, compliance_report, original_prospect, adequated_prospect)
        params = self._request_params(payload)
        detalle_effort = f" · effort: {self.config.effort}" if "effort" in params["output_config"] else ""
        console.detail(
            f"modelo: {self.config.model}{detalle_effort} · "
            f"{len(payload):,} caracteres a revisar".replace(",", ".")
        )

        try:
            # Streaming: el input es grande y una llamada no-streaming de este
            # tamaño puede chocar contra el timeout HTTP del SDK.
            with self.client.beta.messages.stream(**params) as stream:
                response = stream.get_final_message()
        except self._anthropic.APIStatusError as e:
            raise RuntimeError(
                f"La verificación final falló ({e.status_code}): {e.message}"
            ) from e

        tracker.record_anthropic(response.model, getattr(response, "usage", None))

        # Un rechazo de los clasificadores llega como 200 con content vacío o
        # parcial: hay que mirar stop_reason antes que el contenido.
        if response.stop_reason == "refusal":
            detalle = getattr(getattr(response, "stop_details", None), "explanation", "")
            raise RuntimeError(
                "El verificador declinó revisar este prospecto "
                f"({getattr(getattr(response, 'stop_details', None), 'category', 's/d')}). {detalle}".strip()
            )
        if response.stop_reason == "max_tokens":
            raise RuntimeError(
                "La respuesta del verificador se cortó por límite de tokens; "
                f"subí VERIFIER_MAX_TOKENS (actual: {self.config.max_output_tokens})"
            )

        texto = next((b.text for b in response.content if b.type == "text"), "")
        if not texto.strip():
            raise RuntimeError(f"El verificador no devolvió contenido (stop_reason={response.stop_reason})")

        result: Dict[str, Any] = json.loads(texto)
        result["model"] = response.model
        result["stop_reason"] = response.stop_reason
        return result


# ============================================================================
# Render a texto
# ============================================================================

_ETIQUETA_EVALUACION = {
    "resuelta": "RESUELTA",
    "pendiente_de_dato": "FALTA UN DATO",
    "parcial": "PARCIAL",
    "no_resuelta": "NO RESUELTA",
    "introduce_error": "INTRODUCE ERROR",
}

_ETIQUETA_MOTIVO = {
    "ambigua": "regla ambigua",
    "no_verificable_por_ia": "no verificable por IA",
    "requiere_dato_externo": "requiere un dato externo",
    "conflicto_entre_normas": "conflicto entre normas",
    "criterio_profesional": "criterio profesional",
}


def render_text(result: Dict[str, Any]) -> str:
    """Convierte el informe en el texto que se guarda y se muestra."""
    ancho = 78
    lineas: List[str] = [
        "=" * ancho,
        "VERIFICACIÓN FINAL DE LA ADECUACIÓN",
        f"modelo: {result.get('model', 's/d')}",
        "=" * ancho,
        "",
        f"VEREDICTO: {result.get('veredicto', 's/d').replace('_', ' ').upper()}"
        f"   (confianza {float(result.get('confianza', 0)) * 100:.0f}%)",
        "",
        result.get("resumen", ""),
        "",
    ]

    adecuaciones = result.get("adecuaciones") or []
    lineas += ["-" * ancho, f"ADECUACIONES REVISADAS ({len(adecuaciones)})", "-" * ancho]
    if not adecuaciones:
        lineas.append("  (el paso 4 no adecuó ninguna regla)")
    for item in adecuaciones:
        etiqueta = _ETIQUETA_EVALUACION.get(item.get("evaluacion", ""), item.get("evaluacion", ""))
        lineas += [
            "",
            f"  [{etiqueta}]  {item.get('disposition_id', 's/d')} · regla {item.get('rule_id', 's/d')}",
            f"    objetivo: {item.get('objetivo_regla', '')}",
            f"    {item.get('fundamento', '')}",
        ]
        if (item.get("correccion_sugerida") or "").strip():
            lineas.append(f"    corrección sugerida: {item['correccion_sugerida']}")

    humanas = result.get("requiere_revision_humana") or []
    lineas += ["", "-" * ancho, f"REQUIERE INTERVENCIÓN HUMANA ({len(humanas)})", "-" * ancho]
    if not humanas:
        lineas.append("  (ninguna regla quedó pendiente de criterio humano)")
    for item in humanas:
        motivo = _ETIQUETA_MOTIVO.get(item.get("motivo", ""), item.get("motivo", ""))
        lineas += [
            "",
            f"  {item.get('disposition_id', 's/d')} · regla {item.get('rule_id', 's/d')}  —  {motivo}",
            f"    {item.get('explicacion', '')}",
            f"    a decidir: {item.get('que_debe_decidir_la_persona', '')}",
        ]

    riesgos = result.get("riesgos") or []
    if riesgos:
        lineas += ["", "-" * ancho, f"RIESGOS ({len(riesgos)})", "-" * ancho]
        for item in riesgos:
            lineas += [
                "",
                f"  [{item.get('severidad', 's/d').upper()}] {item.get('descripcion', '')}",
                f"    en: {item.get('donde', '')}",
            ]

    notas = (result.get("notas_del_verificador") or "").strip()
    if notas:
        lineas += ["", "-" * ancho, "NOTAS DEL VERIFICADOR", "-" * ancho, "", notas]

    lineas += ["", "=" * ancho, ""]
    return "\n".join(lineas)


def write_outputs(result: Dict[str, Any], output_dir: Path) -> Dict[str, Path]:
    """Escribe el informe en JSON y en texto. Devuelve las rutas."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "verificacion_final.json"
    text_path = output_dir / "verificacion_final.txt"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    text_path.write_text(render_text(result), encoding="utf-8")
    return {"json": json_path, "txt": text_path}
