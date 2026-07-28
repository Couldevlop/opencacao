// Couche INFRASTRUCTURE — capteurs et décodage d'image du navigateur.
// SEUL module qui connaît canvas, video, MediaRecorder et navigator.geolocation.
// Il rend au reste de l'application des objets déjà prêts pour l'API (base64 +
// métriques) : ni la couche application ni la couche domaine ne voient un pixel.
//
// Pourquoi le travail se fait ici plutôt que sur le serveur : le navigateur
// possède déjà les pixels décodés, alors que l'API n'a aucun décodeur d'image
// (aucune dépendance nouvelle n'est autorisée). Il calcule donc la netteté et
// l'exposition, redimensionne, et n'envoie que ce qui est utile.

import {
  INTERVALLE_ECHANTILLON_S,
  IMAGES_MAX,
  LARGEUR_MAX_PX,
  PRECISION_DECLARABLE_MAX_M,
  PRECISION_MAX_M,
  QUALITE_JPEG,
  SCORE_NETTETE_MAX,
} from "../domain/parcelle.js";

// Un seek qui ne rend jamais la main bloquerait tout l'écran : on borne l'attente.
const DELAI_SEEK_MS = 6000;
const DELAI_POSITION_MS = 15000;

/**
 * Netteté par variance du laplacien, calculée sur les pixels que le navigateur
 * possède déjà. Une photo floue de cabosse ne permet aucun constat : on la refuse
 * avant de consommer la bande passante.
 * @param {ImageData} imageData
 * @returns {number} variance du laplacien (plus c'est haut, plus c'est net)
 */
export function scoreNettete(imageData) {
  const { data, width, height } = imageData;
  const gris = new Float32Array(width * height);
  for (let i = 0; i < gris.length; i++) {
    const p = i * 4;
    gris[i] = 0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2];
  }
  let somme = 0;
  let sommeCarres = 0;
  let nombre = 0;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const i = y * width + x;
      const laplacien =
        -4 * gris[i] + gris[i - 1] + gris[i + 1] + gris[i - width] + gris[i + width];
      somme += laplacien;
      sommeCarres += laplacien * laplacien;
      nombre++;
    }
  }
  if (nombre === 0) return 0;
  const moyenne = somme / nombre;
  return sommeCarres / nombre - moyenne * moyenne;
}

/**
 * Luminance moyenne (0-255) : détecte le contre-jour et le sous-bois trop sombre,
 * les deux défauts dominants en plantation.
 * @param {ImageData} imageData
 */
export function luminanceMoyenne(imageData) {
  const { data } = imageData;
  if (data.length === 0) return 0;
  let somme = 0;
  for (let p = 0; p < data.length; p += 4) {
    somme += 0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2];
  }
  return somme / (data.length / 4);
}

/** Borne une métrique dans l'intervalle accepté par l'API (sinon : 422). */
function borner(valeur, min, max) {
  if (!isFinite(valeur)) return min;
  return Math.min(max, Math.max(min, valeur));
}

/** Encode des octets en base64 par tranches (éviter un dépassement de pile). */
function enBase64(octets) {
  const TRANCHE = 0x8000;
  let binaire = "";
  for (let i = 0; i < octets.length; i += TRANCHE) {
    binaire += String.fromCharCode.apply(null, octets.subarray(i, i + TRANCHE));
  }
  return btoa(binaire);
}

/**
 * Dessine une source (image ou vidéo) dans un canvas d'au plus LARGEUR_MAX_PX de
 * large. Ce redimensionnement n'est pas un confort : sans lui, une photo de
 * téléphone de 4000 px encodée en base64 dépasse à elle seule le plafond de corps
 * de requête de l'API (8 Mio).
 */
function canvasRedimensionne(source, largeurSource, hauteurSource) {
  const echelle = Math.min(1, LARGEUR_MAX_PX / Math.max(1, largeurSource));
  const largeur = Math.max(1, Math.round(largeurSource * echelle));
  const hauteur = Math.max(1, Math.round(hauteurSource * echelle));
  const canvas = document.createElement("canvas");
  canvas.width = largeur;
  canvas.height = hauteur;
  const contexte = canvas.getContext("2d", { willReadFrequently: true });
  if (!contexte) throw new Error("Le navigateur ne sait pas traiter les images.");
  contexte.drawImage(source, 0, 0, largeur, hauteur);
  return { canvas, contexte };
}

/**
 * Assemble l'objet d'image attendu par l'API depuis un canvas déjà dessiné :
 * JPEG qualité 0,85 encodé en base64, dimensions réelles et métriques mesurées.
 */
async function depuisCanvas(canvas, contexte) {
  const donnees = contexte.getImageData(0, 0, canvas.width, canvas.height);
  const blob = await new Promise((ok) => canvas.toBlob(ok, "image/jpeg", QUALITE_JPEG));
  if (!blob) throw new Error("Image illisible.");
  const tampon = await blob.arrayBuffer();
  return {
    contenu_base64: enBase64(new Uint8Array(tampon)),
    largeur: canvas.width,
    hauteur: canvas.height,
    score_nettete: borner(scoreNettete(donnees), 0, SCORE_NETTETE_MAX),
    luminance_moyenne: borner(luminanceMoyenne(donnees), 0, 255),
  };
}

/** Décode un fichier image, avec repli sur <img> si createImageBitmap manque. */
async function decoderImage(fichier) {
  if (typeof createImageBitmap === "function") {
    try {
      const bitmap = await createImageBitmap(fichier);
      return {
        source: bitmap,
        largeur: bitmap.width,
        hauteur: bitmap.height,
        liberer: () => bitmap.close && bitmap.close(),
      };
    } catch {
      /* format non géré par createImageBitmap : repli ci-dessous */
    }
  }
  const url = URL.createObjectURL(fichier);
  const img = new Image();
  img.decoding = "async";
  try {
    await new Promise((ok, ko) => {
      img.onload = ok;
      img.onerror = () => ko(new Error("Photo illisible."));
      img.src = url;
    });
  } catch (e) {
    URL.revokeObjectURL(url);
    throw e;
  }
  return {
    source: img,
    largeur: img.naturalWidth,
    hauteur: img.naturalHeight,
    liberer: () => URL.revokeObjectURL(url),
  };
}

/**
 * Prépare une photo choisie ou prise par le producteur : redimensionnement à
 * 1024 px maximum, JPEG 0,85, métriques de netteté et d'exposition.
 * @param {File|Blob} fichier
 * @returns {Promise<object>} image prête pour l'API
 */
export async function imageDepuisFichier(fichier) {
  const decodee = await decoderImage(fichier);
  try {
    const { canvas, contexte } = canvasRedimensionne(
      decodee.source,
      decodee.largeur,
      decodee.hauteur
    );
    return await depuisCanvas(canvas, contexte);
  } finally {
    decodee.liberer();
  }
}

/** Attend un événement de la vidéo, sans jamais bloquer indéfiniment. */
function attendre(video, evenement, delaiMs) {
  return new Promise((ok, ko) => {
    const minuteur = setTimeout(() => {
      nettoyer();
      ko(new Error("Vidéo illisible."));
    }, delaiMs);
    function nettoyer() {
      clearTimeout(minuteur);
      video.removeEventListener(evenement, surEvenement);
      video.removeEventListener("error", surErreur);
    }
    function surEvenement() {
      nettoyer();
      ok();
    }
    function surErreur() {
      nettoyer();
      ko(new Error("Vidéo illisible."));
    }
    video.addEventListener(evenement, surEvenement);
    video.addEventListener("error", surErreur);
  });
}

/**
 * Échantillonne une vidéo en images fixes, SUR L'APPAREIL : une image toutes les
 * deux secondes, douze au maximum. Téléverser une vidéo de 100 Mo sur un réseau
 * mobile ivoirien échouerait — douze vues suffisent au constat.
 * @param {File|Blob} fichier
 * @param {{onProgression?: (fait: number, total: number) => void}} options
 * @returns {Promise<object[]>} images prêtes pour l'API
 */
export async function echantillonnerVideo(fichier, { onProgression } = {}) {
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  video.src = URL.createObjectURL(fichier);
  const images = [];
  try {
    await attendre(video, "loadedmetadata", DELAI_SEEK_MS);
    const duree = isFinite(video.duration) ? video.duration : 0;
    const total = Math.max(1, Math.min(IMAGES_MAX, Math.floor(duree / INTERVALLE_ECHANTILLON_S) || 1));
    for (let indice = 0; indice < total; indice++) {
      const instant = indice * INTERVALLE_ECHANTILLON_S;
      if (duree && instant >= duree) break;
      video.currentTime = instant;
      try {
        await attendre(video, "seeked", DELAI_SEEK_MS);
      } catch {
        break; // le téléphone ne sait pas se positionner plus loin : on garde l'acquis
      }
      const { canvas, contexte } = canvasRedimensionne(
        video,
        video.videoWidth,
        video.videoHeight
      );
      images.push(await depuisCanvas(canvas, contexte));
      if (onProgression) onProgression(images.length, total);
    }
  } finally {
    URL.revokeObjectURL(video.src);
    video.removeAttribute("src");
  }
  if (images.length === 0) throw new Error("Aucune image n'a pu être extraite de la vidéo.");
  return images;
}

/** La géolocalisation est-elle exposée par ce navigateur ? */
export function geolocalisationDisponible() {
  return Boolean(navigator.geolocation && typeof navigator.geolocation.watchPosition === "function");
}

/** Convertit une position du navigateur en point conforme au schéma de l'API. */
function versPoint(position) {
  const { latitude, longitude, accuracy } = position.coords;
  const point = { latitude, longitude, horodatage: new Date(position.timestamp).toISOString() };
  // precision_m est borné à 10 000 côté API : une valeur aberrante ferait échouer
  // toute la capture, on préfère ne rien déclarer.
  if (typeof accuracy === "number" && accuracy >= 0 && accuracy <= PRECISION_DECLARABLE_MAX_M) {
    point.precision_m = accuracy;
  }
  return point;
}

/**
 * Démarre le relevé du contour de la parcelle. Ne garde que les points dont la
 * précision est acceptable ; le filtrage par distance relève de la couche
 * application (règle métier, pas capteur).
 * @param {(point: object) => void} surPoint
 * @param {(erreur: Error) => void} [surErreur] - appelé si la géolocalisation est
 *   refusée ou absente : à l'appelant de proposer la saisie manuelle, jamais un blocage.
 * @returns {number|null} identifiant de veille, ou null si la géolocalisation manque
 */
export function demarrerParcours(surPoint, surErreur) {
  if (!geolocalisationDisponible()) {
    if (surErreur) surErreur(new Error("La géolocalisation n'est pas disponible sur cet appareil."));
    return null;
  }
  return navigator.geolocation.watchPosition(
    (position) => {
      const { accuracy } = position.coords;
      if (typeof accuracy === "number" && accuracy > PRECISION_MAX_M) return;
      surPoint(versPoint(position));
    },
    (erreur) => {
      if (surErreur) surErreur(erreur);
    },
    { enableHighAccuracy: true, maximumAge: 0, timeout: DELAI_POSITION_MS }
  );
}

/** Arrête le relevé du contour. Sans effet si le parcours n'avait pas démarré. */
export function arreterParcours(veille) {
  if (veille === null || veille === undefined) return;
  if (!geolocalisationDisponible()) return;
  navigator.geolocation.clearWatch(veille);
}

/**
 * Position ponctuelle, au mieux : sert à géoréférencer des photos. Ne rejette
 * jamais — une photo sans coordonnée reste une photo utile.
 * @returns {Promise<object|null>}
 */
export function positionActuelle() {
  if (!geolocalisationDisponible()) return Promise.resolve(null);
  return new Promise((ok) => {
    navigator.geolocation.getCurrentPosition(
      (position) => ok(versPoint(position)),
      () => ok(null),
      { enableHighAccuracy: true, maximumAge: 30000, timeout: DELAI_POSITION_MS }
    );
  });
}
