// Couche INFRASTRUCTURE — adaptateur HTTP vers l'API OpenCacao.
// Seul endroit qui connaît fetch et les codes HTTP. Traduit la réponse/les
// erreurs réseau en entités et erreurs de DOMAINE (dépendance vers l'intérieur).

import { ConseilError, ErreurKind, versConseil } from "../domain/models.js";

/**
 * Crée un client API.
 * @param {() => string} lireBaseUrl - fournit l'URL de base courante (configurable).
 */
export function creerClientApi(lireBaseUrl) {
  async function demander(question) {
    const base = String(lireBaseUrl() || "").replace(/\/+$/, "");

    let resp;
    try {
      resp = await fetch(base + "/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ question, langue: "fr", canal: "web" }),
      });
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }

    if (resp.status === 429) throw new ConseilError(ErreurKind.RATE_LIMIT, "Trop de requêtes");
    if (resp.status === 503) throw new ConseilError(ErreurKind.INDISPONIBLE, "Service indisponible");
    if (resp.status === 422) throw new ConseilError(ErreurKind.VALIDATION, "Question invalide");
    if (!resp.ok) throw new ConseilError(ErreurKind.HTTP, "Erreur HTTP " + resp.status);

    try {
      return versConseil(await resp.json());
    } catch {
      throw new ConseilError(ErreurKind.HTTP, "Réponse illisible");
    }
  }

  return Object.freeze({ demander });
}
