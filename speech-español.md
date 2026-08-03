# Speech — cómo implementé agenteProspectos y por qué

> Guion para explicarle el proyecto a un empleador. Guiá vos el orden: pitch →
> arquitectura → una decisión técnica jugosa → roadmap para cerrar.

## Nota previa: LangGraph, no LangChain

El proyecto **no usa LangChain**. Usa **LangGraph**, y solo en el paso 3. Son
cosas distintas y decirlo bien suma: LangChain es la librería de cadenas y
abstracciones sobre LLMs (que este proyecto deliberadamente *no* usa — las
llamadas van al SDK de OpenAI directo); LangGraph es el motor de grafos de
estado del mismo equipo. Decir "tengo un LangChain" y después mostrar
`client.responses.create` a pelo queda pagando.

---

## 1. El pitch (30 segundos)

> "Construí un sistema que audita prospectos de medicamentos contra la normativa
> de ANMAT. Le das el PDF de un prospecto y te devuelve dos cosas: un informe de
> cumplimiento regla por regla, y una versión reescrita del prospecto que corrige
> lo que estaba mal, con cada cambio marcado y referenciado a la norma que lo
> obliga.
>
> El problema de fondo es que esa revisión hoy la hace una persona leyendo
> disposiciones y tildando ítems a mano. Lo que automaticé no es 'resumir con
> IA': es convertir la normativa en reglas verificables y evaluarlas una por una
> de forma trazable."

## 2. La arquitectura, y por qué esa

> "Lo pensé como un **pipeline de pasos discretos**, no como un agente
> conversacional. Son cinco pasos: normativa → reglas JSON, prospecto → texto
> limpio, verificación de cumplimiento, adecuación del prospecto, y una
> verificación final opcional.
>
> La decisión de diseño central es que **ningún paso conoce las rutas del otro**.
> Cada corrida tiene un `manifest.json` donde cada paso registra los artefactos
> que produjo, y el siguiente los pide de ahí. Eso me dio gratis dos cosas que
> necesitaba: poder **retomar una corrida desde el paso N** sin re-pagar los
> anteriores, y que cada corrida quede **autocontenida y reproducible** — si
> mañana cambian las reglas base, la corrida de la semana pasada se sigue
> explicando a sí misma.
>
> Elegí pipeline y no agente autónomo por una razón de dominio: esto es
> compliance. Necesito que la salida sea auditable y determinista en su
> estructura. Un agente que decide solo qué herramienta llamar es más
> impresionante en una demo y mucho peor para justificar por qué dijo que una
> regla no se cumple."

## 3. Por qué cada pieza

Este es el bloque donde más aprietan. Respuestas cortas y con el trade-off
explícito.

### LangGraph (paso 3)

> "El paso 3 es un grafo de cuatro nodos: cargar prospecto → clasificar qué
> disposiciones aplican → verificar regla por regla → generar informe. Usé
> LangGraph por el estado tipado — hay un `TypedDict` que define exactamente qué
> circula entre nodos — y porque el paso 3 es el que va a crecer: la mejora que
> tengo pendiente es paralelizar las llamadas de verificación, y ahí el grafo me
> da dónde apoyarme.
>
> Y soy honesto: para un flujo hoy lineal, LangGraph es discutible; se podría
> resolver con cuatro funciones encadenadas. Lo que me compró fue disciplina de
> estado y un lugar donde meter ramas condicionales sin reescribir el paso. Por
> eso está **solo ahí** y no en todo el proyecto."

*Si preguntan "¿y por qué no LangChain en el resto?":*

> "Porque no lo necesitaba. Mis agentes son una llamada con un system prompt y un
> JSON de vuelta. Meter una capa de abstracción encima del SDK me habría
> escondido justo lo que quería controlar: el orden del input para el caché, los
> timeouts, los reintentos. Prefiero una dependencia menos y ver la llamada."

### OpenAI Responses API, stateless

> "Todos los agentes son stateless: sin threads, sin `assistant_id`. Cada llamada
> es autocontenida. Los system prompts viven en archivos `.txt` versionados en
> git, no embebidos en el código — así un cambio de prompt es un diff revisable,
> que en un sistema donde el prompt *es* la lógica de negocio me parece
> innegociable."

### Anthropic / Claude en el paso 5  ← el mejor punto, destacarlo

> "Los pasos 1 a 4 usan OpenAI. El paso 5 usa Claude a propósito: es un
> **verificador cruzado de otro proveedor**. La idea es que un modelo distinto
> tiene más chances de ver lo que el primero dio por bueno — si uso el mismo
> modelo para hacer y para revisar, hereda sus propios puntos ciegos.
>
> Y no responde 'está bien o está mal': responde dos preguntas. Por cada regla
> que el paso 4 intentó resolver, si quedó `resuelta`, `parcial`, `no_resuelta` o
> `introduce_error`. Y por separado, **qué necesita una persona** — reglas
> ambiguas, o que dependen de un dato del expediente, o que son criterio
> profesional. Ese segundo output es el que hace que el sistema sea usable en la
> vida real: no pretende reemplazar al que firma, le arma la lista de decisiones
> que le tocan a él."

### PyMuPDF + OCR local

> "Los PDFs con capa de texto se leen con PyMuPDF, instantáneo y sin pérdida. La
> caída a OCR es automática: si el PDF rinde menos de 120 caracteres por página,
> asumo que está escaneado y recién ahí levanto el modelo de OCR local. El OCR se
> importa de forma perezosa porque cargar torch tarda segundos, y cachea por
> página, así que un PDF que falló a la mitad retoma donde quedó. Es local y no un
> servicio en la nube porque son documentos regulatorios de clientes."

### FastAPI + la web

> "La web no es un sistema aparte: se cuelga de los mismos eventos que emite la
> consola del pipeline, así que la terminal y el navegador muestran exactamente
> lo mismo — una sola fuente de verdad. La corrida se ve en vivo, y si recargás la
> página en el medio se reengancha, porque cada corrida guarda su historial de
> eventos y un cliente que llega tarde recibe todo y sigue desde ahí."

## 4. Las decisiones que muestran criterio de ingeniero

Estas diferencian de alguien que pegó un prompt en un script. Elegir dos o tres
según el interlocutor.

### Una llamada por regla, y por qué acepto pagar más

> "En el paso 3 hago una llamada al modelo por cada regla, en vez de mandar las 80
> juntas. Es más caro en tokens y lo sé. Lo elegí porque no hay degradación de
> contexto: la regla 80 se evalúa con la misma calidad que la 1. En compliance,
> una regla evaluada con desgano porque quedó al final del prompt es un falso
> 'cumple' — el ahorro no valía eso."

### El caché de prompts, que es cómo recupero ese costo

> "Ese costo lo compenso con el caché de prompts. El prospecto va **siempre
> primero** en el input y lo que varía — la disposición, la regla — al final,
> porque el caché exige prefijo idéntico. Además fijo un `prompt_cache_key` para
> que el ruteo no me mande la llamada a otra máquina y pierda el caché. El
> prefijo cacheado se factura a una fracción, así que el patrón 'una llamada por
> regla' pasa de carísimo a razonable. Y no lo estimo: la API informa cuántos
> tokens salieron de caché y el sistema reporta al final de cada corrida cuántas
> llamadas hizo, cuánto costó y cuánto ahorró."

### Adecuación en una sola llamada — la decisión opuesta, y por qué

> "El paso 4 es al revés: una sola llamada con todas las reglas incumplidas
> juntas. Porque si reescribiera el prospecto una regla por vez, cada llamada no
> vería los cambios de las otras y se pisarían entre sí. Verificar es
> paralelizable; redactar un documento coherente, no."

### Reintentos y timeouts diferenciados

> "Puse un techo duro de 5 minutos por llamada, y las llamadas largas —
> adecuación, generación de reglas — tienen **un solo reintento** en vez de cinco.
> El motivo es empírico: con cinco reintentos, un timeout de 5 minutos se
> convertía en media hora de espera antes de fallar. En llamadas que ya tardan
> minutos, insistir sale carísimo en tiempo y el segundo intento rara vez arregla
> lo que falló en el primero."

### Cancelación cooperativa

> "El botón de detener no mata el hilo, porque en Python no se puede matar un
> hilo. Es cooperativa: se marca el pedido y el pipeline chequea entre una llamada
> al modelo y la siguiente. Puse los puntos de chequeo justo donde el pipeline
> pasa el tiempo. Los pasos que ya terminaron dejan sus artefactos."

### Fallar fuerte, a propósito

> "Si una disposición se clasifica como aplicable pero no se puede verificar, la
> corrida **falla**, no sigue. No hay try/except ahí y es deliberado: un informe de
> cumplimiento parcial que parece completo es peor que ningún informe."

### El laboratorio — buen cierre: muestra que pensé el proceso, no solo el producto

> "Al final me armé un laboratorio dentro de la web. El problema era que afinar un
> prompt costaba correr el pipeline entero: 15 minutos y tokens. El laboratorio
> ejecuta **un paso solo**, con las entradas precargadas de los fixtures de tests,
> y con el modelo, los parámetros y el system prompt editables en la página — vale
> para esa ejecución y no toca el repo. Play, y te muestra la salida, lo que
> tardó y lo que costó. Bajó el ciclo de iteración de minutos a segundos."

## 5. Testing

*Te van a preguntar: "¿y cómo lo probás si es no determinista?"*

> "Separé en dos. El **smoke test** corre el pipeline entero con las respuestas del
> modelo simuladas: valida el encadenamiento vía manifest, el parseo de cada
> agente, los cuatro formatos del informe y las marcas del DOCX. Son 55 chequeos y
> no gasta un token — puede correr en CI.
>
> Las **pruebas de integración** sí llaman a las APIs reales, pero con un caso
> mínimo: una disposición de dos reglas y un prospecto de 20 líneas, unos 5
> centavos de dólar. Y cada paso arranca de fixtures fijos, así que puedo probar
> el paso 4 sin correr el 3.
>
> Lo que no testeo es la *calidad* del juicio del modelo con un assert, porque no
> es determinista. Para eso está el paso 5 y está el humano. Lo que sí garantizo
> con tests es que la infraestructura alrededor del modelo no se rompa."

## 6. Lo que haría distinto — cerrar con esto

*La madurez vende más que la demo.*

> "Tengo el roadmap escrito y priorizado. Lo de mayor impacto es **paralelizar el
> paso 3**: las llamadas son independientes entre sí y hoy van secuenciales; con un
> pool de 5-10 workers, un análisis completo baja de ~15 minutos a ~2. Después:
> pasar a **structured outputs** en vez de forzar modo JSON y sanear la salida a
> mano, **cachear la clasificación** por hash del prospecto para no re-pagar una
> reejecución, y usar la **Batch API** en el paso 3, que es 50% más barata y el
> análisis no es interactivo.
>
> Si arrancara de nuevo, la Batch API y los structured outputs los ponía desde el
> día uno."

---

## Preguntas filosas y cómo responderlas

| Te preguntan | Respondé |
|---|---|
| **"¿Por qué LangGraph y no funciones?"** | No lo defiendas de más. "Estado tipado y lugar para crecer hacia paralelismo y ramas. Para el flujo lineal de hoy, admito que es discutible — por eso está solo en el paso 3." La honestidad acá hace ganar. |
| **"¿Y si el modelo alucina una regla?"** | "Por eso las reglas no las inventa en runtime: se generan en el paso 1 en dos pasadas — borrador y auditoría contra el texto original, descartando lo que no está respaldado — y quedan versionadas en JSON. En la verificación, el modelo no genera reglas, solo evalúa contra reglas fijas y devuelve los fragmentos del prospecto que usó como evidencia." |
| **"¿Esto reemplaza al regulatory affairs?"** | "No, y está diseñado para no hacerlo. El paso 5 produce explícitamente la lista de lo que necesita criterio humano. Le saca el trabajo mecánico y le deja las decisiones." |
| **"¿Cuánto cuesta correrlo?"** | Número concreto: "una corrida real de ACICLOVIR, el paso 5 completo — 19 adecuaciones revisadas, 11 reglas para intervención humana, 6 riesgos — costó 11 centavos de dólar." Los números concretos matan la duda sobre si lo corriste de verdad. |
| **"¿Por qué dos proveedores? Es más complejidad."** | "Es la complejidad que compra independencia de criterio. Y como el paso 5 está apagado por defecto y es opcional, el sistema funciona entero sin la segunda API key — no es un acoplamiento, es un opt-in." |

---

## Dos consejos de entrega

1. **Guiá vos el orden**: pitch → arquitectura → una decisión técnica jugosa →
   roadmap para cerrar.
2. **Decí siempre el trade-off que aceptaste.** "Elegí X aunque cuesta Y, porque
   Z" suena a ingeniero; "usé X porque es lo mejor" suena a tutorial.
