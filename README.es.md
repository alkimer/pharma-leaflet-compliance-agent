# pharma-leaflet-compliance-agent — Analizador y adecuador de prospectos (ANMAT)

> Versión en español del [README en inglés](README.md).

Pipeline que toma un prospecto de especialidad medicinal, lo verifica contra las
disposiciones y circulares de ANMAT convertidas en reglas, y devuelve un informe
de cumplimiento más una versión adecuada del prospecto.

> Está hecho para pharma, pero no está soldado a pharma: el motor resuelve cualquier
> problema con forma de *norma → documento → cumplimiento*. Cambiar de industria es
> cambiar el corpus normativo y los prompts, no el pipeline — ver
> [Pharma es el primer dominio, no el único](#pharma-es-el-primer-dominio-no-el-único).

```
disposiciones (PDF)  ──paso 1──▶  reglas JSON
                                      │
prospecto (PDF/MD)   ──paso 2──▶  texto limpio
                                      │
                                 paso 3 ──▶  informe de cumplimiento (JSON/MD/HTML/PDF)
                                      │
                                 paso 4 ──▶  prospecto adecuado (JSON/TXT/DOCX)
```

![Una corrida completa, del prospecto al informe y al documento adecuado](documentacion/media/corrida.gif)

<sub>Una corrida real sobre el prospecto de ejemplo, acelerada. 15 disposiciones
clasificadas, 4 aplicables, **85 reglas verificadas a una llamada al modelo cada una** —
29 cumplidas, 33 incumplidas, 17 que no se pueden decidir sin información externa.
**101 llamadas, 6 min 32 s, US$ 0,23**, con el 81,5% de los tokens de entrada servidos
desde el caché (US$ 0,15 ahorrados). Son los números de la propia corrida, leídos de la
API e impresos al final de cada ejecución.</sub>

## Pharma es el primer dominio, no el único

El problema que resuelve no es exclusivo de los prospectos. Es la forma que comparte
todo documento regulado: **un cuerpo de normas escritas de un lado, un documento que
tiene que satisfacerlas del otro, y una persona cruzando cláusula por cláusula.** Nada
en el motor sabe qué es un prospecto: sabe convertir prosa normativa en reglas
verificables, decidir cuáles aplican, verificar cada una contra un texto citando la
evidencia, y reescribir lo que falta.

Lo que sí está atado a pharma es poco y vive en dos lugares:

| Atado al dominio | Independiente del dominio |
|---|---|
| El corpus normativo en `disposiciones/` (disposiciones y circulares de ANMAT) | La orquestación de los 5 pasos y el `manifest.json` retomable |
| La redacción de los 6 prompts en `src/agents/prompts/` | El schema de reglas: objetivo, procedimiento de verificación, criterio de aceptación, frases obligatorias, referencia al artículo |
| El vocabulario de los informes («disposición», «prospecto») | Una llamada por regla, evidencia citada, cero análisis parciales, dos capas de reintentos |
| — | Extracción de reglas en dos pasadas, caché de prompts, contabilidad de tokens y costo, la web y la CLI |

**Para apuntarlo a otra industria se cambian el corpus y los prompts. El pipeline no se
toca.** En concreto: dejás los PDF de las normas nuevas en la carpeta a la que apunta
`DISPOSITIONS_SOURCES_DIR`, corrés el paso 1 para extraer las reglas, y adaptás los seis
archivos de prompts al vocabulario de ese dominio. Del paso 2 en adelante no cambia nada:
el mismo clasificador, el mismo checker regla por regla, la misma adecuación, los mismos
informes.

El schema de reglas es genérico a propósito. `must_include_phrases` es «el texto literal
que la norma exige», `article_reference` es «dónde lo dice», `acceptance_criteria` es
«cuándo se considera cumplido»: nada de eso es farmacéutico. Sirve igual para rotulado de
alimentos, cosmética, productos médicos, prospectos financieros, condiciones de pólizas,
procedimientos GMP/ISO o un contrato verificado contra una política interna, sin tocar una
línea del schema.

Dos advertencias honestas. Primero, los identificadores del código hablan el dominio
original (`prospecto`, `disposicion`): al motor le da igual, pero quien lea el fuente va a
ver nombres farmacéuticos. Segundo, los prompts son el verdadero trabajo de un dominio
nuevo: ahí están los criterios que aplicaría un especialista, y portarlos bien lo hace
alguien que conoce esa normativa, no un buscar-y-reemplazar de una tarde.

## Por qué está hecho así

- **Una llamada al modelo por regla, nunca un prompt gigante.** Cada llamada lleva el
  prospecto + una regla y nada más. Cuesta más tokens que agrupar, pero la regla 80 se
  evalúa con la misma calidad que la 1: no hay degradación de contexto.
- **Lo caro sale gratis.** Como el prospecto va siempre primero y solo cambia la regla al
  final, el caché de prompts de OpenAI cubre el prefijo común y lo factura al 25%. Un
  `prompt_cache_key` derivado del prospecto fija el ruteo para que el caché realmente pegue.
  El informe de la corrida muestra el ahorro real, leído de los `cached_tokens` de la API.
- **Nunca un análisis parcial.** Si una regla no se puede evaluar después de sus reintentos,
  la corrida falla. Un informe con reglas sin verificar haría que el paso 4 concluyera que
  «el prospecto ya cumple»: la respuesta equivocada más peligrosa en un flujo regulatorio.
- **Dos capas de reintentos que no se pisan.** El SDK cubre el transporte (429, timeouts) con
  backoff; el pipeline cubre las respuestas que llegaron pero no sirven (no parsean, vienen
  truncadas).
- **Las reglas se extraen en dos pasadas.** Un borrador lee la norma y una auditoría vuelve
  al texto, descarta lo no respaldado y normaliza el schema. Una sola pasada omite requisitos.
- **Todos los pasos son retomables.** Cada paso registra sus artefactos en el
  `manifest.json` de la corrida y el siguiente los lee de ahí.
- **Todo el pipeline se puede probar sin gastar un centavo** (ver «Banco de pruebas»).

## Instalación

```bash
git clone https://github.com/alkimer/pharma-leaflet-compliance-agent.git
cd pharma-leaflet-compliance-agent

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # completar OPENAI_API_KEY
```

El OCR local es opcional: solo hace falta para PDFs **escaneados**
(`pip install -r requirements-ocr.txt` + `OCR_MODEL_DIR` en el `.env`).

## Uso

Hay tres formas de usarlo: la **terminal**, la **web** y el **banco de pruebas offline**.

### 1. Terminal

```bash
python run_pipeline.py
```

Modo interactivo: pregunta si ya tenés reglas generadas, pide el prospecto
(podés arrastrar el archivo a la terminal) y ejecuta los 4 pasos con logs
detallados y coloreados.

```bash
# Sin preguntas, para automatizar
python run_pipeline.py --prospecto ejemplos/prospectos/IBUPROFENO-DEMO.md --no-interactivo

# Generar las reglas desde cero a partir de los PDF de las disposiciones
python run_pipeline.py --generar-reglas

# Retomar una corrida existente desde un paso puntual
python run_pipeline.py --listar-corridas
python run_pipeline.py --corrida 20260728-1745 --desde 3

# Forzar el OCR local aunque el PDF tenga capa de texto
python run_pipeline.py --prospecto escaneado.pdf --forzar-ocr
```

### 2. Interfaz web

```bash
pip install -r requirements-web.txt
python run_web.py                 # abre http://127.0.0.1:8000
```

El mismo pipeline, sin preguntas: el prospecto se sube o se elige de los
ejemplos, y la corrida se ve en vivo — los 5 pasos se van encendiendo, la
consola muestra cada evento a medida que pasa y al final quedan los artefactos,
con el informe PDF abriéndose en un visor dentro de la misma página. Si
recargás la página en medio de una corrida, se reengancha donde iba.

Dos vistas sobre la misma API:

| Ruta | Vista |
|------|-------|
| `/` | Estética de terminal: consola completa, barras de progreso por paso, tiles de consumo |
| `/minimal` | Clara y minimalista: sólo el proceso y los dos resultados que importan |

Las dos vistas son **bilingües**: dos banderitas arriba a la derecha cambian entre inglés
(el default) y español. La elección se recuerda entre recargas, y al cambiar se repinta lo
que ya está en pantalla —incluidos los resultados de una corrida terminada— sin recargar la
página. El log del pipeline sigue en español: esas líneas vienen del backend.

**Detener corrida** cancela la ejecución. La cancelación es cooperativa (no se
puede matar un hilo en Python): el pipeline corta en el próximo punto de
chequeo, así que tarda lo que tarde la llamada al modelo que está en vuelo —en
la práctica, un par de segundos. Los pasos que ya terminaron dejan sus
artefactos; el paso 4 no se ejecuta.

La web se cuelga de los eventos que emite `src/core/console.py`, así que
terminal y navegador muestran exactamente lo mismo.

### 3. Banco de pruebas (sin API key, sin tokens)

```bash
python tests/smoke_pipeline.py      # 53 chequeos, sin gastar tokens
```

Es el laboratorio para trabajar sobre el pipeline sin pagarlo. Corre los 4 pasos con
respuestas del LLM simuladas y valida: el encadenamiento vía manifest, el parseo de cada
agente, las dos ramas del paso 1 (reutilizar y generar con LLM), los 4 formatos del informe,
las marcas de formato del DOCX y la política de reintentos y corte (un fallo transitorio se
recupera; una regla que no se puede evaluar corta la corrida y el paso 4 se niega a correr
sobre un informe incompleto). Se ejecuta en cada push vía GitHub Actions.

## Los pasos

| Paso | Qué hace | Salida |
|------|----------|--------|
| **0** | Calcula el `<fecha-hora>` de la corrida (`AAAAmmDD-HHMM`) y crea las carpetas | — |
| **1** | Disposiciones → reglas JSON. Pregunta si ya existen: si **sí**, copia una CARPETA-REGLAS existente; si **no**, las genera con el LLM en dos pasadas (borrador + auditoría) | `disposiciones/disposiciones-explotadas/<fecha-hora>/reglas-extraidas` |
| **2** | Prospecto → texto limpio. `.md`/`.txt` pasan tal cual; los PDF usan la capa de texto nativa y caen a OCR local solo si están escaneados | `corridas/<fecha-hora>/documento-subido` |
| **3** | Verificación de cumplimiento: clasifica qué disposiciones aplican y evalúa regla por regla | `corridas/<fecha-hora>/resultado` |
| **4** | Adecuación one-shot: reescribe el prospecto resolviendo las reglas incumplidas | `corridas/<fecha-hora>/documento-adecuado` |

Cada paso registra sus artefactos en `corridas/<fecha-hora>/manifest.json`, y el
siguiente los lee de ahí: no hay rutas hardcodeadas entre pasos, y por eso se
puede retomar una corrida con `--desde`.

## Estados de cumplimiento

| Estado | Significado |
|--------|-------------|
| `ok` | La regla aplica y se cumple |
| `missing` | La regla aplica y **no** se cumple → el paso 4 la adecua |
| `not_applicable` | La regla no aplica a este producto |
| `not_evaluable` | Aplica pero no se puede verificar con la información del prospecto |

## Estructura del repo

```
run_pipeline.py            punto de entrada por terminal
run_web.py                 punto de entrada de la web
src/                       todo el código importable (se agrega al sys.path)
  core/                    configuración, consola coloreada, contexto de corrida
    config.py              todo el .env en un objeto Settings
    console.py             logging coloreado, banners, preguntas y bus de eventos
    run_context.py         <fecha-hora>, carpetas y manifest
    usage.py               contabilidad de tokens y costo por corrida
    retry.py               reintentos de la capa de aplicación
    cancellation.py        cancelación cooperativa
  agents/                  agentes LLM (Responses API, stateless)
    llm_client.py          wrapper de client.responses.create
    rules_generator.py     paso 1
    disposition_classifier.py / compliance_checker.py   paso 3
    prospect_adequator.py  paso 4
    prompts/*.txt          system prompts versionados en git
  etl/                     entrada: texto nativo (PyMuPDF) y OCR local (DeepSeek-OCR)
  reporting/               salida: informe en JSON, Markdown, HTML y PDF
  pipeline/                un módulo por paso, orquestador y grafo LangGraph del paso 3
  web/                     API FastAPI + las páginas de la interfaz web
disposiciones/
  disposiciones-originales/  las normas tal cual vinieron
    fuentes/               PDF y DOCX originales (documentos públicos de ANMAT)
    markdown/              texto de las normas ya extraído
    reglas-base/           reglas JSON de referencia (semilla del paso 1)
  disposiciones-explotadas/  reglas extraídas por corrida (git-ignored)
ejemplos/prospectos/       prospecto de ejemplo sintético
scripts/                   utilidades (regenerar PDF, deploy)
tests/smoke_pipeline.py    prueba de humo sin llamar a la API
corridas/<fecha-hora>/     salidas de cada corrida (git-ignored)
```

## Configuración

Todo se configura por `.env` (ver [`.env.example`](.env.example)). Lo más usado:

| Variable | Para qué |
|----------|----------|
| `OPENAI_API_KEY` | Credencial (obligatoria) |
| `RULES_GENERATOR_MODEL` | Modelo del paso 1 |
| `CLASSIFIER_MODEL` / `CHECKER_MODEL` | Modelos del paso 3 |
| `ADEQUATOR_ONESHOT_MODEL` | Modelo del paso 4 |
| `MAX_RETRIES` | Reintentos del SDK ante 429 |
| `LLM_ATTEMPTS` | Intentos del pipeline por clasificación y por regla |
| `BASE_RULES_DIR` | Reglas usadas cuando se reutilizan |
| `OCR_MODEL_DIR` / `OCR_DEVICE` | OCR local |

## Notas

- **Idioma.** El código, los comentarios y la documentación de referencia están en inglés.
  Los flags de la CLI, la salida por consola y los prompts del LLM están en español
  a propósito: el dominio es la regulación farmacéutica argentina. La web es bilingüe y
  arranca en inglés (`src/web/static/i18n.js`).
- **Datos de ejemplo.** Las normas de `disposiciones/` son documentos públicos de ANMAT. El
  prospecto de `ejemplos/prospectos/` es sintético, con datos inventados. En este repositorio
  no hay ningún documento real de un cliente.
- **No es una autoridad regulatoria.** Es una herramienta de asistencia: su salida es un
  borrador para que revise un profesional, nunca un reemplazo de la aprobación regulatoria.
- Los agentes usan la **Responses API** (stateless): los prompts viven en
  `src/agents/prompts/*.txt` y no hay `assistant_id` ni threads.
- Documentación de arquitectura: [`documentacion/ARQUITECTURA.md`](documentacion/ARQUITECTURA.md).

## Licencia

[MIT](LICENSE) © Marco Ustarroz
