// Couche PRÉSENTATION — rendu du fil de discussion dans le DOM.
// Dépend des entités de domaine (lecture seule) et de l'utilitaire markdown.
// Ne connaît ni fetch ni l'API. Privilégie textContent (sûr) ; le seul innerHTML
// utilisé reçoit du markdown DÉJÀ échappé (cf. markdown.js).

import { rendreMarkdown } from "./markdown.js";

export function creerVue(refs, { onFeedback } = {}) {
  const defiler = () => refs.chat.scrollTo({ top: refs.chat.scrollHeight, behavior: "smooth" });

  /** Boutons de retour 👍/👎 (un seul vote, désactivés après clic). */
  function construireRetour(interactionId) {
    const barre = document.createElement("div");
    barre.className = "feedback";
    barre.append(document.createTextNode("Cette réponse vous a-t-elle aidé ? "));
    const voter = (vote, libelle) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "fb-btn";
      btn.textContent = libelle;
      btn.addEventListener("click", () => {
        if (typeof onFeedback === "function") onFeedback(interactionId, vote);
        barre.querySelectorAll(".fb-btn").forEach((b) => (b.disabled = true));
        btn.classList.add("choisi");
        const merci = document.createElement("span");
        merci.className = "fb-merci";
        merci.textContent = " Merci !";
        barre.appendChild(merci);
      });
      return btn;
    };
    barre.append(voter("up", "👍"), voter("down", "👎"));
    return barre;
  }

  function cacherAccueil() {
    const w = document.getElementById("welcome");
    if (w) w.remove();
  }

  function avatar(classe, emoji) {
    const a = document.createElement("div");
    a.className = "avatar " + classe;
    a.textContent = emoji;
    return a;
  }

  function ajouterUtilisateur(texte) {
    cacherAccueil();
    const m = document.createElement("div");
    m.className = "msg user";
    const b = document.createElement("div");
    b.className = "bubble";
    b.textContent = texte; // entrée utilisateur => jamais en HTML
    m.append(avatar("me", "🧑‍🌾"), b);
    refs.thread.appendChild(m);
    defiler();
  }

  function montrerSaisie() {
    const m = document.createElement("div");
    m.className = "msg bot";
    m.id = "typing";
    const b = document.createElement("div");
    b.className = "bubble";
    const t = document.createElement("div");
    t.className = "typing";
    t.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
    b.appendChild(t);
    m.append(avatar("bot", "🌱"), b);
    refs.thread.appendChild(m);
    defiler();
  }

  function cacherSaisie() {
    const t = document.getElementById("typing");
    if (t) t.remove();
  }

  /** Construit le bloc de métadonnées (sources, ANADER, confiance, disclaimer). */
  function construireMeta(conseil) {
    const meta = document.createElement("div");
    meta.className = "meta";
    const row = document.createElement("div");
    row.className = "meta-row";

    conseil.sources.forEach((s) => {
      const tag = document.createElement("span");
      tag.className = "tag source";
      tag.textContent = "📚 " + s;
      row.appendChild(tag);
    });

    if (conseil.redirectionAnader) {
      const tag = document.createElement("span");
      tag.className = "tag anader";
      tag.textContent = "→ Voir un agent ANADER";
      row.appendChild(tag);
    }

    if (conseil.confiance) {
      const conf = document.createElement("span");
      conf.className = "conf";
      conf.append(document.createTextNode("Confiance : "));
      const niveau = document.createElement("b");
      niveau.className = conseil.confiance; // valeur déjà restreinte (domaine)
      niveau.textContent = conseil.confiance;
      conf.appendChild(niveau);
      row.appendChild(conf);
    }

    meta.appendChild(row);

    if (conseil.disclaimer) {
      const d = document.createElement("div");
      d.className = "disclaimer";
      d.textContent = conseil.disclaimer;
      meta.appendChild(d);
    }

    if (conseil.interactionId) {
      meta.appendChild(construireRetour(conseil.interactionId));
    }
    return meta;
  }

  function ajouterBot(conseil) {
    const m = document.createElement("div");
    m.className = "msg bot";
    const b = document.createElement("div");
    b.className = "bubble";
    b.innerHTML = rendreMarkdown(conseil.reponse); // markdown déjà échappé
    b.appendChild(construireMeta(conseil));
    m.append(avatar("bot", "🌱"), b);
    refs.thread.appendChild(m);
    defiler();
  }

  /**
   * Démarre une bulle « bot » alimentée au fil de l'eau (streaming).
   * Retourne { append(texte), finaliser(conseil) } :
   *  - append : ajoute du texte brut affiché en direct (sûr, textContent) ;
   *  - finaliser : rend la réponse complète en markdown + ajoute les métadonnées.
   */
  function demarrerBot() {
    cacherAccueil();
    const m = document.createElement("div");
    m.className = "msg bot";
    const b = document.createElement("div");
    b.className = "bubble";
    const corps = document.createElement("div");
    corps.className = "stream-corps";
    corps.classList.add("curseur");
    b.appendChild(corps);
    m.append(avatar("bot", "🌱"), b);
    refs.thread.appendChild(m);
    defiler();

    let texte = "";
    function append(t) {
      texte += t;
      corps.textContent = texte; // direct : texte brut (le markdown vient à la fin)
      defiler();
    }
    function finaliser(conseil) {
      corps.classList.remove("curseur");
      corps.innerHTML = rendreMarkdown(conseil.reponse || texte); // markdown échappé
      b.appendChild(construireMeta(conseil));
      defiler();
    }
    return { append, finaliser };
  }

  function ajouterErreur(message) {
    const m = document.createElement("div");
    m.className = "msg bot";
    const b = document.createElement("div");
    b.className = "bubble";
    const e = document.createElement("div");
    e.className = "error-note";
    e.textContent = message; // message contrôlé, posé en texte
    b.appendChild(e);
    m.append(avatar("bot", "🌱"), b);
    refs.thread.appendChild(m);
    defiler();
  }

  return Object.freeze({
    ajouterUtilisateur,
    montrerSaisie,
    cacherSaisie,
    ajouterBot,
    demarrerBot,
    ajouterErreur,
  });
}
