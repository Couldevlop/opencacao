// Couche INFRASTRUCTURE — adaptateur HTTP vers l'API OpenCacao.
// Seul endroit qui connaît fetch et les codes HTTP. Traduit la réponse/les
// erreurs réseau en entités et erreurs de DOMAINE (dépendance vers l'intérieur).

import { ConseilError, ErreurKind, versConseil } from "../domain/models.js";

const ERREURS_HTTP = {
  429: ErreurKind.RATE_LIMIT,
  503: ErreurKind.INDISPONIBLE,
  422: ErreurKind.VALIDATION,
};

/** Traduit un événement d'erreur SSE en ConseilError. */
function erreurDepuisKind(kind) {
  if (kind === "rate_limit") return new ConseilError(ErreurKind.RATE_LIMIT, "Trop de requêtes");
  if (kind === "indisponible") return new ConseilError(ErreurKind.INDISPONIBLE, "Service indisponible");
  return new ConseilError(ErreurKind.HTTP, "Erreur du service");
}

/**
 * Crée un client API.
 * @param {() => string} lireBaseUrl - fournit l'URL de base courante (configurable).
 */
export function creerClientApi(lireBaseUrl) {
  const baseCourante = () => String(lireBaseUrl() || "").replace(/\/+$/, "");

  async function demander(question) {
    let resp;
    try {
      resp = await fetch(baseCourante() + "/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ question, langue: "fr", canal: "web" }),
      });
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }

    if (ERREURS_HTTP[resp.status]) throw new ConseilError(ERREURS_HTTP[resp.status], "Erreur");
    if (!resp.ok) throw new ConseilError(ErreurKind.HTTP, "Erreur HTTP " + resp.status);

    try {
      return versConseil(await resp.json());
    } catch {
      throw new ConseilError(ErreurKind.HTTP, "Réponse illisible");
    }
  }

  /**
   * Demande un conseil en flux (SSE). Appelle onToken(texte) au fil de l'eau et
   * renvoie l'entité Conseil finale (réponse complète + métadonnées).
   * @param {string} question
   * @param {(texte: string) => void} onToken
   */
  async function demanderStream(question, onToken) {
    let resp;
    try {
      resp = await fetch(baseCourante() + "/v1/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ question, langue: "fr", canal: "web" }),
      });
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }

    if (ERREURS_HTTP[resp.status]) throw new ConseilError(ERREURS_HTTP[resp.status], "Erreur");
    if (!resp.ok || !resp.body) throw new ConseilError(ErreurKind.HTTP, "Erreur HTTP " + resp.status);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let tampon = "";
    let texte = "";
    let meta = null;

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      tampon += decoder.decode(value, { stream: true });

      let sep;
      while ((sep = tampon.indexOf("\n\n")) >= 0) {
        const bloc = tampon.slice(0, sep);
        tampon = tampon.slice(sep + 2);
        const ligne = bloc.split("\n").find((l) => l.startsWith("data:"));
        if (!ligne) continue;

        let evt;
        try {
          evt = JSON.parse(ligne.slice(5).trim());
        } catch {
          continue;
        }
        if (evt.type === "token") {
          texte += evt.text;
          onToken(evt.text);
        } else if (evt.type === "done") {
          meta = evt;
        } else if (evt.type === "error") {
          throw erreurDepuisKind(evt.kind);
        }
      }
    }

    return versConseil({ ...(meta || {}), reponse: texte });
  }

  /**
   * Envoie un retour 👍/👎 (best-effort : les erreurs sont silencieuses).
   * @param {string} interactionId
   * @param {"up"|"down"} vote
   */
  async function envoyerFeedback(interactionId, vote) {
    if (!interactionId) return;
    try {
      await fetch(baseCourante() + "/v1/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interaction_id: interactionId, vote }),
      });
    } catch {
      /* retour non bloquant : on n'interrompt jamais l'expérience */
    }
  }

  return Object.freeze({ demander, demanderStream, envoyerFeedback });
}
