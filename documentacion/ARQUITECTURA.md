# Arquitectura

## Idea general

El pipeline es una cadena de 4 pasos donde **cada paso consume los artefactos que
registró el anterior**. El pegamento es `src/core/run_context.py`: fija el
`<fecha-hora>` de la corrida, crea las carpetas y mantiene
`corridas/<fecha-hora>/manifest.json`.

```
RunContext(stamp)
  ├─ documentos/<stamp>/documentos/files   ← paso 1: disposiciones fuente
  ├─ disposiciones/disposiciones-explotadas/<stamp>/reglas-extraidas             ← paso 1: CARPETA-REGLAS  ──┐
  └─ corridas/<stamp>/                                                 │
       ├─ documento-subido/    ← paso 2: original + texto limpio ──┐   │
       ├─ resultado/           ← paso 3: informe ─────────────┐    │   │
       ├─ documento-adecuado/  ← paso 4: prospecto adecuado ◀─┘◀───┘◀──┘
       ├─ logs/                ← log completo de la corrida
       └─ manifest.json        ← índice de artefactos de los 4 pasos
```

Ningún paso conoce las rutas de otro: pide lo que necesita al manifest
(`ctx.get_path("paso2_prospecto", "clean_text_file")`). De ahí sale gratis poder
retomar una corrida con `--corrida <stamp> --desde N`.

## Qué es de pharma y qué no

La cadena de arriba no tiene nada de farmacéutico: es *norma → reglas → verificación
con evidencia → remediación*. Conviene tener presente dónde está la frontera, porque es
la que hay que cruzar para llevar esto a otra industria.

**Atado al dominio** (se reemplaza):

- `disposiciones/disposiciones-originales/fuentes/` — los documentos normativos de
  origen (apunta ahí `DISPOSITIONS_SOURCES_DIR`).
- `disposiciones/disposiciones-originales/reglas-base/` — las reglas ya extraídas que
  se reutilizan entre corridas (`BASE_RULES_DIR`).
- `src/agents/prompts/*.txt` — los seis prompts. Son el trabajo real de un dominio
  nuevo: ahí viven los criterios que aplicaría un especialista.

**Independiente del dominio** (no se toca):

- `src/pipeline/` — la orquestación, el grafo del paso 3, el manifest, los reintentos.
- `src/agents/*.py` — los agentes: cargan un prompt y un schema, no saben de qué hablan.
- `src/reporting/`, `src/web/`, `src/core/` — informes, interfaz, contabilidad de
  tokens, cancelación, idioma.

El schema de reglas de la sección siguiente es el contrato entre las dos mitades, y por
eso está redactado en términos genéricos (`objective`, `verification_procedure`,
`acceptance_criteria`, `must_include_phrases`, `article_reference`). Los nombres de los
campos y de las carpetas conservan el vocabulario de ANMAT —`disposition_id`,
`prospecto`— porque renombrarlos sería un cambio cosmético con un costo alto en diffs;
el motor no los interpreta.

## Paso 1 — Reglas

`src/pipeline/step1_rules.py` + `src/agents/rules_generator.py`

Dos ramas:

- **Reutilizar**: elige una CARPETA-REGLAS existente (corrida anterior o
  `disposiciones/disposiciones-originales/reglas-base`) y la **copia** a `disposiciones/disposiciones-explotadas/<stamp>/reglas-extraidas`. Se
  copia en lugar de referenciar para que cada corrida quede autocontenida: si
  mañana cambian las reglas base, la corrida vieja sigue siendo reproducible.

- **Generar**: copia los documentos fuente y llama al LLM **dos veces por norma**:

  1. `rules_generator_draft.txt` → borrador de reglas desde el texto crudo.
  2. `rules_generator_audit.txt` → recibe texto + borrador, verifica cobertura,
     descarta lo que no está respaldado y normaliza al schema final.

  Las dos pasadas replican el proceso manual con el que se construyeron las
  reglas de `disposiciones/disposiciones-originales/reglas-base`. Una sola pasada omite requisitos y se
  desvía del schema que consumen el clasificador y el checker.

El `disposition_id` se deriva del nombre del archivo
(`derive_disposition_id`), porque es la clave con la que los pasos 3 y 4 cruzan
las disposiciones.

### Schema de reglas

Lo que esperan los agentes de los pasos 3 y 4:

```json
{
  "disposition_id": "ANMAT_4525_2006",
  "title": "...",
  "source_type": "DISPOSICIÓN | CIRCULAR",
  "sale_condition": "VENTA LIBRE | VENTA BAJO RECETA | null",
  "objective": "...",
  "rules": [
    {
      "rule_id": 1,
      "objective": "qué debe cumplir el prospecto",
      "verification_procedure": "cómo verificarlo",
      "acceptance_criteria": "cuándo se considera cumplido",
      "must_include_phrases": ["..."],
      "article_reference": "Art. 3",
      "attach_reference": ["ANEXO I"]
    }
  ]
}
```

Todo lo que no es `rules` es el **header**, y es lo único que ve el clasificador
(para decidir si la norma aplica) — así el prompt se mantiene chico.

## Paso 2 — Texto limpio

`src/pipeline/step2_prospect.py` + `src/etl/document_text.py`

| Entrada | Estrategia |
|---------|-----------|
| `.md` `.txt` | Pasa tal cual |
| `.docx` | Párrafos y tablas con python-docx |
| `.pdf` con capa de texto | PyMuPDF: instantáneo y sin pérdida |
| `.pdf` escaneado | OCR local DeepSeek-OCR (`src/etl/pdf_ocr_pipeline.py`) |

La decisión es automática: si el PDF rinde menos de `MIN_CHARS_PER_PAGE` (120)
caracteres por página, se asume escaneado y se pasa a OCR. `--forzar-ocr` lo
fuerza.

El OCR se importa de forma perezosa (torch + transformers tardan segundos en
cargar) y guarda un `result.mmd` por página, así que reintentar un PDF a medio
procesar retoma donde quedó.

## Paso 3 — Cumplimiento

`src/pipeline/compliance_graph.py` (LangGraph, 4 nodos)

```
load_prospect → classify_dispositions → check_compliance → generate_report
```

- **classify**: una llamada por disposición con `{prospect_text, disposition_header}`.
- **check**: una llamada por regla con `{prospect_text, disposition_header, rule}`.

Cada llamada es autocontenida y de tamaño acotado. Es más caro en tokens que
mandar todo junto, pero no hay degradación de contexto: la regla 80 se evalúa con
la misma calidad que la 1.

El informe sale en cuatro formatos (`src/reporting/report_generator.py`):
JSON (lo consume el paso 4), Markdown, HTML y PDF. El PDF usa ReportLab
(`pdf_reportlab.py`, self-contained) y cae a xhtml2pdf si falla.

## Paso 4 — Adecuación

`src/pipeline/step4_adequation.py` + `src/agents/prospect_adequator.py`

Filtra el informe a las reglas `missing` de las disposiciones aplicables y hace
**una sola** llamada con `{prospect_text, compliance_report}`. Una llamada por
regla haría que cada una reescribiera el prospecto sin ver los cambios de las
otras.

Salidas: JSON con la traza completa, TXT legible y DOCX con las adecuaciones
resaltadas según las marcas del prompt:

| Marca | Formato en el DOCX |
|-------|--------------------|
| `**texto**` | Negrita |
| `*texto*` | Cursiva roja (adecuación) |
| `*{ref. {...}}*` | Cursiva verde (referencia a la regla) |
| `╬ ... ╬` | Delimitadores de bloque (no se imprimen) |
| `[COMPLETAR ACÁ!]` | Cursiva roja |

## LLM

`src/agents/llm_client.py` envuelve `client.responses.create`. Es **stateless**: sin
threads, sin `assistant_id`, y los prompts versionados en `src/agents/prompts/*.txt`.
Los reintentos ante 429 los maneja el SDK con backoff exponencial
(`MAX_RETRIES`).

Cada agente arma su cliente desde su `ModelConfig` de `src/core/config.py`, así que
modelo, temperature y top_p se cambian por `.env` sin tocar código.

## Mejoras pendientes

1. **Paralelizar el paso 3.** Hoy las llamadas son secuenciales; son
   independientes entre sí, así que un pool de 5-10 workers reduciría el tiempo
   de un análisis completo de ~15 min a ~2 min. Es la mejora de mayor impacto.
2. **Structured outputs** en vez de `json_object` + parseo manual, usando
   `src/agents/prompts/adequator_schema.json`. Elimina el saneamiento de
   caracteres de control y los ````json` que hay que quitar a mano.
3. **Caché de clasificación** por hash del prospecto + hash de la disposición:
   reejecutar el paso 3 sobre el mismo prospecto hoy paga todo de nuevo.
4. **Costos y tokens** por corrida en el manifest (el SDK devuelve `usage`).
5. **Batch API** para el paso 3: 50% más barato, y el análisis no es interactivo.
