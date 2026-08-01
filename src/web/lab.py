"""
Laboratorio: correr un paso suelto con entradas, parámetros y prompt a medida.

El pipeline completo tarda minutos y encadena cinco pasos. Cuando lo que se
quiere es afinar UN prompt o comparar dos modelos, eso es carísimo en tiempo y
en tokens. Acá cada agente se ejecuta solo, con:

    entradas    el prospecto, la disposición, el informe… editables en la página
    parámetros  modelo, temperature, effort — sin tocar el .env
    prompt      el system prompt real, editable, sin escribir el archivo

Los valores por defecto salen de `tests/integracion/fixtures`, que son los
mismos casos mínimos de las pruebas de integración: una disposición de dos
reglas y un prospecto de veinte líneas. Una ejecución cuesta centavos.

Nada de lo que se hace acá persiste: el prompt editado vale para esa corrida y
no se escribe al repo.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agents import prompts as prompt_bank
from core.config import ModelConfig, VerifierConfig, settings
from core.usage import tracker

logger = logging.getLogger(__name__)

FIXTURES = settings.project_root / "tests" / "integracion" / "fixtures"
PROMPTS_DIR = Path(prompt_bank.__file__).parent


def _fixture(name: str) -> str:
    """Contenido de un fixture, o vacío si no está (el laboratorio no debe romperse)."""
    path = FIXTURES / name
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        logger.warning(f"Falta el fixture {path}")
        return ""


def _prompt(name: str) -> str:
    try:
        return (PROMPTS_DIR / name).read_text(encoding="utf-8")
    except OSError:  # pragma: no cover — el prompt siempre está en el repo
        return ""


# ============================================================================
# Descripción de los campos
# ============================================================================

@dataclass
class Campo:
    """Un campo editable de la página: entrada, parámetro o prompt."""

    nombre: str
    etiqueta: str
    tipo: str = "texto"  # texto | area | numero | opciones
    valor: Any = ""
    ayuda: str = ""
    opciones: List[str] = field(default_factory=list)
    lenguaje: str = ""  # json | md — solo informativo, para el editor

    def as_dict(self) -> Dict[str, Any]:
        return {
            "nombre": self.nombre,
            "etiqueta": self.etiqueta,
            "tipo": self.tipo,
            "valor": self.valor,
            "ayuda": self.ayuda,
            "opciones": self.opciones,
            "lenguaje": self.lenguaje,
        }


@dataclass
class PasoLab:
    """Un paso ejecutable del laboratorio."""

    id: str
    numero: str
    titulo: str
    descripcion: str
    agente: str
    entradas: List[Campo]
    parametros: List[Campo]
    prompts: List[Campo]
    ejecutar: Callable[[Dict[str, str], Dict[str, str], Dict[str, str]], Dict[str, Any]]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "numero": self.numero,
            "titulo": self.titulo,
            "descripcion": self.descripcion,
            "agente": self.agente,
            "entradas": [c.as_dict() for c in self.entradas],
            "parametros": [c.as_dict() for c in self.parametros],
            "prompts": [c.as_dict() for c in self.prompts],
        }


# ============================================================================
# Helpers de parámetros
# ============================================================================

MODELOS_OPENAI = ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "gpt-5"]
MODELOS_CLAUDE = [
    "claude-haiku-4-5",
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-fable-5",
]


def _campo_modelo(valor: str, opciones: List[str]) -> Campo:
    return Campo(
        "model", "Modelo", "opciones", valor,
        "Se puede escribir otro que no esté en la lista.", opciones,
    )


def _campo_temperature(valor: Optional[float]) -> Campo:
    return Campo(
        "temperature", "Temperature", "numero", "" if valor is None else valor,
        "Vacío = no se manda y el modelo usa su default.",
    )


def _model_config(base: ModelConfig, params: Dict[str, str]) -> ModelConfig:
    """Arma un ModelConfig con lo que se pidió en la página, cayendo al del .env."""
    def _float(clave: str, default: Optional[float]) -> Optional[float]:
        crudo = (params.get(clave) or "").strip()
        if not crudo:
            return default
        try:
            return float(crudo)
        except ValueError:
            raise ValueError(f"{clave} no es un número: {crudo!r}")

    return ModelConfig(
        model=(params.get("model") or base.model).strip(),
        temperature=_float("temperature", base.temperature),
        top_p=_float("top_p", base.top_p),
        max_output_tokens=base.max_output_tokens,
        delay_between_calls=0.0,
    )


def _json_o_error(crudo: str, que: str) -> Any:
    try:
        return json.loads(crudo)
    except json.JSONDecodeError as e:
        raise ValueError(f"{que} no es JSON válido: {e}") from e


# ============================================================================
# Ejecutores — uno por paso
# ============================================================================

def _correr_paso1(entradas, params, prompts) -> Dict[str, Any]:
    """Disposición → reglas JSON. Es el único agente con dos prompts encadenados."""
    from agents.rules_generator import RulesGenerator

    generator = RulesGenerator(config=_model_config(settings.rules_generator, params))
    if prompts.get("draft"):
        generator.draft_client.instructions = prompts["draft"]
    if prompts.get("audit"):
        generator.audit_client.instructions = prompts["audit"]

    reglas = generator.generate(
        disposition_id=(entradas.get("disposition_id") or "ANMAT_LAB").strip(),
        raw_text=entradas["texto_disposicion"],
    )
    return {"formato": "json", "salida": reglas}


def _correr_clasificador(entradas, params, prompts) -> Dict[str, Any]:
    """Decide si una disposición aplica a un prospecto."""
    from agents.disposition_classifier import DispositionClassifier

    disposicion = _json_o_error(entradas["disposicion"], "La disposición")
    # La CARPETA-REGLAS no se usa acá —la disposición viene en la entrada—, pero
    # el clasificador la exige al construirse: se le pasa la de los fixtures.
    clasificador = DispositionClassifier(
        rules_dir=FIXTURES / "reglas",
        config=_model_config(settings.classifier, params),
    )
    if prompts.get("classifier"):
        clasificador.client.instructions = prompts["classifier"]

    header = {k: v for k, v in disposicion.items() if k != "rules"}
    return {
        "formato": "json",
        "salida": clasificador._classify_one(entradas["prospecto"], header),
    }


def _correr_checker(entradas, params, prompts) -> Dict[str, Any]:
    """Verifica las reglas de una disposición contra el prospecto."""
    from agents.compliance_checker import ComplianceChecker

    disposicion = _json_o_error(entradas["disposicion"], "La disposición")
    checker = ComplianceChecker(config=_model_config(settings.checker, params))
    if prompts.get("checker"):
        checker.client.instructions = prompts["checker"]

    return {
        "formato": "json",
        "salida": checker.check(
            prospect_text=entradas["prospecto"], disposition=disposicion
        ),
    }


def _correr_paso2(entradas, params, prompts) -> Dict[str, Any]:
    """Extrae texto de un documento. No llama a ningún modelo."""
    from etl.document_text import extract_document

    ruta = Path((entradas.get("ruta_documento") or "").strip())
    if not ruta.is_file():
        raise ValueError(f"No existe el archivo: {ruta}")

    extraccion = extract_document(ruta)
    return {
        "formato": "texto",
        "salida": extraccion.text,
        "detalle": {
            "metodo": extraccion.method,
            "paginas": extraccion.pages,
            "caracteres": extraccion.char_count,
        },
    }


def _correr_paso4(entradas, params, prompts) -> Dict[str, Any]:
    """Reescribe el prospecto resolviendo las reglas incumplidas del informe."""
    from agents.prospect_adequator import ProspectAdequator

    adecuador = ProspectAdequator(config=_model_config(settings.adequator, params))
    if prompts.get("adequator"):
        adecuador.client.instructions = prompts["adequator"]

    informe = _json_o_error(entradas["informe"], "El informe de cumplimiento")
    filtrado = adecuador.filter_compliance_report(informe)
    resultado = adecuador.adequate(
        prospect_text=entradas["prospecto"], filtered_compliance_report=filtrado
    )
    return {
        "formato": "texto",
        "salida": resultado.get("updated_prospect_text", ""),
        "detalle": {
            "reglas_adecuadas": filtrado["summary"]["total_missing_rules"],
            "notas": resultado.get("adequation_notes", ""),
        },
    }


def _correr_paso5(entradas, params, prompts) -> Dict[str, Any]:
    """Revisa la adecuación con Claude."""
    from agents.final_verifier import FinalVerifier, render_text

    base = settings.verifier
    config = VerifierConfig(
        model=(params.get("model") or base.model).strip(),
        effort=(params.get("effort") or base.effort).strip(),
        max_output_tokens=int(params.get("max_output_tokens") or base.max_output_tokens),
    )
    verificador = FinalVerifier(config=config, instructions=prompts.get("final_verifier"))
    resultado = verificador.verify(
        rules=[_json_o_error(entradas["reglas"], "Las reglas")],
        compliance_report=_json_o_error(entradas["informe"], "El informe de cumplimiento"),
        original_prospect=entradas["prospecto_original"],
        adequated_prospect=entradas["prospecto_adecuado"],
    )
    return {"formato": "json", "salida": resultado, "texto": render_text(resultado)}


# ============================================================================
# Catálogo
# ============================================================================

def _catalogo() -> List[PasoLab]:
    """Se arma en cada pedido para que los prompts editados en disco se reflejen."""
    prospecto = _fixture("prospecto_minimo.md")
    disposicion = _fixture("reglas/disposicion_minima_rules.json")
    informe = _fixture("compliance_report_minimo.json")
    adecuado = _fixture("prospecto_adecuado_minimo.txt")

    return [
        PasoLab(
            id="paso1_reglas",
            numero="1",
            titulo="Disposición → reglas JSON",
            descripcion=(
                "Extrae reglas verificables del texto de una norma. Dos pasadas: "
                "un borrador y una auditoría que lo normaliza al schema final."
            ),
            agente="RulesGenerator (OpenAI)",
            entradas=[
                Campo("texto_disposicion", "Texto de la disposición", "area",
                      _fixture("disposicion_minima.md"), lenguaje="md"),
                Campo("disposition_id", "disposition_id", "texto", "ANMAT_LAB_9001_2026",
                      "El id que va a llevar el JSON generado."),
            ],
            parametros=[
                _campo_modelo(settings.rules_generator.model, MODELOS_OPENAI),
                _campo_temperature(settings.rules_generator.temperature),
            ],
            prompts=[
                Campo("draft", "Prompt pasada 1 — borrador", "area",
                      _prompt("rules_generator_draft.txt")),
                Campo("audit", "Prompt pasada 2 — auditoría", "area",
                      _prompt("rules_generator_audit.txt")),
            ],
            ejecutar=_correr_paso1,
        ),
        PasoLab(
            id="paso2_texto",
            numero="2",
            titulo="Documento → texto limpio",
            descripcion=(
                "Extrae el texto de un PDF, DOCX, MD o TXT. Es el único paso que "
                "no llama a ningún modelo: no tiene prompt ni parámetros."
            ),
            agente="etl.document_text (sin API)",
            entradas=[
                Campo("ruta_documento", "Ruta del documento", "texto",
                      str(FIXTURES / "prospecto_minimo.md"),
                      "Ruta absoluta en esta máquina."),
            ],
            parametros=[],
            prompts=[],
            ejecutar=_correr_paso2,
        ),
        PasoLab(
            id="paso3a_clasificador",
            numero="3a",
            titulo="¿Aplica esta disposición?",
            descripcion=(
                "Decide si una disposición alcanza al prospecto. Recibe el prospecto "
                "y el header de la norma, sin sus reglas."
            ),
            agente="DispositionClassifier (OpenAI)",
            entradas=[
                Campo("prospecto", "Prospecto", "area", prospecto, lenguaje="md"),
                Campo("disposicion", "Disposición (JSON)", "area", disposicion, lenguaje="json"),
            ],
            parametros=[
                _campo_modelo(settings.classifier.model, MODELOS_OPENAI),
                _campo_temperature(settings.classifier.temperature),
            ],
            prompts=[Campo("classifier", "Prompt del clasificador", "area",
                           _prompt("classifier.txt"))],
            ejecutar=_correr_clasificador,
        ),
        PasoLab(
            id="paso3b_checker",
            numero="3b",
            titulo="¿Se cumple cada regla?",
            descripcion=(
                "Evalúa regla por regla: ok / missing / not_applicable / not_evaluable, "
                "con la evidencia que encontró en el prospecto."
            ),
            agente="ComplianceChecker (OpenAI)",
            entradas=[
                Campo("prospecto", "Prospecto", "area", prospecto, lenguaje="md"),
                Campo("disposicion", "Disposición con sus reglas (JSON)", "area",
                      disposicion, lenguaje="json"),
            ],
            parametros=[
                _campo_modelo(settings.checker.model, MODELOS_OPENAI),
                _campo_temperature(settings.checker.temperature),
            ],
            prompts=[Campo("checker", "Prompt del checker", "area", _prompt("checker.txt"))],
            ejecutar=_correr_checker,
        ),
        PasoLab(
            id="paso4_adecuacion",
            numero="4",
            titulo="Adecuar el prospecto",
            descripcion=(
                "Reescribe el prospecto resolviendo las reglas que el informe marcó "
                "como incumplidas, delimitando lo agregado con ╬."
            ),
            agente="ProspectAdequator (OpenAI)",
            entradas=[
                Campo("prospecto", "Prospecto original", "area", prospecto, lenguaje="md"),
                Campo("informe", "Informe de cumplimiento (JSON)", "area",
                      informe, lenguaje="json"),
            ],
            parametros=[
                _campo_modelo(settings.adequator.model, MODELOS_OPENAI),
                _campo_temperature(settings.adequator.temperature),
                Campo("top_p", "Top-p", "numero",
                      "" if settings.adequator.top_p is None else settings.adequator.top_p),
            ],
            prompts=[Campo("adequator", "Prompt del adecuador", "area",
                           _prompt("adequator.txt"))],
            ejecutar=_correr_paso4,
        ),
        PasoLab(
            id="paso5_verificacion",
            numero="5",
            titulo="Verificación final",
            descripcion=(
                "Revisa con Claude si la adecuación es correcta y marca qué reglas "
                "necesitan criterio humano."
            ),
            agente="FinalVerifier (Anthropic)",
            entradas=[
                Campo("reglas", "Disposición con sus reglas (JSON)", "area",
                      disposicion, lenguaje="json"),
                Campo("informe", "Informe de cumplimiento (JSON)", "area",
                      informe, lenguaje="json"),
                Campo("prospecto_original", "Prospecto original", "area",
                      prospecto, lenguaje="md"),
                Campo("prospecto_adecuado", "Prospecto adecuado", "area",
                      adecuado, lenguaje="md"),
            ],
            parametros=[
                _campo_modelo(settings.verifier.model, MODELOS_CLAUDE),
                Campo("effort", "Effort", "opciones", settings.verifier.effort,
                      "Sólo lo usan los modelos 5/4.6+; Haiku 4.5 lo rechaza.",
                      ["low", "medium", "high", "xhigh", "max"]),
                Campo("max_output_tokens", "Máx. tokens de salida", "numero",
                      settings.verifier.max_output_tokens),
            ],
            prompts=[Campo("final_verifier", "Prompt del verificador", "area",
                           _prompt("final_verifier.txt"))],
            ejecutar=_correr_paso5,
        ),
    ]


def catalogo() -> List[Dict[str, Any]]:
    """Los pasos disponibles, con sus campos y valores por defecto."""
    return [paso.as_dict() for paso in _catalogo()]


def ejecutar(
    paso_id: str,
    entradas: Dict[str, str],
    parametros: Dict[str, str],
    prompts: Dict[str, str],
) -> Dict[str, Any]:
    """
    Corre un paso y devuelve su salida, con lo que consumió.

    El consumo se mide alrededor de esta llamada: el tracker es global y el
    laboratorio corre de a un paso por vez.

    Raises:
        KeyError: Si el paso no existe.
        ValueError: Si una entrada no es válida (JSON roto, ruta inexistente).
    """
    paso = next((p for p in _catalogo() if p.id == paso_id), None)
    if paso is None:
        raise KeyError(f"Paso desconocido: {paso_id}")

    tracker.reset()
    empezo = time.monotonic()
    resultado = paso.ejecutar(entradas, parametros, prompts)
    duracion = time.monotonic() - empezo

    return {
        "paso": paso.id,
        "titulo": paso.titulo,
        "duracion_s": round(duracion, 2),
        "consumo": tracker.snapshot(),
        **resultado,
    }
