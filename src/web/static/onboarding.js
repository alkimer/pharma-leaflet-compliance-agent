/**
 * First-run onboarding, shared by both views.
 *
 * Whoever lands here for the first time has no idea what the page does. Three
 * screens, in order:
 *
 *   1. language — two flags, before any text exists to be read
 *   2. what the system does — in the language just picked
 *   3. how to get the result — wait and watch it live, or leave an email
 *
 * Then the overlay steps aside and the pipeline UI is there, already in the right
 * language and holding the address, if one was given.
 *
 * The component brings its own markup and CSS, and paints itself with the
 * `--ob-*` custom properties each page defines, so the same wizard looks like the
 * terminal view in one and like the light view in the other.
 *
 * It shows up once (localStorage) and can be reopened from any element carrying
 * `data-onboarding-open`.
 */

const OB_KEY = "leaflet-onboarding";

const OB_TEXTS = {
  es: {
    langTitle: "Elegí tu idioma",
    whatTitle: "Qué hace este sistema",
    whatBody: [
      "Subí el prospecto médico que quieras, o usá uno de los ejemplos.",
      "El sistema lo analiza según las disposiciones ANMAT (Argentina) y adecúa el prospecto para que cumpla con ellas.",
      "Es un análisis riguroso y profesional, sin alucinaciones: cada veredicto cita la evidencia encontrada en el texto y el artículo que la exige, y nunca inventa información clínica.",
    ],
    timeTitle: "Puede tardar varios minutos",
    timeBody:
      "Podés esperar y ver el análisis en vivo, paso por paso. O dejarnos tu email y te mandamos el prospecto adecuado y el informe de cumplimiento cuando termine.",
    emailLabel: "Tu email (opcional)",
    emailPlaceholder: "nombre@correo.com",
    emailInvalid: "Esa dirección no parece válida.",
    emailOff: "El envío por correo no está configurado en este servidor, así que por ahora hay que esperar en la página.",
    next: "Continuar",
    back: "Volver",
    startLive: "Ver en vivo",
    startMail: "Ver en vivo y recibirlo por mail",
    langNote: "El informe sale en el idioma que elijas. El prospecto adecuado se entrega siempre en español: las disposiciones exigen frases textuales en ese idioma.",
  },
  en: {
    langTitle: "Choose your language",
    whatTitle: "What this system does",
    whatBody: [
      "Upload the medicine leaflet you want, or use one of the samples.",
      "The system analyses it against the ANMAT (Argentina) dispositions and adequates the leaflet so that it complies with them.",
      "It is a rigorous, professional analysis with no hallucinations: every verdict cites the evidence found in the text and the article that demands it, and it never invents clinical information.",
    ],
    timeTitle: "This can take several minutes",
    timeBody:
      "You can wait and watch the analysis live, step by step. Or leave your email and we will send you the adequated leaflet and the compliance report when it is done.",
    emailLabel: "Your email (optional)",
    emailPlaceholder: "name@email.com",
    emailInvalid: "That address does not look valid.",
    emailOff: "Emailing is not configured on this server, so for now you will need to wait on the page.",
    next: "Continue",
    back: "Back",
    startLive: "Watch it live",
    startMail: "Watch it live and email it to me",
    startTitle: "Ready",
    langNote: "The report comes out in the language you pick. The adequated leaflet is always delivered in Spanish: the dispositions require literal Spanish wording.",
  },
};

const OB_CSS = `
.ob-backdrop {
  position: fixed; inset: 0; z-index: 9000;
  display: flex; align-items: center; justify-content: center; padding: 20px;
  background: var(--ob-backdrop, rgba(4, 8, 11, .82));
  backdrop-filter: blur(3px);
  opacity: 0; transition: opacity .22s ease;
}
.ob-backdrop[data-open="true"] { opacity: 1; }
.ob-card {
  width: 100%; max-width: 560px;
  padding: 30px 32px 26px;
  background: var(--ob-panel, #0e151b);
  color: var(--ob-text, #e3f1ea);
  border: 1px solid var(--ob-border, rgba(125, 214, 178, .3));
  border-radius: var(--ob-radius, 14px);
  font-family: var(--ob-font, inherit);
  box-shadow: 0 24px 60px rgba(0, 0, 0, .45);
  transform: translateY(8px) scale(.99);
  transition: transform .22s ease;
}
.ob-backdrop[data-open="true"] .ob-card { transform: none; }
@media (max-width: 560px) { .ob-card { padding: 24px 20px 20px; } }

.ob-dots { display: flex; gap: 6px; margin-bottom: 22px; }
.ob-dot {
  width: 26px; height: 3px; border-radius: 2px;
  background: var(--ob-border, rgba(125, 214, 178, .3));
  transition: background .2s;
}
.ob-dot[data-on="true"] { background: var(--ob-accent, #5cffb0); }

.ob-title {
  /* Every property is spelled out: the host page styles its own h2 (uppercase in
     the light view, letter-spaced in the terminal one) and the wizard must not
     inherit that. */
  margin: 0 0 14px; font-size: 19px; line-height: 1.3; font-weight: 600;
  text-transform: none; letter-spacing: normal;
  color: var(--ob-title, var(--ob-text, #e3f1ea));
}
.ob-p { margin: 0 0 11px; font-size: 14.5px; line-height: 1.62; color: var(--ob-muted, #93aca3); }
.ob-p:last-of-type { margin-bottom: 0; }
.ob-p strong { color: var(--ob-text, #e3f1ea); font-weight: 600; }

.ob-flags { display: flex; gap: 14px; margin: 4px 0 18px; }
.ob-flag {
  flex: 1; display: flex; flex-direction: column; align-items: center; gap: 10px;
  padding: 22px 12px; cursor: pointer;
  font-family: inherit; font-size: 14px; color: var(--ob-text, #e3f1ea);
  background: var(--ob-surface, rgba(255, 255, 255, .03));
  border: 1px solid var(--ob-border, rgba(125, 214, 178, .3));
  border-radius: calc(var(--ob-radius, 14px) - 4px);
  transition: border-color .18s, transform .18s, background .18s;
}
.ob-flag:hover { border-color: var(--ob-accent, #5cffb0); transform: translateY(-2px); }
.ob-flag .ob-flag-emoji { font-size: 34px; line-height: 1; }

.ob-field { margin: 18px 0 6px; }
.ob-label { display: block; margin-bottom: 7px; font-size: 12.5px; color: var(--ob-muted, #93aca3); }
.ob-input {
  width: 100%; padding: 11px 13px;
  font-family: inherit; font-size: 15px;
  color: var(--ob-text, #e3f1ea);
  background: var(--ob-surface, rgba(255, 255, 255, .03));
  border: 1px solid var(--ob-border, rgba(125, 214, 178, .3));
  border-radius: calc(var(--ob-radius, 14px) - 6px);
}
.ob-input:focus { outline: none; border-color: var(--ob-accent, #5cffb0); }
.ob-error { margin-top: 7px; font-size: 12.5px; color: var(--ob-error, #ff7280); min-height: 16px; }

.ob-actions { display: flex; align-items: center; gap: 12px; margin-top: 22px; }
.ob-spacer { flex: 1; }
.ob-btn {
  padding: 11px 22px; cursor: pointer;
  font-family: inherit; font-size: 14px; font-weight: 600;
  color: var(--ob-accent-text, #06110c);
  background: var(--ob-accent, #5cffb0);
  border: 0; border-radius: 999px;
  transition: filter .18s;
}
.ob-btn:hover { filter: brightness(1.08); }
.ob-btn-ghost {
  padding: 11px 6px; cursor: pointer;
  font-family: inherit; font-size: 13.5px;
  color: var(--ob-muted, #93aca3);
  background: none; border: 0;
}
.ob-btn-ghost:hover { color: var(--ob-text, #e3f1ea); }
.ob-note { margin-top: 16px; font-size: 12px; line-height: 1.55; color: var(--ob-muted, #93aca3); opacity: .85; }
`;

const Onboarding = (() => {
  let emailChosen = "";
  let emailEnabled = true;
  let onLanguage = () => {};
  let onFinish = () => {};
  let step = 1;
  let backdrop = null;

  // ---- Persistence -------------------------------------------------------
  // Only "already seen" and the address are remembered. The language lives where
  // it always did, in i18n.js's own key.
  function load() {
    try {
      return JSON.parse(localStorage.getItem(OB_KEY) || "{}") || {};
    } catch (_) {
      return {};
    }
  }

  function save(data) {
    try {
      localStorage.setItem(OB_KEY, JSON.stringify(data));
    } catch (_) {
      // Storage disabled: the wizard will simply show up again next time.
    }
  }

  const texts = () => OB_TEXTS[typeof currentLang === "function" ? currentLang() : "en"] || OB_TEXTS.en;

  // A deliberately loose check, the same idea as the backend's: catch the typo,
  // do not argue with valid-but-unusual addresses.
  const looksLikeEmail = (value) => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value.trim());

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ---- Rendering ---------------------------------------------------------

  function mount() {
    if (!document.getElementById("ob-style")) {
      const style = document.createElement("style");
      style.id = "ob-style";
      style.textContent = OB_CSS;
      document.head.appendChild(style);
    }
    backdrop = document.createElement("div");
    backdrop.className = "ob-backdrop";
    backdrop.setAttribute("role", "dialog");
    backdrop.setAttribute("aria-modal", "true");
    backdrop.innerHTML = '<div class="ob-card" id="ob-card"></div>';
    document.body.appendChild(backdrop);
    // The overlay is modal: the page behind it must not scroll away.
    document.body.style.overflow = "hidden";
    requestAnimationFrame(() => backdrop.setAttribute("data-open", "true"));
  }

  function dots() {
    return '<div class="ob-dots">' +
      [1, 2, 3].map(n => `<span class="ob-dot" data-on="${n <= step}"></span>`).join("") +
      "</div>";
  }

  function render() {
    const card = document.getElementById("ob-card");
    if (!card) return;
    const t = texts();

    if (step === 1) {
      // No copy beyond the heading: whoever is reading has not chosen a language
      // yet, so the flags have to speak for themselves.
      card.innerHTML = dots() +
        `<h2 class="ob-title">${esc(t.langTitle)}</h2>` +
        '<div class="ob-flags">' +
        '<button type="button" class="ob-flag" data-lang="es">' +
        '<span class="ob-flag-emoji">🇪🇸</span><span>Español</span></button>' +
        '<button type="button" class="ob-flag" data-lang="en">' +
        '<span class="ob-flag-emoji">🇬🇧</span><span>English</span></button>' +
        "</div>" +
        `<div class="ob-note">${esc(t.langNote)}</div>`;

      card.querySelectorAll(".ob-flag").forEach(button => {
        button.addEventListener("click", () => {
          onLanguage(button.dataset.lang);
          step = 2;
          render();
        });
      });
      return;
    }

    if (step === 2) {
      card.innerHTML = dots() +
        `<h2 class="ob-title">${esc(t.whatTitle)}</h2>` +
        t.whatBody.map(p => `<p class="ob-p">${esc(p)}</p>`).join("") +
        '<div class="ob-actions">' +
        `<button type="button" class="ob-btn-ghost" data-ob="back">← ${esc(t.back)}</button>` +
        '<span class="ob-spacer"></span>' +
        `<button type="button" class="ob-btn" data-ob="next">${esc(t.next)}</button>` +
        "</div>";

      card.querySelector('[data-ob="back"]').addEventListener("click", () => { step = 1; render(); });
      card.querySelector('[data-ob="next"]').addEventListener("click", () => { step = 3; render(); });
      return;
    }

    card.innerHTML = dots() +
      `<h2 class="ob-title">${esc(t.timeTitle)}</h2>` +
      `<p class="ob-p">${esc(t.timeBody)}</p>` +
      (emailEnabled
        ? '<div class="ob-field">' +
          `<label class="ob-label" for="ob-email">${esc(t.emailLabel)}</label>` +
          `<input class="ob-input" id="ob-email" type="email" inputmode="email"
                  autocomplete="email" placeholder="${esc(t.emailPlaceholder)}"
                  value="${esc(emailChosen)}">` +
          '<div class="ob-error" id="ob-error"></div></div>'
        : `<div class="ob-note">${esc(t.emailOff)}</div>`) +
      '<div class="ob-actions">' +
      `<button type="button" class="ob-btn-ghost" data-ob="back">← ${esc(t.back)}</button>` +
      '<span class="ob-spacer"></span>' +
      `<button type="button" class="ob-btn" data-ob="done">${esc(t.startLive)}</button>` +
      "</div>";

    card.querySelector('[data-ob="back"]').addEventListener("click", () => { step = 2; render(); });

    const input = card.querySelector("#ob-email");
    const done = card.querySelector('[data-ob="done"]');

    if (input) {
      // The button says what will actually happen, and it changes as you type.
      const refresh = () => {
        done.textContent = input.value.trim() ? t.startMail : t.startLive;
      };
      input.addEventListener("input", () => {
        card.querySelector("#ob-error").textContent = "";
        refresh();
      });
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") done.click(); });
      refresh();
    }

    done.addEventListener("click", () => {
      const value = input ? input.value.trim() : "";
      if (value && !looksLikeEmail(value)) {
        card.querySelector("#ob-error").textContent = t.emailInvalid;
        input.focus();
        return;
      }
      emailChosen = value;
      finish();
    });
  }

  function finish() {
    save({ done: true, email: emailChosen });
    if (backdrop) {
      backdrop.setAttribute("data-open", "false");
      const node = backdrop;
      backdrop = null;
      setTimeout(() => node.remove(), 220);
    }
    document.body.style.overflow = "";
    onFinish({ email: emailChosen });
  }

  // ---- Public API --------------------------------------------------------

  return {
    /**
     * Show the wizard.
     *
     * @param {object} options
     * @param {boolean} options.emailEnabled  whether the server can send email
     * @param {Function} options.onLanguage   called with "es"/"en" on the first screen
     * @param {Function} options.onFinish     called when the wizard closes
     */
    start(options = {}) {
      emailEnabled = options.emailEnabled !== false;
      onLanguage = options.onLanguage || (() => {});
      onFinish = options.onFinish || (() => {});
      step = 1;
      emailChosen = load().email || "";
      mount();
      render();
    },

    /** Show it only the first time. Returns whether it was shown. */
    startIfFirstTime(options = {}) {
      if (load().done) {
        emailChosen = load().email || "";
        return false;
      }
      this.start(options);
      return true;
    },

    /** The address given on the third screen, or "" if there was none. */
    email() {
      return emailChosen;
    },

    /** Wire every `data-onboarding-open` element to reopen the wizard. */
    bindOpeners(options = {}) {
      document.querySelectorAll("[data-onboarding-open]").forEach(el => {
        el.addEventListener("click", () => this.start(options));
      });
    },
  };
})();
