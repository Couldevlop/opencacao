// Couche APPLICATION — cas d'usage « ma parcelle » et captures terrain (V3, C1).
// Orchestration pure au-dessus de deux ports : le client API et le module de
// capture (capteurs du navigateur). Ne connaît NI le DOM, NI fetch, NI canvas.
//
// C'est ici que vivent les règles de composition d'une capture : plafonner à douze
// images, ne garder d'un parcours que les points qui apportent de l'information,
// borner la trace. Le serveur revalide tout — mais refuser au plus tôt, c'est de la
// bande passante économisée sur un réseau mobile faible.

import {
  IMAGES_MAX,
  Modalite,
  SourceGeometrie,
  TRACE_MAX_POINTS,
  pointApporteInfo,
} from "../domain/parcelle.js";

/**
 * @param {{
 *   creerParcelle: (donnees: object) => Promise<object>,
 *   listerParcelles: () => Promise<Array|null>,
 *   obtenirParcelle: (id: string) => Promise<object|null>,
 *   enregistrerGeometrie: (id: string, donnees: object) => Promise<object>,
 *   deposerCapture: (id: string, donnees: object) => Promise<object>,
 * }} client - adaptateur API (port).
 * @param {{
 *   imageDepuisFichier: (fichier: Blob) => Promise<object>,
 *   echantillonnerVideo: (fichier: Blob, options?: object) => Promise<object[]>,
 *   demarrerParcours: (surPoint: Function, surErreur?: Function) => number|null,
 *   arreterParcours: (veille: number|null) => void,
 *   positionActuelle: () => Promise<object|null>,
 * }} media - adaptateur capteurs (port).
 */
export function creerCasUsageParcelles(client, media) {
  /**
   * Ne garde d'une trace que les points qui apportent de l'information (au moins
   * cinq mètres depuis le précédent retenu), et la borne au plafond de l'API.
   * @param {Array<{latitude: number, longitude: number}>} points
   */
  function filtrerTrace(points) {
    const gardes = [];
    for (const point of points || []) {
      if (!pointApporteInfo(gardes[gardes.length - 1], point)) continue;
      gardes.push(point);
      if (gardes.length >= TRACE_MAX_POINTS) break;
    }
    return gardes;
  }

  /**
   * Mène un parcours du contour : accumule les points déjà filtrés au fil de l'eau.
   * Rend un objet de contrôle — l'appelant décide quand arrêter.
   * @param {{surPoint?: (point: object, total: number) => void, surErreur?: (e: Error) => void}} rappels
   */
  function menerParcours({ surPoint, surErreur } = {}) {
    const points = [];
    const veille = media.demarrerParcours((point) => {
      if (points.length >= TRACE_MAX_POINTS) return;
      if (!pointApporteInfo(points[points.length - 1], point)) return;
      points.push(point);
      if (surPoint) surPoint(point, points.length);
    }, surErreur);
    let arrete = false;
    return Object.freeze({
      /** Le relevé a-t-il pu démarrer (géolocalisation présente) ? */
      demarre: () => veille !== null && veille !== undefined,
      /** Points retenus jusqu'ici (copie). */
      points: () => points.slice(),
      /** Arrête le relevé et rend les points retenus. Idempotent. */
      arreter: () => {
        if (!arrete) {
          media.arreterParcours(veille);
          arrete = true;
        }
        return points.slice();
      },
    });
  }

  /**
   * Prépare des photos choisies ou prises par le producteur : au plus douze,
   * redimensionnées et mesurées par la couche infrastructure.
   * @param {Array<Blob>} fichiers
   * @param {{onProgression?: (fait: number, total: number) => void}} options
   */
  async function preparerPhotos(fichiers, { onProgression } = {}) {
    const retenus = Array.from(fichiers || []).slice(0, IMAGES_MAX);
    const images = [];
    for (const fichier of retenus) {
      images.push(await media.imageDepuisFichier(fichier));
      if (onProgression) onProgression(images.length, retenus.length);
    }
    return images;
  }

  /**
   * Échantillonne une vidéo en images fixes sur l'appareil (jamais de téléversement
   * de la vidéo : un réseau mobile ivoirien ne le supporterait pas).
   * @param {Blob} fichier
   * @param {{onProgression?: (fait: number, total: number) => void}} options
   */
  async function preparerVideo(fichier, options = {}) {
    const images = await media.echantillonnerVideo(fichier, options);
    return images.slice(0, IMAGES_MAX);
  }

  /**
   * Géoréférence des images avec la position courante, au mieux. Une photo sans
   * coordonnée reste une photo utile : jamais de blocage sur ce point.
   * @param {Array<object>} images
   */
  async function georeferencer(images) {
    if (images.length === 0) return images;
    const point = await media.positionActuelle();
    if (!point) return images;
    return images.map((image) => ({ ...image, coordonnee: point }));
  }

  /**
   * Assemble et dépose une capture : plafond de douze images, trace filtrée.
   * @param {string} identifiant - parcelle visée
   * @param {string} modalite - une valeur de `Modalite`
   * @param {{images?: Array<object>, trace?: Array<object>}} contenu
   */
  function deposer(identifiant, modalite, { images = [], trace = [] } = {}) {
    return client.deposerCapture(identifiant, {
      modalite,
      images: images.slice(0, IMAGES_MAX),
      trace: filtrerTrace(trace),
    });
  }

  /**
   * Enregistre le contour relevé au GPS, après filtrage des points redondants.
   * @param {string} identifiant
   * @param {Array<object>} points
   */
  function enregistrerContour(identifiant, points) {
    return client.enregistrerGeometrie(identifiant, {
      points: filtrerTrace(points),
      source: SourceGeometrie.PARCOURS_GPS,
    });
  }

  return Object.freeze({
    /** Liste les parcelles de l'appareil (null si le serveur ne les sert pas). */
    lister: () => client.listerParcelles(),
    /** Crée une parcelle (nom + localité déclarés par le producteur). */
    creer: (donnees) => client.creerParcelle(donnees),
    /** Ouvre une parcelle, ou null si elle n'existe plus. */
    ouvrir: (id) => client.obtenirParcelle(id),
    menerParcours,
    preparerPhotos,
    preparerVideo,
    georeferencer,
    deposer,
    enregistrerContour,
    filtrerTrace,
    Modalite,
  });
}
