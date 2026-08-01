/**
 * Shared i18n for both web views (terminal and light).
 *
 * English is the default; the choice is stored in localStorage so it survives a
 * reload. Static text is marked up in the HTML with `data-i18n` (textContent),
 * `data-i18n-title` (title attribute) and `data-i18n-html` (innerHTML); the
 * dynamic parts call `t()` and are re-rendered by each page's `applyLang()`.
 *
 * Note: the live console/log lines are streamed from the Python backend, which
 * speaks Spanish. Switching language translates the interface, not the
 * pipeline's own log output.
 */
const I18N = {
  en: {
    tabTitle: "Leaflet compliance · ANMAT",
    tabTitleMin: "Leaflets · ANMAT",
    appTitle: "Leaflet compliance analyzer & fixer",
    appSubtitle: "ANMAT · 4-step pipeline",
    minTitle: "Leaflets",
    minLede: "Compliance analysis and adequation · ANMAT",

    viewLight: "light view ↗",
    viewLightTitle: "Light, minimalist view",
    viewTerminal: "Switch to the terminal view →",
    viewLab: "laboratory ↗",
    viewLabTitle: "Try one isolated step with a custom prompt and params",

    stampNone: "no run",
    stampStarting: "starting…",
    statusIdle: "idle",
    statusRunning: "running",
    statusDone: "completed",
    statusCancelled: "cancelled",
    statusFailed: "failed",

    formTitle: "New run",
    fieldExample: "Sample leaflet",
    fieldLeaflet: "Leaflet",
    optChoose: "— choose —",
    optChooseExample: "Choose an example",
    fieldUpload: "…or upload one (PDF, DOCX, MD, TXT)",
    uploadFile: "Upload file",
    fileTypes: "PDF, DOCX, MD or TXT",
    remove: "Remove",
    fieldRules: "Rules folder",
    fieldRulesShort: "Rules",
    // The rules are not a choice: the form only states which ones are in use.
    rulesFixed: "Using ANMAT-Argentina rules",

    howItWorks: "how it works",
    auditStep6: "Run Step 6 - Expert Agent Audit",
    auditNote:
      "A second opinion from {model}, a model from a different provider: it " +
      "reviews the adequation and flags which rules need human judgement. It is " +
      "the run's most expensive call and it only runs if you ask for it.",
    auditUnavailable:
      "Needs ANTHROPIC_API_KEY in the .env; without it, it stays disabled.",
    btnRun: "Run pipeline",
    btnRunMin: "Analyze",
    btnRunning: "Running…",
    btnAnalyzing: "Analyzing…",
    btnGeneratingRules: "Generating rules…",
    btnStop: "■ Stop run",
    btnStopMin: "Stop",
    btnCancelling: "Cancelling…",
    btnGenerateRules: "Generate rules",

    hintDefault:
      "Step 3 makes one model call per disposition and one per rule: a full run " +
      "takes several minutes.",
    hintNoRules:
      "No rules yet: step 1 will generate them from {n} dispositions. It happens " +
      "once and they are reused afterwards.",
    hintNoDocs:
      "No rules and no dispositions to generate them from: drop the documents in {folder}.",
    noticeRulesTitle: "The rules have to be generated first",
    noticeRulesBody:
      "Rules are extracted from the dispositions once and reused from then on. " +
      "There are {n} dispositions in the sources folder; it takes a few minutes.",
    noticeRulesNoDocs:
      "No rules and no documents to generate them from: drop the dispositions' " +
      "PDF, DOCX or MD files in {folder} and reload the page.",

    consoleTitle: "Console",
    consoleWaiting: "waiting for a run…",
    processTitle: "Process",
    logTitle: "Log",
    usageTitle: "Usage",
    resultsTitle: "Results",

    viewerDownload: "Download",
    viewerClose: "Close ✕",
    viewerCloseMin: "Close",
    viewerTitle: "Compliance report PDF",

    stepWord: "STEP",
    stepWordCap: "Step",
    step0Title: "Context",
    step0Note: "folders and manifest",
    step1Title: "Rules",
    step1Note: "dispositions → JSON",
    step2Title: "Leaflet",
    step2Note: "→ clean text",
    step3Title: "Compliance",
    step3Note: "classify + check",
    step4Title: "Adequation",
    step4Note: "corrected leaflet",
    step5Title: "Verification",
    step5Note: "optional · Claude",

    resultReport: "Compliance report",
    resultAdequated: "Adequated leaflet",
    resultVerification: "Final verification",
    ctaViewer: "View in viewer ▸",
    ctaDownload: "Download ▾",
    ctaOpen: "Open ▸",
    ctaView: "View",
    ctaDownloadPlain: "Download",
    othersToggle: "other {n} files",
    othersMin: "Other {n} files",

    usageCost: "run cost",
    usageSaved: "saved by cache ({p}% less)",
    usageInput: "input tokens",
    usageCachedIn: "input from cache",
    usageOutput: "output tokens",
    usageCalls: "model calls",
    callOne: "call",
    callMany: "calls",
    modelCallOne: "model call",
    modelCallMany: "model calls",
    cacheWord: "cache",
    usageFoot: "{in} input tokens · {out} output",
    usageFootSaved: " · {money} saved by cache",
    noData: "n/a",

    closeOk: "── run finished ──",
    closeCancelled: "── run cancelled ──",
    closeFailed: "── the run failed ──",
    closeOkMin: "Done.",
    closeCancelledMin: "Run cancelled.",
    closeFailedMin: "The run failed.",
    closeFailedMinDetail: "The run failed: {error}",

    alertPick: "Choose a sample leaflet or upload a file.",
    retrying: "retrying",
  },

  es: {
    tabTitle: "Analizador de prospectos · ANMAT",
    tabTitleMin: "Prospectos · ANMAT",
    appTitle: "Analizador y adecuador de prospectos",
    appSubtitle: "ANMAT · pipeline de 4 pasos",
    minTitle: "Prospectos",
    minLede: "Análisis de cumplimiento y adecuación · ANMAT",

    viewLight: "vista clara ↗",
    viewLightTitle: "Vista clara y minimalista",
    viewTerminal: "Cambiar a la vista terminal →",
    viewLab: "laboratorio ↗",
    viewLabTitle: "Probar un paso suelto con prompt y parámetros a medida",

    stampNone: "sin corrida",
    stampStarting: "iniciando…",
    statusIdle: "en espera",
    statusRunning: "corriendo",
    statusDone: "completada",
    statusCancelled: "cancelada",
    statusFailed: "falló",

    formTitle: "Nueva corrida",
    fieldExample: "Prospecto de ejemplo",
    fieldLeaflet: "Prospecto",
    optChoose: "— elegir —",
    optChooseExample: "Elegir un ejemplo",
    fieldUpload: "…o subir uno (PDF, DOCX, MD, TXT)",
    uploadFile: "Subir archivo",
    fileTypes: "PDF, DOCX, MD o TXT",
    remove: "Quitar",
    fieldRules: "Carpeta de reglas",
    fieldRulesShort: "Reglas",
    rulesFixed: "Usando reglas ANMAT-Argentina",

    howItWorks: "cómo funciona",
    auditStep6: "Ejecutar Paso 6 - Auditoría del Agente Experto",
    auditNote:
      "Una segunda opinión de {model}, un modelo de otro proveedor: revisa la " +
      "adecuación y marca qué reglas necesitan criterio humano. Es la llamada " +
      "más cara de la corrida y sólo corre si la pedís.",
    auditUnavailable:
      "Requiere ANTHROPIC_API_KEY en el .env; sin eso queda deshabilitada.",
    btnRun: "Ejecutar pipeline",
    btnRunMin: "Analizar",
    btnRunning: "Corriendo…",
    btnAnalyzing: "Analizando…",
    btnGeneratingRules: "Generando reglas…",
    btnStop: "■ Detener corrida",
    btnStopMin: "Detener",
    btnCancelling: "Cancelando…",
    btnGenerateRules: "Generar reglas",

    hintDefault:
      "El paso 3 hace una llamada al modelo por disposición y otra por regla: " +
      "una corrida completa tarda varios minutos.",
    hintNoRules:
      "No hay reglas todavía: el paso 1 las va a generar a partir de {n} " +
      "disposiciones. Se hace una vez y se reutilizan.",
    hintNoDocs:
      "No hay reglas ni disposiciones para generarlas: dejá los documentos en {folder}.",
    noticeRulesTitle: "Primero hay que generar las reglas",
    noticeRulesBody:
      "Las reglas se extraen de las disposiciones una sola vez y después se " +
      "reutilizan. Hay {n} disposiciones en la carpeta de fuentes; tarda unos minutos.",
    noticeRulesNoDocs:
      "No hay reglas ni documentos para generarlas: dejá los PDF, DOCX o MD de " +
      "las disposiciones en {folder} y recargá la página.",

    consoleTitle: "Consola",
    consoleWaiting: "esperando una corrida…",
    processTitle: "Proceso",
    logTitle: "Registro",
    usageTitle: "Consumo",
    resultsTitle: "Resultados",

    viewerDownload: "Descargar",
    viewerClose: "Cerrar ✕",
    viewerCloseMin: "Cerrar",
    viewerTitle: "Informe PDF",

    stepWord: "PASO",
    stepWordCap: "Paso",
    step0Title: "Contexto",
    step0Note: "carpetas y manifest",
    step1Title: "Reglas",
    step1Note: "disposiciones → JSON",
    step2Title: "Prospecto",
    step2Note: "→ texto limpio",
    step3Title: "Cumplimiento",
    step3Note: "clasificar + verificar",
    step4Title: "Adecuación",
    step4Note: "prospecto corregido",
    step5Title: "Verificación",
    step5Note: "opcional · Claude",

    resultReport: "Informe de cumplimiento",
    resultAdequated: "Prospecto adecuado",
    resultVerification: "Verificación final",
    ctaViewer: "Ver en el visor ▸",
    ctaDownload: "Descargar ▾",
    ctaOpen: "Abrir ▸",
    ctaView: "Ver",
    ctaDownloadPlain: "Descargar",
    othersToggle: "otros {n} archivos",
    othersMin: "Otros {n} archivos",

    usageCost: "costo de la corrida",
    usageSaved: "ahorrado por caché ({p}% menos)",
    usageInput: "tokens de entrada",
    usageCachedIn: "entrada desde caché",
    usageOutput: "tokens de salida",
    usageCalls: "llamadas al modelo",
    callOne: "llamada",
    callMany: "llamadas",
    modelCallOne: "llamada al modelo",
    modelCallMany: "llamadas al modelo",
    cacheWord: "caché",
    usageFoot: "{in} tokens de entrada · {out} de salida",
    usageFootSaved: " · {money} ahorrados por caché",
    noData: "s/d",

    closeOk: "── corrida finalizada ──",
    closeCancelled: "── corrida cancelada ──",
    closeFailed: "── la corrida falló ──",
    closeOkMin: "Listo.",
    closeCancelledMin: "Corrida cancelada.",
    closeFailedMin: "La corrida falló.",
    closeFailedMinDetail: "La corrida falló: {error}",

    alertPick: "Elegí un prospecto de ejemplo o subí un archivo.",
    retrying: "reintentando",
  },
};

const LANG_KEY = "leaflet-lang";
const LANG_DEFAULT = "en";

let lang = LANG_DEFAULT;
try {
  const saved = localStorage.getItem(LANG_KEY);
  if (saved && I18N[saved]) lang = saved;
} catch (_) {
  // Private browsing with storage disabled: English it is.
}

/** Current language code ("en" | "es"). */
const currentLang = () => lang;

/**
 * Switch language and remember the choice.
 *
 * The flag switch does this inline, but the onboarding picks the language before
 * the switch even exists, so it needs a way in.
 *
 * @returns {boolean} whether the language actually changed.
 */
function setLang(code) {
  if (!I18N[code] || code === lang) return false;
  lang = code;
  try {
    localStorage.setItem(LANG_KEY, lang);
  } catch (_) {
    // Nothing to do: the choice just will not survive the reload.
  }
  return true;
}

/** Locale for number formatting, so figures follow the chosen language. */
const numLocale = () => (lang === "es" ? "es-AR" : "en-US");

/** Translate `key`, replacing `{placeholders}` with `vars`. */
function t(key, vars) {
  let text = (I18N[lang] && I18N[lang][key]) ?? I18N[LANG_DEFAULT][key] ?? key;
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      text = text.replaceAll(`{${name}}`, String(value));
    }
  }
  return text;
}

/** Fill every element marked up with data-i18n / -html / -title. */
function applyStaticText() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-html]").forEach(el => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  document.querySelectorAll("[data-i18n-title]").forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
}

/**
 * Render the flag switch and wire it up.
 *
 * @param {string} containerId  id of the element that holds the flags
 * @param {Function} onChange   called after the language changes, to re-render
 */
function initLangSwitch(containerId, onChange) {
  const box = document.getElementById(containerId);
  if (!box) return;

  const FLAGS = [
    { code: "en", flag: "🇬🇧", label: "English" },
    { code: "es", flag: "🇪🇸", label: "Español" },
  ];

  box.innerHTML = FLAGS.map(f =>
    `<button type="button" class="flag" data-lang="${f.code}" title="${f.label}"
             aria-label="${f.label}">${f.flag}</button>`).join("");

  const mark = () => box.querySelectorAll(".flag").forEach(b => {
    b.dataset.active = String(b.dataset.lang === lang);
  });

  box.querySelectorAll(".flag").forEach(button => {
    button.addEventListener("click", () => {
      if (button.dataset.lang === lang) return;
      lang = button.dataset.lang;
      try {
        localStorage.setItem(LANG_KEY, lang);
      } catch (_) {
        // Nothing to do: the choice just will not survive the reload.
      }
      mark();
      onChange();
    });
  });

  mark();
}
