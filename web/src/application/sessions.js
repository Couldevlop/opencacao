// Couche APPLICATION — cas d'usage « conversations persistées » (V2).
// Orchestration pure au-dessus du port client (API). Ne connaît ni le DOM ni fetch.
// Le serveur fait autorité sur le contenu ; ces fonctions ne font que coordonner
// listage, création, ouverture et suppression d'une conversation.

/**
 * @param {{
 *   listerSessions: () => Promise<Array>,
 *   creerSession: (opts?: object) => Promise<object>,
 *   obtenirSession: (id: string) => Promise<object|null>,
 *   supprimerSession: (id: string) => Promise<boolean>,
 * }} client - adaptateur API (port).
 */
export function creerCasUsageSessions(client) {
  return Object.freeze({
    /** Liste les conversations (les plus récemment actives en tête). */
    lister: () => client.listerSessions(),

    /** Crée une conversation vide et renvoie ses métadonnées (Session). */
    creer: (opts) => client.creerSession(opts),

    /**
     * Ouvre une conversation : renvoie { session, messages } ou null si elle n'existe
     * plus (supprimée ailleurs, purge serveur), à charge à l'appelant de repartir
     * d'une conversation neuve.
     */
    ouvrir: (id) => client.obtenirSession(id),

    /** Supprime une conversation. true si la suppression a abouti. */
    supprimer: (id) => client.supprimerSession(id),
  });
}
