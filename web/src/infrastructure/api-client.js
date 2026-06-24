// Couche INFRASTRUCTURE — adaptateur HTTP vers l'API OpenCacao.
// Seul endroit qui connaît fetch et les codes HTTP. Traduit la réponse/les
// erreurs réseau en entités et erreurs de DOMAINE (dépendance vers l'intérieur).

import {
  ConseilError,
  ErreurKind,
  versConseil,
  versSession,
  versSessionAvecMessages,
} from "../domain/models.js";
import { lireDeviceId } from "./device-id.js";

const ERREURS_HTTP = {
  429: ErreurKind.RATE_LIMIT,
  503: ErreurKind.INDISPONIBLE,
  422: ErreurKind.VALIDATION,
};

/** Traduit un événement d'erreur SSE en ConseilError. */
function erreurDepuisKind(kind) {
  if (kind === "rate_limit") return new ConseilError(ErreurKind.RATE_LIMIT, "Trop de requêtes");
  if (kind === "indisponible") return new ConseilError(ErreurKind.INDISPONIBLE, "Service indisponible");
  if (kind === "session_inconnue")
    return new ConseilError(ErreurKind.SESSION_INCONNUE, "Session inconnue");
  return new ConseilError(ErreurKind.HTTP, "Erreur du service");
}

/**
 * Crée un client API.
 * @param {() => string} lireBaseUrl - fournit l'URL de base courante (configurable).
 */
export function creerClientApi(lireBaseUrl) {
  const baseCourante = () => String(lireBaseUrl() || "").replace(/\/+$/, "");

  // Toutes les requêtes portent l'identité anonyme de l'appareil (D1) : le serveur
  // cloisonne ainsi les conversations par navigateur.
  const enTetes = (extra = {}) => ({ "X-Device-Id": lireDeviceId(), ...extra });

  /**
   * Construit le corps d'une requête de chat. Avec un sessionId, l'historique fait
   * autorité côté serveur (V2) : on ne renvoie pas de tours, juste le session_id.
   */
  function corpsChat(question, { historique = [], sessionId = null } = {}) {
    const corps = { question, langue: "fr", canal: "web" };
    if (sessionId) corps.session_id = sessionId;
    else corps.historique = historique;
    return corps;
  }

  async function demander(question, options = {}) {
    let resp;
    try {
      resp = await fetch(baseCourante() + "/v1/chat", {
        method: "POST",
        headers: enTetes({ "Content-Type": "application/json", Accept: "application/json" }),
        body: JSON.stringify(corpsChat(question, options)),
      });
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }

    if (resp.status === 404) throw new ConseilError(ErreurKind.SESSION_INCONNUE, "Session inconnue");
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
   * @param {{historique?: Array<{role: string, content: string}>, sessionId?: string|null}} options
   */
  async function demanderStream(question, onToken, options = {}) {
    let resp;
    try {
      resp = await fetch(baseCourante() + "/v1/chat/stream", {
        method: "POST",
        headers: enTetes({ "Content-Type": "application/json", Accept: "text/event-stream" }),
        body: JSON.stringify(corpsChat(question, options)),
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
        headers: enTetes({ "Content-Type": "application/json" }),
        body: JSON.stringify({ interaction_id: interactionId, vote }),
      });
    } catch {
      /* retour non bloquant : on n'interrompt jamais l'expérience */
    }
  }

  /* ---------- Sessions de conversation (V2) ---------- */

  /** Crée une conversation côté serveur et renvoie ses métadonnées (Session). */
  async function creerSession({ titre, langue = "fr", canal = "web" } = {}) {
    const corps = { langue, canal };
    if (titre) corps.titre = titre;
    let resp;
    try {
      resp = await fetch(baseCourante() + "/v1/sessions", {
        method: "POST",
        headers: enTetes({ "Content-Type": "application/json", Accept: "application/json" }),
        body: JSON.stringify(corps),
      });
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }
    if (ERREURS_HTTP[resp.status]) throw new ConseilError(ERREURS_HTTP[resp.status], "Erreur");
    if (!resp.ok) throw new ConseilError(ErreurKind.HTTP, "Erreur HTTP " + resp.status);
    return versSession(await resp.json());
  }

  /** Liste les conversations, de la plus récemment active à la plus ancienne. */
  async function listerSessions() {
    let resp;
    try {
      resp = await fetch(baseCourante() + "/v1/sessions", {
        headers: enTetes({ Accept: "application/json" }),
      });
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }
    if (!resp.ok) throw new ConseilError(ErreurKind.HTTP, "Erreur HTTP " + resp.status);
    const data = await resp.json();
    return Array.isArray(data) ? data.map(versSession) : [];
  }

  /** Récupère une conversation et ses messages, ou null si elle n'existe plus. */
  async function obtenirSession(id) {
    if (!id) return null;
    let resp;
    try {
      resp = await fetch(baseCourante() + "/v1/sessions/" + encodeURIComponent(id), {
        headers: enTetes({ Accept: "application/json" }),
      });
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }
    if (resp.status === 404) return null;
    if (!resp.ok) throw new ConseilError(ErreurKind.HTTP, "Erreur HTTP " + resp.status);
    return versSessionAvecMessages(await resp.json());
  }

  /** Supprime une conversation. Renvoie true si la suppression a abouti. */
  async function supprimerSession(id) {
    if (!id) return false;
    let resp;
    try {
      resp = await fetch(baseCourante() + "/v1/sessions/" + encodeURIComponent(id), {
        method: "DELETE",
        headers: enTetes(),
      });
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }
    // 204 = supprimée ; 404 = déjà absente (idempotent du point de vue de l'UI).
    return resp.status === 204 || resp.status === 404;
  }

  /** Renomme une conversation (C3). Renvoie la session à jour, ou null si absente. */
  async function renommerSession(id, titre) {
    if (!id) return null;
    let resp;
    try {
      resp = await fetch(baseCourante() + "/v1/sessions/" + encodeURIComponent(id), {
        method: "PATCH",
        headers: enTetes({ "Content-Type": "application/json", Accept: "application/json" }),
        body: JSON.stringify({ titre }),
      });
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }
    if (resp.status === 404) return null;
    if (ERREURS_HTTP[resp.status]) throw new ConseilError(ERREURS_HTTP[resp.status], "Erreur");
    if (!resp.ok) throw new ConseilError(ErreurKind.HTTP, "Erreur HTTP " + resp.status);
    return versSession(await resp.json());
  }

  /** Recherche plein-texte dans les conversations de l'appareil (C5). */
  async function rechercherSessions(requete) {
    const q = (requete || "").trim();
    if (!q) return [];
    let resp;
    try {
      resp = await fetch(
        baseCourante() + "/v1/sessions/recherche?q=" + encodeURIComponent(q),
        { headers: enTetes({ Accept: "application/json" }) }
      );
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }
    if (!resp.ok) throw new ConseilError(ErreurKind.HTTP, "Erreur HTTP " + resp.status);
    const data = await resp.json();
    return Array.isArray(data) ? data.map(versSession) : [];
  }

  return Object.freeze({
    demander,
    demanderStream,
    envoyerFeedback,
    creerSession,
    listerSessions,
    obtenirSession,
    supprimerSession,
    renommerSession,
    rechercherSessions,
  });
}
