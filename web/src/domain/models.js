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
