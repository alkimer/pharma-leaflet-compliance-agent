## Descripción del proyecto (MVP sin writer ni evaluator)

**ANMAT Prospect Rule Checker (MVP)**

### Objetivo

Este proyecto implementa un **pipeline de agentes LLM** para:

> Dado un **prospecto** de un medicamento y un conjunto de **disposiciones ANMAT en formato JSON de reglas**,
>
> 1. determinar qué disposiciones aplican al prospecto,
> 2. verificar **regla por regla** si se cumplen,
> 3. y, para cada **regla incumplida**, generar el **texto que falta** junto con los metadatos de la regla.

⚠️ En este MVP **no se arma el prospecto corregido** ni se implementa un evaluador global.
El flujo termina en un **informe estructurado de incumplimientos y textos sugeridos**.

---

## Modelo de ejecución en la API de OpenAI

* Cada componente lógico del pipeline:

  * **DISPOSITION_CLASSIFIER**
  * **CHECKER_ASSISTANT**
  * **ADEQUATOR_ASSISTANT**
    se corresponde con un **Assistant de la API de OpenAI**, con su propio `assistant_id` y su propio *system prompt*.

* Para cada ejecución del pipeline (un prospecto):

  * Se crea un **thread nuevo por cada assistant** que interviene.
  * En ese thread, **el primer mensaje** que se envía al assistant contiene siempre el **`prospect_text` completo** (y, si aplica, el set de disposiciones/reglas).
  * Luego, en mensajes subsiguientes dentro del **mismo thread**, se van enviando:

    * para el `DISPOSITION_CLASSIFIER`: consultas adicionales si hiciera falta,
    * para el `CHECKER_ASSISTANT`: las reglas, una por una o agrupadas,
    * para el `ADEQUATOR_ASSISTANT`: las reglas incumplidas, una por una o agrupadas.
  * De esta forma, cada assistant **aprovecha el contexto persistente del thread**, evitando reenviar el prospecto completo y las instrucciones en cada llamada.

El backend solo necesita administrar:

* los `assistant_id` (definidos previamente),
* los `thread_id` creados por pipeline,
* y el flujo de mensajes según la etapa del pipeline.

---

## Pipeline del MVP

El pipeline tiene **tres etapas**:

1. **DISPOSITION_CLASSIFIER**
2. **CHECKER_ASSISTANT**
3. **ADEQUATOR_ASSISTANT**

---

### 1. DISPOSITION_CLASSIFIER – Verificación de disposiciones aplicables

* Entrada:

  * `prospect_text` (texto plano)
  * `all_dispositions`: lista de disposiciones ANMAT disponibles, cada una con:

    * `disposition_id` (ej. `ANMAT_753_2012`)
    * `title`
    * `description` (breve resumen de la disposición)
    * otros metadatos útiles

* Salida:

  * `applicable_dispositions`: lista de IDs de disposiciones ANMAT que aplican al prospecto
    (ej. `["ANMAT_753_2012", "ANMAT_1210_1999"]`)

Implementación posible:

* Como servicio/clase aparte (modelo de clasificación con embeddings + reglas), o
* como otro Assistant LLM simple.

Lo importante para el resto del pipeline es que entregue la lista de disposiciones que hay que usar.

---

### 2. CHECKER_ASSISTANT – Verificación de reglas

Usa las disposiciones aplicables y sus JSON de reglas (por ejemplo `Dispo-753-12-–-PROSPECTOS-DE-VENTA-LIBRE_rules_2.json`).

* Entrada:

  * `prospect_text`
  * `rulesets`: lista de disposiciones aplicables, cada una con:

    * `disposition_id`
    * `rules[]` (cada una con campos como `rule_id`, `description`, `must_include_phrases`, etc.)

* Lógica MVP:

  * El código:

    * recorre todas las `rules` de todos los `rulesets` en un `for`,
    * por cada regla hace **1 llamada** al `CHECKER_ASSISTANT`, dentro del mismo thread, con:

      * el `prospect_text` (ya conocido en el contexto del thread),
      * la regla actual.

* Output por regla (ejemplo de estructura):

```json
{
  "disposition_id": "ANMAT_753_2012",
  "rule_id": 29,
  "status": "ok",          // ok | missing | partial | not_applicable
  "evidence_snippets": [
    "frase o párrafo del prospecto que fundamenta el OK (si aplica)"
  ],
  "checker_notes": "Breve explicación de por qué se considera ok/missing/partial."
}
```

* Resultado global del checker:

  * Una lista `rule_evaluations[]` con la evaluación de cada regla aplicable.

---

### 3. ADEQUATOR_ASSISTANT – Sugerencias de texto para reglas incumplidas

En este MVP, el adecuador **NO edita el prospecto**, solo devuelve **qué texto faltaría** para cada regla incumplida.

* Entrada:

  * `prospect_text`
  * `violated_rules`: lista de reglas con `status = "missing"` o `"partial"`, cada una con:

    * `disposition_id`
    * `rule_id`
    * `description`
    * `must_include_phrases` (si existen)
    * severidad y otros metadatos que sean útiles

* Llamada al LLM:

  * Opciones:

    * **Una sola llamada** con todas las `violated_rules`, o
    * un `for` y una llamada por cada regla violada (más simple para el MVP), utilizando siempre el mismo `thread_id` del `ADEQUATOR_ASSISTANT` para aprovechar el contexto.

* Output por regla (idealmente en una lista):

```json
{
  "prospect_id": "P123",
  "disposition_id": "ANMAT_753_2012",
  "rule_id": 29,
  "section_hint": "overdose",   // opcional: sección donde debería ir
  "suggested_text": "Texto que debería agregarse al prospecto para cumplir la regla.",
  "rationale": "Explicación breve de cómo el texto propuesto satisface la disposición.",
  "severity": "critica"
}
```

* Resultado global del adecuador:

  * `suggested_fixes[]`: una entrada por cada regla incumplida, listando:

    * disposición,
    * regla,
    * severidad,
    * texto sugerido.

Con esto ya se puede:

* generar un **informe de no conformidades**, y
* entregárselo a Regulatorio para que haga la adecuación manual del prospecto.

---

## Estructura de carpetas (ajustada al MVP)

```text
project-root/
  common/
  agents/
  graph/
  examples/
```

### `common/`

Código y modelos compartidos:

* `rule_models.*`

  * Modelos:

    * `DispositionRuleset`
    * `Rule`
    * `RuleEvaluation` (status + notas)
    * `SuggestedFix` (para el adecuador)
* `rules_loader.*`

  * Carga JSON de reglas (p.ej. `Dispo-753-12-–-PROSPECTOS-DE-VENTA-LIBRE_rules_2.json`).
* `prospect_io.*`

  * Carga prospectos en texto plano.
* `openai_client.*`

  * Cliente para llamar a Assistants de OpenAI (creación de threads, envío de mensajes, lectura de respuestas).
* `logging_utils.*`

  * Utilidades de logging.

### `agents/`

Lógica específica de cada agente:

* `disposition_classifier.*`

  * Implementa el **DISPOSITION_CLASSIFIER**
    (puede ser modelo ML, Assistant LLM, reglas, etc.).
* `checker_agent.*`

  * Encapsula las llamadas al `CHECKER_ASSISTANT`.
  * Expone funciones tipo:
    `run_checker(prospect_text, rulesets) -> rule_evaluations[]`.
* `adequator_agent.*`

  * Encapsula las llamadas al `ADEQUATOR_ASSISTANT`.
  * Expone algo como:
    `run_adequator(prospect_text, violated_rules) -> suggested_fixes[]`.

### `graph/`

Definición del **grafo principal del MVP** (p.ej. con LangGraph o flujo propio):

* `main_graph.*`

  * Nodos:

    * `N_DISPOSITION_CLASSIFIER`
    * `N_CHECKER`
    * `N_ADEQUATOR`
  * Edges:

    * `N_DISPOSITION_CLASSIFIER → N_CHECKER → N_ADEQUATOR`
  * Estado del grafo:

    * `prospect_id`
    * `prospect_text`
    * `applicable_dispositions`
    * `rulesets`
    * `rule_evaluations`
    * `suggested_fixes`

No hay nodos de orquestador ni evaluador en este MVP.

### `examples/`

* `rules/`

  * JSON de reglas ANMAT (ej. `Dispo-753-12-–-PROSPECTOS-DE-VENTA-LIBRE_rules_2.json`).
* `prospectos/`

  * Prospectos de prueba (`.txt`).
* `runs/`

  * Configs simples para probar el pipeline completo:

    * qué prospecto,
    * qué disposiciones,
    * output esperado (opcional).
