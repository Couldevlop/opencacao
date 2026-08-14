// COMPOSITION ROOT de l'écran « Ma parcelle » — assemble les couches et relie la
// vue aux cas d'usage. Seul module de cet écran qui touche au DOM concret et qui
// injecte les dépendances (client API + capteurs -> cas d'usage -> vue).
// Aucune logique métier ici : elle vit dans application/parcelles.js.

import { creerCasUsageParcelles } from "./application/parcelles.js";
import { ConseilError, ErreurKind } from "./domain/models.js";
import { IMAGES_MAX, Modalite, POINTS_MIN_POLYGONE } from "./domain/parcelle.js";
import { creerClientApi } from "./infrastructure/api-client.js";
import * as media from "./infrastructure/capture-media.js";
import { creerVueParcelle } from "./ui/parcelle-view.js";

const CLE_API = "opencacao.apiUrl";
// Même défaut que l'écran de conversation : l'API sert souvent l'interface (zéro CORS).
const API_DEFAUT = window.location.protocol.startsWith("http")
  ? window.location.origin
  : "http://localhost:8080";

const $ = (id) => document.getElementById(id);
const refs = {
  formCreation: $("formCreation"),
  champNom: $("champNom"),
  champLocalite: $("champLocalite"),
  btnCreer: $("btnCreer"),
  listeParcelles: $("listeParcelles"),
  panneau: $("panneauCapture"),
  titreParcelle: $("titreParcelle"),
  metaParcelle: $("metaParcelle"),
  btnPhotos: $("btnPhotos"),
  btnVideo: $("btnVideo"),
  btnParcours: $("btnParcours"),
  btnParcoursVideo: $("btnParcoursVideo"),
  fichierPhotos: $("fichierPhotos"),
  fichierVideo: $("fichierVideo"),
  zoneParcours: $("zoneParcours"),
  parcoursEtat: $("parcoursEtat"),
  btnArreter: $("btnArreter"),
  zoneVideoTour: $("zoneVideoTour"),
  btnVideoTour: $("btnVideoTour"),
  geoAide: $("geoAide"),
  geoAideTexte: $("geoAideTexte"),
  btnSaisieManuelle: $("btnSaisieManuelle"),
  // « statutParcelle » et non « statut » : l'atelier portait le même identifiant, et
  // depuis que les trois écrans partagent une seule page, deux nœuds de même id n'en
  // laissent qu'un joignable — les messages de l'un seraient partis chez l'autre.
  statut: $("statutParcelle"),
  resultats: $("resultats"),
  settingsBtn: $("settingsBtn"),
  modal: $("modal"),
  apiUrl: $("apiUrl"),
  modalSave: $("modalSave"),
  modalCancel: $("modalCancel"),
};

let baseUrl = localStorage.getItem(CLE_API) || API_DEFAUT;
let parcelleActive = null;
let parcours = null;
// Modalité en cours quand un parcours est ouvert : détermine ce qu'on fait à l'arrêt.
let modaliteParcours = Modalite.PARCOURS;
// Modalité sous laquelle sera déposée la prochaine vidéo : « vidéo » seule, ou
// « parcours + vidéo » quand elle fait suite à un tour de parcelle.
let modaliteVideo = Modalite.VIDEO;
let occupe = false;

const client = creerClientApi(() => baseUrl);
const parcelles = creerCasUsageParcelles(client, media);

const MESSAGES_ERREUR = {
  [ErreurKind.VALIDATION]:
    "Ces informations ne sont pas valides. Vérifiez le nom, la localité et le tracé.",
  [ErreurKind.RATE_LIMIT]: "Trop d'envois à la suite. Patientez une minute, puis réessayez.",
  [ErreurKind.CHARGE_TROP_LOURDE]:
    "L'envoi est trop lourd pour le serveur. Recommencez avec moins de photos.",
  [ErreurKind.STOCKAGE_INSUFFISANT]:
    "Le serveur n'a plus assez de place pour enregistrer vos photos. Réessayez plus tard.",
  [ErreurKind.INTROUVABLE]: "Cette parcelle n'est plus disponible sur cet appareil.",
  [ErreurKind.INDISPONIBLE]:
    "Le service est momentanément indisponible. Réessayez dans un instant.",
  [ErreurKind.RESEAU]: "Service injoignable. Vérifiez votre connexion, puis réessayez.",
  [ErreurKind.HTTP]: "L'envoi n'a pas abouti. Réessayez dans un instant.",
};

// Refus dont le serveur rédige lui-même le message pour le producteur (géométrie
// hors Côte d'Ivoire, tracé qui se coupe, superficie aberrante, quota d'appareil) :
// on l'affiche tel quel, il est plus précis que toute formule générique.
const DETAIL_SERVEUR = new Set([
  ErreurKind.VALIDATION,
  ErreurKind.RATE_LIMIT,
  ErreurKind.STOCKAGE_INSUFFISANT,
  ErreurKind.INTROUVABLE,
]);

function messageErreur(e) {
  if (e instanceof ConseilError) {
    if (DETAIL_SERVEUR.has(e.kind) && e.message) return e.message;
    return MESSAGES_ERREUR[e.kind] || MESSAGES_ERREUR[ErreurKind.HTTP];
  }
  // Erreurs des capteurs (vidéo illisible, photo illisible) : déjà en français simple.
  return (e && e.message) || MESSAGES_ERREUR[ErreurKind.HTTP];
}

const vue = creerVueParcelle(refs, {
  onCreer: (donnees) => creerParcelle(donnees),
  onSelectionner: (id) => ouvrirParcelle(id),
  onPhotos: (fichiers) => envoyerPhotos(fichiers),
  onVideo: (fichier) => envoyerVideo(fichier),
  onDemandeVideo: () => {
    modaliteVideo = Modalite.VIDEO;
  },
  onDemandeVideoTour: () => {
    modaliteVideo = Modalite.PARCOURS_VIDEO;
  },
  onParcours: () => demarrerParcours(Modalite.PARCOURS),
  onParcoursVideo: () => demarrerParcours(Modalite.PARCOURS_VIDEO),
  onArreterParcours: () => arreterParcours(),
});

/** Verrouille les commandes pendant un traitement long (encodage, téléversement). */
function verrouiller(actif) {
  occupe = actif;
  vue.occupe(actif);
}

/* ---------- parcelles ---------- */

async function rafraichirListe() {
  const liste = await parcelles.lister();
  if (liste === null) {
    vue.statut(
      "Les parcelles ne sont pas activées sur ce serveur. Vous pouvez continuer à poser vos questions dans la conversation.",
      "err"
    );
    vue.occupe(true);
    return null;
  }
  vue.rendreListe(liste, parcelleActive ? parcelleActive.identifiant : null);
  return liste;
}

async function creerParcelle({ nom, localite }) {
  if (occupe) return;
  if (!nom || !localite) {
    vue.statut("Indiquez le nom de la parcelle et votre localité.", "err");
    return;
  }
  verrouiller(true);
  vue.statut("Enregistrement de la parcelle…");
  try {
    const parcelle = await parcelles.creer({ nom, localite });
    vue.viderCreation();
    parcelleActive = parcelle;
    vue.rendreParcelle(parcelle);
    vue.proposerVideoTour(false);
    vue.viderResultats();
    await rafraichirListe();
    vue.statut(
      "Parcelle enregistrée. Choisissez maintenant comment vous voulez la documenter.",
      "ok"
    );
  } catch (e) {
    vue.statut(messageErreur(e), "err");
  } finally {
    verrouiller(false);
  }
}

async function ouvrirParcelle(identifiant) {
  if (occupe) return;
  verrouiller(true);
  try {
    const parcelle = await parcelles.ouvrir(identifiant);
    if (!parcelle) {
      parcelleActive = null;
      vue.viderParcelle();
      await rafraichirListe();
      vue.statut("Cette parcelle n'existe plus.", "err");
      return;
    }
    parcelleActive = parcelle;
    vue.rendreParcelle(parcelle);
    vue.proposerVideoTour(false);
    vue.viderResultats();
    vue.statut("");
    await rafraichirListe();
  } catch (e) {
    vue.statut(messageErreur(e), "err");
  } finally {
    verrouiller(false);
  }
}

/** Recharge la parcelle active (superficie recalculée par le serveur). */
async function rafraichirParcelleActive() {
  if (!parcelleActive) return;
  try {
    const maj = await parcelles.ouvrir(parcelleActive.identifiant);
    if (maj) {
      parcelleActive = maj;
      vue.rendreParcelle(maj);
    }
    await rafraichirListe();
  } catch {
    /* affichage secondaire : on n'interrompt pas le producteur pour ça */
  }
}

/* ---------- captures par images ---------- */

/** Vérifie qu'une parcelle est choisie avant toute capture. */
function parcelleChoisie() {
  if (parcelleActive) return true;
  vue.statut("Choisissez d'abord une parcelle, ou créez-en une.", "err");
  return false;
}

async function envoyerPhotos(fichiers) {
  if (occupe || !parcelleChoisie()) return;
  verrouiller(true);
  try {
    if (fichiers.length > IMAGES_MAX) {
      vue.statut(`Seules les ${IMAGES_MAX} premières photos seront envoyées.`);
    }
    const images = await parcelles.preparerPhotos(fichiers, {
      onProgression: (fait, total) => vue.statut(`Préparation des photos… ${fait}/${total}`),
    });
    await deposerImages(Modalite.PHOTOS, images, []);
  } catch (e) {
    vue.statut(messageErreur(e), "err");
  } finally {
    verrouiller(false);
  }
}

async function envoyerVideo(fichier) {
  // Un sélecteur de fichier annulé n'émet aucun événement sur la plupart des
  // téléphones : la trace du parcours est donc déposée AVANT que la vidéo soit
  // demandée (voir arreterParcours). Rien n'est jamais suspendu à ce choix.
  const modalite = modaliteVideo;
  modaliteVideo = Modalite.VIDEO;
  if (!fichier || occupe || !parcelleChoisie()) return;
  verrouiller(true);
  try {
    vue.statut("Extraction des images de la vidéo…");
    const images = await parcelles.preparerVideo(fichier, {
      onProgression: (fait, total) => vue.statut(`Extraction des images… ${fait}/${total}`),
    });
    await deposerImages(modalite, images, []);
  } catch (e) {
    vue.statut(messageErreur(e), "err");
  } finally {
    verrouiller(false);
  }
}

/** Géoréférence, dépose et affiche les verdicts de recevabilité. */
async function deposerImages(modalite, images, trace) {
  vue.statut("Envoi en cours…");
  const geolocalisees = await parcelles.georeferencer(images);
  const capture = await parcelles.deposer(parcelleActive.identifiant, modalite, {
    images: geolocalisees,
    trace,
  });
  vue.rendreCapture(capture);
  const refusees = capture.images.filter((i) => !i.recevabilite.recevable).length;
  vue.statut(
    refusees === 0
      ? "Capture enregistrée. Toutes les photos sont exploitables."
      : `Capture enregistrée. ${refusees} photo(s) à refaire : suivez les conseils ci-dessous.`,
    refusees === 0 ? "ok" : "info"
  );
}

/* ---------- parcours du contour ---------- */

function demarrerParcours(modalite) {
  if (occupe || !parcelleChoisie()) return;
  if (parcours) return;
  vue.cacherAideGeo();
  vue.proposerVideoTour(false);
  modaliteParcours = modalite;
  parcours = parcelles.menerParcours({
    surPoint: (_point, total) => vue.majParcours(total, true),
    surErreur: (erreur) => aideGeolocalisation(erreur),
  });
  if (!parcours.demarre()) {
    parcours = null;
    return;
  }
  vue.majParcours(0, true);
  vue.statut(
    modalite === Modalite.PARCOURS_VIDEO
      ? "Faites le tour de la parcelle en filmant. Appuyez sur « Terminer » à la fin : la vidéo vous sera demandée."
      : "Faites le tour de la parcelle en marchant le long du contour, puis appuyez sur « Terminer »."
  );
}

/**
 * La géolocalisation est refusée ou indisponible : on l'explique et on renvoie
 * vers la saisie manuelle de la localité. Jamais de blocage — les photos, elles,
 * restent enregistrables sans coordonnées.
 */
function aideGeolocalisation(erreur) {
  const refus = erreur && erreur.code === 1;
  vue.montrerAideGeo(
    refus
      ? "Vous avez refusé la localisation. Le tour de la parcelle ne peut pas être relevé, mais vos photos seront enregistrées. Indiquez votre localité pour situer la parcelle."
      : "La localisation n'est pas disponible sur ce téléphone. Vos photos seront quand même enregistrées. Indiquez votre localité pour situer la parcelle."
  );
  if (parcours) {
    parcours.arreter();
    parcours = null;
    vue.majParcours(0, false);
  }
}

async function arreterParcours() {
  if (!parcours) return;
  const trace = parcours.arreter();
  const modalite = modaliteParcours;
  parcours = null;
  vue.majParcours(0, false);
  await terminerParcours(trace, modalite);
  if (modalite === Modalite.PARCOURS_VIDEO) {
    // Le parcours est déjà déposé : la vidéo devient une seconde capture, et son
    // éventuel abandon ne fait rien perdre du tour de parcelle. On ne peut pas
    // ouvrir le sélecteur nous-mêmes ici — l'envoi du parcours a consommé le geste
    // du producteur, et le navigateur refuserait d'ouvrir la caméra : on propose
    // donc un bouton, dont le clic ouvrira le sélecteur.
    vue.proposerVideoTour(true);
  }
}

/** Dépose la trace relevée, puis enregistre le contour si l'anneau est exploitable. */
async function terminerParcours(trace, modalite) {
  if (trace.length === 0) {
    vue.statut(
      "Aucun point n'a pu être relevé. Vérifiez que la localisation du téléphone est active, puis recommencez.",
      "err"
    );
    return;
  }
  verrouiller(true);
  try {
    vue.statut("Envoi du parcours…");
    const capture = await parcelles.deposer(parcelleActive.identifiant, modalite, { trace });
    vue.rendreCapture(capture);
    if (trace.length < POINTS_MIN_POLYGONE) {
      vue.statut(
        `Parcours enregistré (${trace.length} point(s)). Il en faut au moins ${POINTS_MIN_POLYGONE} pour calculer la superficie : refaites le tour complet.`,
        "info"
      );
      return;
    }
    await enregistrerContour(trace);
  } catch (e) {
    vue.statut(messageErreur(e), "err");
  } finally {
    verrouiller(false);
  }
}

/** Enregistre le contour ; le serveur valide la géographie et calcule la superficie. */
async function enregistrerContour(trace) {
  try {
    const parcelle = await parcelles.enregistrerContour(parcelleActive.identifiant, trace);
    parcelleActive = parcelle;
    vue.rendreParcelle(parcelle);
    await rafraichirListe();
    const superficie = parcelle.geometrie && parcelle.geometrie.superficieHa;
    vue.statut(
      typeof superficie === "number"
        ? `Contour enregistré. Superficie relevée : ${superficie.toFixed(2)} ha.`
        : "Contour enregistré.",
      "ok"
    );
  } catch (e) {
    // Un contour refusé (hors Côte d'Ivoire, tracé croisé, superficie aberrante)
    // n'annule pas la capture déjà déposée : on le dit sans tout perdre.
    vue.statut(messageErreur(e), "err");
    await rafraichirParcelleActive();
  }
}

/* ---------- paramètres API ---------- */

function ouvrirModale() {
  refs.apiUrl.value = baseUrl;
  refs.modal.hidden = false;
  refs.apiUrl.focus();
}
function fermerModale() {
  refs.modal.hidden = true;
}
refs.settingsBtn?.addEventListener("click", ouvrirModale);
refs.modalCancel?.addEventListener("click", fermerModale);
refs.modal?.addEventListener("click", (e) => {
  if (e.target === refs.modal) fermerModale();
});
refs.modalSave?.addEventListener("click", () => {
  const valeur = refs.apiUrl.value.trim();
  if (valeur) {
    baseUrl = valeur;
    localStorage.setItem(CLE_API, valeur);
    amorcer();
  }
  fermerModale();
});

/* ---------- repli logos (sans onerror inline, conforme CSP) ---------- */
document.querySelectorAll("img.logo").forEach((img) => {
  img.addEventListener("error", () => img.classList.add("logo-missing"));
});

/* ---------- amorçage ---------- */

async function amorcer() {
  try {
    await rafraichirListe();
  } catch (e) {
    vue.statut(messageErreur(e), "err");
  }
  if (!media.geolocalisationDisponible()) {
    vue.montrerAideGeo(
      "La localisation n'est pas disponible sur ce téléphone. Vous pouvez quand même enregistrer des photos : indiquez votre localité pour situer la parcelle."
    );
  }
}

amorcer();
