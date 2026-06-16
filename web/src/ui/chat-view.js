// Couche PRÉSENTATION — rendu du fil de discussion dans le DOM.
// Dépend des entités de domaine (lecture seule) et de l'utilitaire markdown.
// Ne connaît ni fetch ni l'API. Privilégie textContent (sûr) ; le seul innerHTML
// utilisé reçoit du markdown DÉJÀ échappé (cf. markdown.js).

import { rendreMarkdown } from "./markdown.js";

export function creerVue(refs) {
  const defiler = () => refs.chat.scrollTo({ top: refs.chat.scrollHeight, behavior: "smooth" });

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

  function ajouterBot(conseil) {
    const m = document.createElement("div");
    m.className = "msg bot";
    const b = document.createElement("div");
    b.className = "bubble";
    b.innerHTML = rendreMarkdown(conseil.reponse); // markdown déjà échappé

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

    b.appendChild(meta);
    m.append(avatar("bot", "🌱"), b);
    refs.thread.appendChild(m);
    defiler();
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
    ajouterErreur,
  });
}
