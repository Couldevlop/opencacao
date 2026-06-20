// Couche APPLICATION — cas d'usage « demander un conseil ».
// Orchestration pure : valide l'entrée (règle de domaine) puis délègue au
// client (port). Ne connaît ni le DOM ni fetch ; reçoit le client par injection.

import { ConseilError, ErreurKind, QUESTION_MAX, QUESTION_MIN } from "../domain/models.js";

/**
 * @param {{demander: (q: string) => Promise<object>}} client - adaptateur API (port).
 */
function valider(question) {
  const q = (question || "").trim();
  if (q.length < QUESTION_MIN || q.length > QUESTION_MAX) {
    throw new ConseilError(
      ErreurKind.VALIDATION,
      `La question doit faire entre ${QUESTION_MIN} et ${QUESTION_MAX} caractères.`
    );
  }
  return q;
}

export function creerCasUsageConseil(client) {
  /** @param {string} question @returns {Promise<object>} entité Conseil */
  return async function demanderConseil(question) {
    return client.demander(valider(question));
  };
}

/**
 * Cas d'usage en flux : valide puis délègue au client streaming.
 * @param {{demanderStream: (q: string, onToken: (t: string) => void) => Promise<object>}} client
 */
export function creerCasUsageConseilStream(client) {
  /**
   * @param {string} question
   * @param {(texte: string) => void} onToken
   * @returns {Promise<object>} entité Conseil finale
   */
  return async function demanderConseilStream(question, onToken, historique = []) {
    return client.demanderStream(valider(question), onToken, historique);
  };
}
