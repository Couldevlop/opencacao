// Couche APPLICATION — cas d'usage « authentification par lien magique » (D2).
// Valide l'email (règle de domaine) puis délègue au client (port). Ne connaît ni le
// DOM ni fetch.

import { ConseilError, ErreurKind } from "../domain/models.js";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/**
 * @param {{demanderLien: (email: string) => Promise<void>,
 *          verifierAuth: (token: string) => Promise<object|null>}} client
 */
export function creerCasUsageAuth(client) {
  return Object.freeze({
    /** Demande l'envoi d'un lien magique à l'email (après validation de forme). */
    demander: async (email) => {
      const e = (email || "").trim().toLowerCase();
      if (!EMAIL.test(e)) {
        throw new ConseilError(ErreurKind.VALIDATION, "Adresse email invalide.");
      }
      return client.demanderLien(e);
    },

    /** Vérifie un jeton de lien et renvoie { accountId, email }, ou null si invalide. */
    verifier: (token) => client.verifierAuth(token),
  });
}
