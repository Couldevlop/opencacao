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
import { versCapture, versParcelle } from "../domain/parcelle.js";
import { lireCompte } from "./auth-store-local.js";
import { lireDeviceId } from "./device-id.js";

const ERREURS_HTTP = {
  429: ErreurKind.RATE_LIMIT,
  503: ErreurKind.INDISPONIBLE,
  422: ErreurKind.VALIDATION,
  // Captures de parcelle (V3) : seules routes à transporter des images, donc
  // seules à pouvoir buter sur le plafond de corps (413) ou sur le disque (507).
  413: ErreurKind.CHARGE_TROP_LOURDE,
  507: ErreurKind.STOCKAGE_INSUFFISANT,
};

/**
 * Lit le message d'erreur du serveur s'il en fournit un. Les refus métier des
 * parcelles (géométrie hors Côte d'Ivoire, tracé qui se coupe, quota) portent un
 * `detail` rédigé en français pour le producteur : on l'affiche tel quel plutôt
 * que de le remplacer par une formule générique. Les erreurs de validation
 * Pydantic, elles, rendent une liste : on les ignore.
 */
async function detailServeur(resp) {
  try {
    const data = await resp.json();
    return typeof data?.detail === "string" ? data.detail : "";
  } catch {
    return "";
  }
}

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

  // Identité portée par chaque requête (en-tête X-Device-Id) : l'identifiant de
  // compte si l'utilisateur est connecté (D2 — ses conversations le suivent d'un
  // appareil à l'autre), sinon l'identité anonyme de l'appareil (D1).
  const identite = () => {
    const compte = lireCompte();
    return compte ? compte.accountId : lireDeviceId();
  };
  const enTetes = (extra = {}) => ({ "X-Device-Id": identite(), ...extra });

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
   * renvoie l'entité Conseil finale (réponse complète + métadonnées). Les événements
   * « progress » (étape en cours côté serveur) sont relayés à options.onProgress.
   * @param {string} question
   * @param {(texte: string) => void} onToken
   * @param {{historique?: Array<{role: string, content: string}>, sessionId?: string|null, onProgress?: (texte: string) => void}} options
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
        } else if (evt.type === "progress") {
          if (options.onProgress) options.onProgress(evt.text);
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

  /* ---------- Authentification par lien magique (D2) ---------- */

  /** Demande l'envoi d'un lien magique à l'email. Résout si la demande est acceptée. */
  async function demanderLien(email) {
    let resp;
    try {
      resp = await fetch(baseCourante() + "/v1/auth/request", {
        method: "POST",
        headers: enTetes({ "Content-Type": "application/json", Accept: "application/json" }),
        body: JSON.stringify({ email }),
      });
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }
    if (resp.status === 404) throw new ConseilError(ErreurKind.AUTH_INDISPONIBLE, "Auth désactivée");
    if (resp.status === 422) throw new ConseilError(ErreurKind.VALIDATION, "Email invalide");
    if (ERREURS_HTTP[resp.status]) throw new ConseilError(ERREURS_HTTP[resp.status], "Erreur");
    if (!resp.ok) throw new ConseilError(ErreurKind.HTTP, "Erreur HTTP " + resp.status);
  }

  /** Vérifie un jeton de lien. Renvoie { accountId, email } ou null si invalide/expiré. */
  async function verifierAuth(token) {
    let resp;
    try {
      resp = await fetch(baseCourante() + "/v1/auth/verify", {
        method: "POST",
        headers: enTetes({ "Content-Type": "application/json", Accept: "application/json" }),
        body: JSON.stringify({ token }),
      });
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }
    if (resp.status === 400) return null; // lien invalide ou expiré
    if (resp.status === 404) throw new ConseilError(ErreurKind.AUTH_INDISPONIBLE, "Auth désactivée");
    if (!resp.ok) throw new ConseilError(ErreurKind.HTTP, "Erreur HTTP " + resp.status);
    const data = await resp.json();
    return { accountId: data.account_id, email: data.email };
  }

  /* ---------- Parcelles et captures terrain (V3, C1) ---------- */

  /**
   * Appelle une route de parcelle et traduit les codes HTTP en erreurs de domaine.
   * @param {string} chemin
   * @param {RequestInit} init
   * @param {{tolererAbsence?: boolean, messageAbsence?: string}} options - avec
   *   `tolererAbsence`, un 404 rend `null` au lieu de lever (lecture d'une parcelle
   *   supprimée, ou routes absentes quand PARCELLES_ENABLED vaut false).
   * @returns {Promise<Response|null>}
   */
  async function appelParcelle(chemin, init = {}, options = {}) {
    const { tolererAbsence = false, messageAbsence = "Parcelle inconnue" } = options;
    let resp;
    try {
      resp = await fetch(baseCourante() + chemin, init);
    } catch {
      throw new ConseilError(ErreurKind.RESEAU, "API injoignable");
    }
    if (resp.status === 404) {
      if (tolererAbsence) return null;
      throw new ConseilError(ErreurKind.INTROUVABLE, messageAbsence);
    }
    if (ERREURS_HTTP[resp.status]) {
      // Message vide si le serveur n'en fournit pas de lisible : à l'écran d'y
      // substituer sa propre phrase, jamais un « Erreur » sec au producteur.
      throw new ConseilError(ERREURS_HTTP[resp.status], await detailServeur(resp));
    }
    if (!resp.ok) throw new ConseilError(ErreurKind.HTTP, "Erreur HTTP " + resp.status);
    return resp;
  }

  /** Crée une parcelle rattachée à cet appareil. Renvoie l'entité Parcelle. */
  async function creerParcelle({ nom, localite }) {
    const resp = await appelParcelle(
      "/v1/parcelles",
      {
        method: "POST",
        headers: enTetes({ "Content-Type": "application/json", Accept: "application/json" }),
        body: JSON.stringify({ nom, localite }),
      },
      { messageAbsence: "Les parcelles ne sont pas activées sur ce serveur." }
    );
    return versParcelle(await resp.json());
  }

  /**
   * Liste les parcelles de cet appareil. Renvoie `null` — et non un tableau vide —
   * si le serveur ne sert pas les parcelles : l'UI doit pouvoir distinguer
   * « aucune parcelle » de « fonction absente ».
   */
  async function listerParcelles() {
    const resp = await appelParcelle(
      "/v1/parcelles",
      { headers: enTetes({ Accept: "application/json" }) },
      { tolererAbsence: true }
    );
    if (!resp) return null;
    const data = await resp.json();
    return Array.isArray(data) ? data.map(versParcelle) : [];
  }

  /** Récupère une parcelle, ou null si elle n'existe plus pour cet appareil. */
  async function obtenirParcelle(identifiant) {
    if (!identifiant) return null;
    const resp = await appelParcelle(
      "/v1/parcelles/" + encodeURIComponent(identifiant),
      { headers: enTetes({ Accept: "application/json" }) },
      { tolererAbsence: true }
    );
    return resp ? versParcelle(await resp.json()) : null;
  }

  /** Enregistre le contour relevé d'une parcelle. Renvoie la parcelle à jour. */
  async function enregistrerGeometrie(identifiant, { points, source }) {
    const resp = await appelParcelle(
      "/v1/parcelles/" + encodeURIComponent(identifiant) + "/geometrie",
      {
        method: "PUT",
        headers: enTetes({ "Content-Type": "application/json", Accept: "application/json" }),
        body: JSON.stringify({ points, source }),
      }
    );
    return versParcelle(await resp.json());
  }

  /** Dépose une capture terrain (images échantillonnées et/ou trace GPS). */
  async function deposerCapture(identifiant, { modalite, images = [], trace = [] }) {
    const resp = await appelParcelle(
      "/v1/parcelles/" + encodeURIComponent(identifiant) + "/captures",
      {
        method: "POST",
        headers: enTetes({ "Content-Type": "application/json", Accept: "application/json" }),
        body: JSON.stringify({ modalite, images, trace }),
      }
    );
    return versCapture(await resp.json());
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
    demanderLien,
    verifierAuth,
    creerParcelle,
    listerParcelles,
    obtenirParcelle,
    enregistrerGeometrie,
    deposerCapture,
  });
}
