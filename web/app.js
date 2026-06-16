/* OpenCacao — logique de l'interface. Appelle POST {API}/v1/chat et affiche la
   réponse avec ses sources, son niveau de confiance et le disclaimer. */

(() => {
  "use strict";

  const LS_KEY = "opencacao.apiUrl";
  const DEFAULT_API = "http://localhost:8080";

  const el = {
    chat: document.getElementById("chat"),
    thread: document.getElementById("thread"),
    welcome: document.getElementById("welcome"),
    suggestions: document.getElementById("suggestions"),
    form: document.getElementById("composer"),
    input: document.getElementById("input"),
    send: document.getElementById("send"),
    settingsBtn: document.getElementById("settingsBtn"),
    modal: document.getElementById("modal"),
    apiUrl: document.getElementById("apiUrl"),
    modalSave: document.getElementById("modalSave"),
    modalCancel: document.getElementById("modalCancel"),
  };

  let apiBase = localStorage.getItem(LS_KEY) || DEFAULT_API;
  let pending = false;

  /* ---------- utilitaires ---------- */
  const escapeHtml = (s) =>
    s.replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

  // Markdown minimal et SÛR : on échappe d'abord, puis gras/italique/listes/sauts.
  function renderMarkdown(text) {
    const lines = escapeHtml(text).split(/\r?\n/);
    let html = "";
    let inList = false;
    for (const raw of lines) {
      const line = raw.trim();
      const isItem = /^[-*•]\s+/.test(line);
      if (isItem) {
        if (!inList) { html += "<ul>"; inList = true; }
        html += "<li>" + inline(line.replace(/^[-*•]\s+/, "")) + "</li>";
      } else {
        if (inList) { html += "</ul>"; inList = false; }
        if (line) html += "<p>" + inline(line) + "</p>";
      }
    }
    if (inList) html += "</ul>";
    return html || "<p></p>";
  }

  function inline(s) {
    return s
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  }

  function hideWelcome() {
    if (el.welcome) el.welcome.remove();
  }

  function scrollToEnd() {
    el.chat.scrollTo({ top: el.chat.scrollHeight, behavior: "smooth" });
  }

  function addUser(text) {
    hideWelcome();
    const msg = document.createElement("div");
    msg.className = "msg user";
    msg.innerHTML =
      '<div class="avatar me">🧑‍🌾</div><div class="bubble"></div>';
    msg.querySelector(".bubble").textContent = text;
    el.thread.appendChild(msg);
    scrollToEnd();
  }

  function addTyping() {
    const msg = document.createElement("div");
    msg.className = "msg bot";
    msg.id = "typing";
    msg.innerHTML =
      '<div class="avatar bot">🌱</div><div class="bubble"><div class="typing"><span></span><span></span><span></span></div></div>';
    el.thread.appendChild(msg);
    scrollToEnd();
  }

  function removeTyping() {
    const t = document.getElementById("typing");
    if (t) t.remove();
  }

  function addBot(data) {
    const msg = document.createElement("div");
    msg.className = "msg bot";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.innerHTML = renderMarkdown(data.reponse || "");

    // Métadonnées (sources, confiance, ANADER, disclaimer)
    const meta = document.createElement("div");
    meta.className = "meta";
    const row = document.createElement("div");
    row.className = "meta-row";

    (data.sources || []).forEach((s) => {
      const tag = document.createElement("span");
      tag.className = "tag source";
      tag.textContent = "📚 " + s;
      row.appendChild(tag);
    });

    if (data.redirection_anader) {
      const tag = document.createElement("span");
      tag.className = "tag anader";
      tag.textContent = "→ Voir un agent ANADER";
      row.appendChild(tag);
    }

    if (data.confiance) {
      const conf = document.createElement("span");
      conf.className = "conf";
      conf.innerHTML =
        "Confiance : <b class='" + data.confiance + "'>" + data.confiance + "</b>";
      row.appendChild(conf);
    }

    meta.appendChild(row);

    if (data.disclaimer) {
      const disc = document.createElement("div");
      disc.className = "disclaimer";
      disc.textContent = data.disclaimer;
      meta.appendChild(disc);
    }

    bubble.appendChild(meta);
    msg.innerHTML = '<div class="avatar bot">🌱</div>';
    msg.appendChild(bubble);
    el.thread.appendChild(msg);
    scrollToEnd();
  }

  function addError(text) {
    const msg = document.createElement("div");
    msg.className = "msg bot";
    msg.innerHTML = '<div class="avatar bot">🌱</div>';
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    const err = document.createElement("div");
    err.className = "error-note";
    err.textContent = text;
    bubble.appendChild(err);
    msg.appendChild(bubble);
    el.thread.appendChild(msg);
    scrollToEnd();
  }

  /* ---------- envoi ---------- */
  async function ask(question) {
    if (pending || !question.trim()) return;
    pending = true;
    setSendEnabled(false);
    addUser(question);
    el.input.value = "";
    autogrow();
    addTyping();

    try {
      const resp = await fetch(apiBase.replace(/\/+$/, "") + "/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, langue: "fr", canal: "web" }),
      });
      removeTyping();

      if (resp.status === 429) {
        addError("Trop de requêtes. Patientez une minute avant de réessayer.");
      } else if (resp.status === 503) {
        addError("Le service de conseil est momentanément indisponible.");
      } else if (resp.status === 422) {
        addError("Question invalide (entre 3 et 2000 caractères).");
      } else if (!resp.ok) {
        addError("Erreur " + resp.status + ". Vérifiez l'URL de l'API (⚙️).");
      } else {
        const data = await resp.json();
        addBot(data);
      }
    } catch (e) {
      removeTyping();
      addError(
        "Impossible de joindre l'API à " +
          apiBase +
          ". Vérifiez l'URL (⚙️), que le service tourne et que CORS autorise cette origine."
      );
    } finally {
      pending = false;
      autogrow();
    }
  }

  /* ---------- UI ---------- */
  function setSendEnabled(force) {
    const ok = force !== undefined ? force : el.input.value.trim().length > 0;
    el.send.disabled = !ok || pending;
  }

  function autogrow() {
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, 180) + "px";
    setSendEnabled();
  }

  el.input.addEventListener("input", autogrow);
  el.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      ask(el.input.value);
    }
  });
  el.form.addEventListener("submit", (e) => {
    e.preventDefault();
    ask(el.input.value);
  });

  if (el.suggestions) {
    el.suggestions.addEventListener("click", (e) => {
      const chip = e.target.closest(".chip");
      if (chip) ask(chip.textContent.trim());
    });
  }

  /* ---------- paramètres ---------- */
  function openModal() {
    el.apiUrl.value = apiBase;
    el.modal.hidden = false;
    el.apiUrl.focus();
  }
  function closeModal() {
    el.modal.hidden = true;
  }
  el.settingsBtn.addEventListener("click", openModal);
  el.modalCancel.addEventListener("click", closeModal);
  el.modal.addEventListener("click", (e) => {
    if (e.target === el.modal) closeModal();
  });
  el.modalSave.addEventListener("click", () => {
    const v = el.apiUrl.value.trim();
    if (v) {
      apiBase = v;
      localStorage.setItem(LS_KEY, v);
    }
    closeModal();
  });

  setSendEnabled(false);
})();
