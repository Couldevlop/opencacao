// Couche INFRASTRUCTURE — persistance LOCALE de la conversation active (C4).
// Le serveur fait autorité sur le contenu des conversations (mémoire V2) ; le
// navigateur ne mémorise QUE l'identifiant de la conversation ouverte, pour la
// rouvrir telle quelle au prochain chargement. localStorage est tolérant aux pannes
// (mode privé, quota) : toute erreur est avalée et dégrade en « pas de reprise ».

const CLE_SESSION = "opencacao.sessionActive";

/** Identifiant de la conversation active mémorisée localement, ou null. */
export function lireSessionActive() {
  try {
    const v = localStorage.getItem(CLE_SESSION);
    return v && v.trim() ? v : null;
  } catch {
    return null;
  }
}

/** Mémorise (ou efface si falsy) l'identifiant de la conversation active. */
export function ecrireSessionActive(id) {
  try {
    if (id) localStorage.setItem(CLE_SESSION, id);
    else localStorage.removeItem(CLE_SESSION);
  } catch {
    /* persistance best-effort : on n'interrompt jamais l'expérience */
  }
}
