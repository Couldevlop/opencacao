// Couche PRÉSENTATION — barre latérale des conversations (V2).
// Rend la liste des conversations, gère la sélection/suppression et le tiroir
// responsive (off-canvas sur mobile, épinglé sur grand écran). Ne connaît ni fetch
// ni l'API : reçoit des données déjà normalisées (domaine) et émet des callbacks.

const TITRE_VIDE = "Aucune conversation pour l'instant.";

export function creerSidebar(refs, { onNouvelle, onSelectionner, onSupprimer } = {}) {
  /** Bouton de suppression (croix) d'une conversation. */
  function boutonSupprimer(id, titre) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "session-suppr";
    btn.setAttribute("aria-label", `Supprimer la conversation « ${titre} »`);
    btn.title = "Supprimer";
    btn.textContent = "✕";
    btn.addEventListener("click", (e) => {
      e.stopPropagation(); // ne pas ouvrir la conversation qu'on supprime
      if (typeof onSupprimer === "function") onSupprimer(id, titre);
    });
    return btn;
  }

  /** Une entrée de la liste : bouton de sélection + bouton de suppression. */
  function elementSession(session, idActif) {
    const li = document.createElement("li");
    li.className = "session-item" + (session.id === idActif ? " actif" : "");

    const ouvrir = document.createElement("button");
    ouvrir.type = "button";
    ouvrir.className = "session-ouvrir";
    ouvrir.textContent = session.titre; // titre serveur => texte (jamais HTML)
    if (session.id === idActif) ouvrir.setAttribute("aria-current", "true");
    ouvrir.addEventListener("click", () => {
      if (typeof onSelectionner === "function") onSelectionner(session.id);
    });

    li.append(ouvrir, boutonSupprimer(session.id, session.titre));
    return li;
  }

  /**
   * Rend la liste des conversations en surlignant l'active.
   * @param {Array} sessions
   * @param {string|null} idActif
   */
  function rendre(sessions, idActif) {
    refs.liste.textContent = "";
    if (!sessions || sessions.length === 0) {
      const vide = document.createElement("li");
      vide.className = "session-vide";
      vide.textContent = TITRE_VIDE;
      refs.liste.appendChild(vide);
      return;
    }
    const fragment = document.createDocumentFragment();
    sessions.forEach((s) => fragment.appendChild(elementSession(s, idActif)));
    refs.liste.appendChild(fragment);
  }

  /* ---------- tiroir responsive ---------- */
  function ouvrir() {
    refs.sidebar.classList.add("ouvert");
    if (refs.backdrop) refs.backdrop.hidden = false;
  }
  function fermer() {
    refs.sidebar.classList.remove("ouvert");
    if (refs.backdrop) refs.backdrop.hidden = true;
  }
  function basculer() {
    if (refs.sidebar.classList.contains("ouvert")) fermer();
    else ouvrir();
  }

  refs.toggle?.addEventListener("click", basculer);
  refs.backdrop?.addEventListener("click", fermer);
  refs.nouvelle?.addEventListener("click", () => {
    if (typeof onNouvelle === "function") onNouvelle();
  });

  return Object.freeze({ rendre, ouvrir, fermer, basculer });
}
