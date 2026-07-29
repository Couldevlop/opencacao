// Couche DOMAINE — parcelle cacaoyère et captures terrain (V3, chantier C1).
// Vocabulaire, plafonds et règles pures : ni DOM, ni fetch, ni canvas, ni capteur.
// Les bornes sont alignées sur celles de l'API (api/app/models/parcelle.py) : le
// navigateur refuse AVANT de consommer la bande passante d'un réseau mobile, mais
// c'est bien le serveur qui fait autorité — ces constantes ne sont qu'un miroir.

/** Les quatre modalités de capture terrain (miroir de `Modalite` côté API). */
export const Modalite = Object.freeze({
  PHOTOS: "photos",
  VIDEO: "video",
  PARCOURS: "parcours",
  PARCOURS_VIDEO: "parcours_video",
});

/** Provenance d'une géométrie (miroir de `SourceGeometrie` côté API). */
export const SourceGeometrie = Object.freeze({
  PARCOURS_GPS: "parcours_gps",
  SAISIE_MANUELLE: "saisie_manuelle",
});

/** Libellés des modalités, pour l'affichage. */
export const LIBELLE_MODALITE = Object.freeze({
  [Modalite.PHOTOS]: "Photos",
  [Modalite.VIDEO]: "Vidéo",
  [Modalite.PARCOURS]: "Parcours",
  [Modalite.PARCOURS_VIDEO]: "Parcours + vidéo",
});

// --- Plafonds de capture -----------------------------------------------------

// Douze vues suffisent à un constat, et bornent le téléversement (miroir de
// IMAGES_MAX_PAR_CAPTURE côté API).
export const IMAGES_MAX = 12;

// Une image toutes les deux secondes de vidéo : au-delà, deux images voisines
// montrent la même chose.
export const INTERVALLE_ECHANTILLON_S = 2;

// Redimensionnement OBLIGATOIRE avant envoi. Une photo de téléphone moderne fait
// 4000 px de large ; encodée en base64 elle pulvériserait à elle seule le plafond
// de 8 Mio du corps de requête. À 1024 px et qualité 0,85, une vue pèse ~200 Ko,
// et douze vues tiennent largement dans le plafond.
export const LARGEUR_MAX_PX = 1024;
export const QUALITE_JPEG = 0.85;

// Un point qui n'a pas bougé de 5 m n'apporte aucune information sur le contour.
export const DISTANCE_MIN_M = 5;

// Au-delà de 50 m d'incertitude, le point dégrade le tracé au lieu de l'enrichir.
export const PRECISION_MAX_M = 50;

// Plafond de précision déclarable à l'API (Field(le=10_000) côté Pydantic) : une
// valeur plus grande ferait échouer toute la capture en 422, on l'omet plutôt.
export const PRECISION_DECLARABLE_MAX_M = 10_000;

// Miroirs des bornes Pydantic de l'API.
export const TRACE_MAX_POINTS = 2_000;
export const POINTS_MIN_POLYGONE = 4;
export const SCORE_NETTETE_MAX = 100_000;
export const NOM_MAX = 120;
export const LOCALITE_MAX = 120;

// --- Recevabilité ------------------------------------------------------------

/** Libellé court du motif de recevabilité rendu par le serveur. */
export const LIBELLE_MOTIF = Object.freeze({
  ok: "Photo exploitable",
  flou: "Photo floue",
  sous_expose: "Photo trop sombre",
  sur_expose: "Photo éblouie",
  trop_petite: "Photo trop petite",
  format_refuse: "Fichier non reconnu",
});

// Conseils de repli : le serveur en renvoie toujours un, ceux-ci ne servent qu'au
// cas — anormal — d'une réponse sans conseil. Français simple, pas de jargon.
const CONSEIL_DEFAUT = Object.freeze({
  ok: "Photo exploitable.",
  flou: "Approchez-vous, tenez le téléphone immobile, et refaites la photo.",
  sous_expose: "Sortez de l'ombre, puis refaites la photo.",
  sur_expose: "Tournez-vous dos au soleil, puis refaites la photo.",
  trop_petite: "Utilisez l'appareil photo du téléphone, pas une capture d'écran.",
  format_refuse: "Envoyez une photo prise avec le téléphone (JPEG ou PNG).",
});

/**
 * Libellé d'un motif de recevabilité, sûr même si le serveur en ajoute un nouveau.
 * @param {string} motif
 */
export function libelleMotif(motif) {
  return LIBELLE_MOTIF[motif] || "Photo non retenue";
}

/**
 * Conseil de reprise à afficher : celui du serveur d'abord, un repli sinon.
 * @param {{motif: string, conseil: string}} recevabilite
 */
export function conseilRecevabilite(recevabilite) {
  const conseil = recevabilite && typeof recevabilite.conseil === "string" ? recevabilite.conseil : "";
  return conseil.trim() || CONSEIL_DEFAUT[recevabilite?.motif] || "";
}

// --- Géométrie ---------------------------------------------------------------

// Rayon moyen de la Terre (IUGG), en mètres — même valeur que services/geometrie.py.
const RAYON_TERRE_M = 6_371_008.8;

/**
 * Distance entre deux points, en mètres, par projection équirectangulaire locale.
 * À l'échelle d'une parcelle l'erreur est négligeable, et l'on évite l'absurdité
 * d'une comparaison en degrés bruts (un degré de longitude ne vaut pas un degré
 * de latitude à 6° de latitude nord).
 * @param {{latitude: number, longitude: number}} a
 * @param {{latitude: number, longitude: number}} b
 * @returns {number} distance en mètres
 */
export function distanceM(a, b) {
  const facteur = (Math.PI / 180) * RAYON_TERRE_M;
  const latMoyenne = ((a.latitude + b.latitude) / 2) * (Math.PI / 180);
  const dx = (b.longitude - a.longitude) * facteur * Math.cos(latMoyenne);
  const dy = (b.latitude - a.latitude) * facteur;
  return Math.sqrt(dx * dx + dy * dy);
}

/**
 * Un point apporte-t-il de l'information par rapport au précédent retenu ?
 * @param {{latitude: number, longitude: number}|null|undefined} dernier
 * @param {{latitude: number, longitude: number}} candidat
 */
export function pointApporteInfo(dernier, candidat) {
  if (!dernier) return true;
  return distanceM(dernier, candidat) >= DISTANCE_MIN_M;
}

// --- Normalisation des réponses ----------------------------------------------

const nombre = (valeur, defaut = 0) => (typeof valeur === "number" && isFinite(valeur) ? valeur : defaut);
const texte = (valeur) => (typeof valeur === "string" ? valeur : "");

/** Normalise un point géographique rendu par l'API. */
export function versCoordonnee(brut) {
  if (!brut || typeof brut !== "object") return null;
  return Object.freeze({
    latitude: nombre(brut.latitude),
    longitude: nombre(brut.longitude),
    precisionM: typeof brut.precision_m === "number" ? brut.precision_m : null,
    horodatage: texte(brut.horodatage),
  });
}

/** Normalise la géométrie d'une parcelle (contour et superficie calculée). */
export function versGeometrie(brut) {
  if (!brut || typeof brut !== "object") return null;
  const points = Array.isArray(brut.points) ? brut.points.map(versCoordonnee).filter(Boolean) : [];
  return Object.freeze({
    type: texte(brut.type),
    source: texte(brut.source),
    points,
    superficieHa: typeof brut.superficie_ha === "number" ? brut.superficie_ha : null,
  });
}

/** Normalise une parcelle rendue par l'API. */
export function versParcelle(brut) {
  return Object.freeze({
    identifiant: texte(brut?.identifiant),
    nom: texte(brut?.nom),
    localite: texte(brut?.localite),
    directionRegionale: texte(brut?.direction_regionale),
    creeLe: texte(brut?.cree_le),
    majLe: texte(brut?.maj_le),
    geometrie: versGeometrie(brut?.geometrie),
  });
}

/** Normalise le verdict de recevabilité d'une image. */
export function versRecevabilite(brut) {
  return Object.freeze({
    recevable: Boolean(brut?.recevable),
    motif: texte(brut?.motif),
    conseil: texte(brut?.conseil),
    scoreNettete: nombre(brut?.score_nettete),
  });
}

/** Normalise une image persistée d'une capture. */
export function versImageCapture(brut) {
  return Object.freeze({
    empreinte: texte(brut?.empreinte_sha256),
    largeur: nombre(brut?.largeur),
    hauteur: nombre(brut?.hauteur),
    recevabilite: versRecevabilite(brut?.recevabilite),
    coordonnee: versCoordonnee(brut?.coordonnee),
  });
}

/** Normalise une capture rendue par l'API. */
export function versCapture(brut) {
  return Object.freeze({
    identifiant: texte(brut?.identifiant),
    parcelle: texte(brut?.parcelle),
    modalite: texte(brut?.modalite),
    creeLe: texte(brut?.cree_le),
    images: Array.isArray(brut?.images) ? brut.images.map(versImageCapture) : [],
    trace: Array.isArray(brut?.trace) ? brut.trace.map(versCoordonnee).filter(Boolean) : [],
  });
}
