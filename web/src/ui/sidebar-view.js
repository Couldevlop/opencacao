// Couche PRÉSENTATION — barre latérale des conversations (V2).
// Rend la liste des conversations, gère la sélection/suppression et le tiroir
// responsive (off-canvas sur mobile, épinglé sur grand écran). Ne connaît ni fetch
// ni l'API : reçoit des données déjà normalisées (domaine) et émet des callbacks.

const TITRE_VIDE = "Aucune conversation pour l'instant.";

export function creerSidebar(
  refs,
  { onNouvelle, onSelectionner, onSupprimer, onRenommer, onRechercher } = {}
) {
  /** Petit bouton d'action (renommer / supprimer) d'une conversation. */
  function boutonAction(classe, libelle, symbole, gestion) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = classe;
    btn.setAttribute("aria-label", libelle);
    btn.title = libelle;
    btn.textContent = symbole;
    btn.addEventListener("click", (e) => {
      e.stopPropagation(); // ne pas ouvrir/sélectionner la conversation visée
      gestion();
    });
    return btn;
  }

  /** Une entrée de la liste : sélection + actions renommer/supprimer. */
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

    const actions = document.createElement("span");
    actions.className = "session-actions";
    actions.append(
      boutonAction("session-renommer", `Renommer « ${session.titre} »`, "✎", () => {
        if (typeof onRenommer === "function") onRenommer(session.id, session.titre);
      }),
      boutonAction("session-suppr", `Supprimer « ${session.titre} »`, "✕", () => {
        if (typeof onSupprimer === "function") onSupprimer(session.id, session.titre);
      })
    );

    li.append(ouvrir, actions);
    return li;
  }

  /**
   * Rend la liste des conversations en surlignant l'active.
   * @param {Array} sessions
   * @param {string|null} idActif
   */
  function rendre(sessions, idActif, messageVide = TITRE_VIDE) {
    refs.liste.textContent = "";
    if (!sessions || sessions.length === 0) {
      const vide = document.createElement("li");
      vide.className = "session-vide";
      vide.textContent = messageVide;
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

  // Recherche (C5) : déclenchée après une courte pause de frappe (anti-rafale).
  let minuteur = null;
  refs.recherche?.addEventListener("input", () => {
    if (minuteur) clearTimeout(minuteur);
    const valeur = refs.recherche.value;
    minuteur = setTimeout(() => {
      if (typeof onRechercher === "function") onRechercher(valeur);
    }, 220);
  });

  /** Réinitialise le champ de recherche (ex. après « Nouvelle »). */
  function viderRecherche() {
    if (refs.recherche) refs.recherche.value = "";
  }

  return Object.freeze({ rendre, ouvrir, fermer, basculer, viderRecherche });
}
