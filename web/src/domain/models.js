// Couche DOMAINE — entités et règles pures, sans dépendance technique
// (ni DOM, ni fetch). C'est le cœur stable de l'application.

export const Confiance = Object.freeze({
  FAIBLE: "faible",
  MOYENNE: "moyenne",
  ELEVEE: "elevee",
});

const CONFIANCES = new Set(Object.values(Confiance));

export const ErreurKind = Object.freeze({
  VALIDATION: "validation",
  RATE_LIMIT: "rate_limit",
  INDISPONIBLE: "indisponible",
  RESEAU: "reseau",
  HTTP: "http",
  // La conversation référencée n'existe plus côté serveur (supprimée/expirée).
  SESSION_INCONNUE: "session_inconnue",
});

/** Erreur métier porteuse d'un type, pour un message utilisateur adapté. */
export class ConseilError extends Error {
  constructor(kind, message) {
    super(message);
    this.name = "ConseilError";
    this.kind = kind;
  }
}

/** Longueurs alignées sur la validation de l'API (Pydantic). */
export const QUESTION_MIN = 3;
export const QUESTION_MAX = 2000;

/**
 * Normalise une réponse brute de l'API en entité de domaine sûre :
 * types garantis, confiance restreinte aux valeurs connues.
 */
export function versConseil(brut) {
  const confiance = CONFIANCES.has(brut?.confiance) ? brut.confiance : Confiance.MOYENNE;
  return Object.freeze({
    reponse: typeof brut?.reponse === "string" ? brut.reponse : "",
    sources: Array.isArray(brut?.sources) ? brut.sources.filter((s) => typeof s === "string") : [],
    confiance,
    redirectionAnader: Boolean(brut?.redirection_anader),
    disclaimer: typeof brut?.disclaimer === "string" ? brut.disclaimer : "",
    interactionId: typeof brut?.interaction_id === "string" ? brut.interaction_id : null,
  });
}

/** Titre par défaut d'une conversation, aligné sur l'API (models/session.py). */
export const TITRE_PAR_DEFAUT = "Nouvelle conversation";

/**
 * Normalise les métadonnées d'une session (V2 conversationnelle) : types garantis,
 * titre jamais vide. `cree_le`/`maj_le` restent des chaînes ISO (tri côté serveur).
 */
export function versSession(brut) {
  return Object.freeze({
    id: typeof brut?.id === "string" ? brut.id : "",
    titre: typeof brut?.titre === "string" && brut.titre.trim() ? brut.titre : TITRE_PAR_DEFAUT,
    langue: typeof brut?.langue === "string" ? brut.langue : "fr",
    canal: typeof brut?.canal === "string" ? brut.canal : "web",
    creeLe: typeof brut?.cree_le === "string" ? brut.cree_le : "",
    majLe: typeof brut?.maj_le === "string" ? brut.maj_le : "",
  });
}

/** Normalise un message persisté d'une session (rôle restreint, contenu sûr). */
export function versMessage(brut) {
  return Object.freeze({
    role: brut?.role === "assistant" ? "assistant" : "user",
    content: typeof brut?.content === "string" ? brut.content : "",
    creeLe: typeof brut?.cree_le === "string" ? brut.cree_le : "",
  });
}

/** Normalise une session et ses messages (réponse de GET /v1/sessions/{id}). */
export function versSessionAvecMessages(brut) {
  const messages = Array.isArray(brut?.messages) ? brut.messages.map(versMessage) : [];
  return Object.freeze({ session: versSession(brut?.session), messages });
}
