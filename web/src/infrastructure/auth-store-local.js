// Couche INFRASTRUCTURE — persistance LOCALE du compte connecté (D2).
// Quand l'utilisateur s'authentifie (lien magique), on conserve son identifiant de
// compte (opaque, renvoyé par l'API) et son email. Cet identifiant sert ensuite de
// « proprietaire » à la place du device id (D1) : les conversations suivent la
// personne d'un appareil à l'autre. Tolérant aux pannes (mode privé) : en cas
// d'erreur localStorage, on dégrade en « non connecté ».

const CLE_COMPTE = "opencacao.compte";

/** Compte connecté { accountId, email }, ou null si anonyme. */
export function lireCompte() {
  try {
    const brut = localStorage.getItem(CLE_COMPTE);
    if (!brut) return null;
    const c = JSON.parse(brut);
    return c && typeof c.accountId === "string" && c.accountId ? c : null;
  } catch {
    return null;
  }
}

/** Mémorise (ou efface si null) le compte connecté. */
export function ecrireCompte(compte) {
  try {
    if (compte && compte.accountId) {
      localStorage.setItem(CLE_COMPTE, JSON.stringify({ accountId: compte.accountId, email: compte.email || "" }));
    } else {
      localStorage.removeItem(CLE_COMPTE);
    }
  } catch {
    /* persistance best-effort : on n'interrompt jamais l'expérience */
  }
}
