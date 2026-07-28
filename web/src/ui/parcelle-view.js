// Couche PRÉSENTATION — écran « Ma parcelle » (V3, C1).
// Rend la liste des parcelles, les quatre modalités de capture et les verdicts de
// recevabilité ; émet des callbacks. Ne connaît ni fetch, ni capteur : reçoit des
// entités déjà normalisées par le domaine.
//
// OWASP A03 (injection) : AUCUN innerHTML. Le nom d'une parcelle, sa localité et
// les conseils de reprise viennent du serveur ou du producteur — ce sont des
// données non fiables, elles ne passent que par textContent et createElement.

import {
  LIBELLE_MODALITE,
  conseilRecevabilite,
  libelleMotif,
} from "../domain/parcelle.js";

const LISTE_VIDE = "Aucune parcelle enregistrée sur cet appareil.";

/**
 * @param {Record<string, HTMLElement>} refs - éléments de la page.
 * @param {{
 *   onCreer?: (donnees: {nom: string, localite: string}) => void,
 *   onSelectionner?: (id: string) => void,
 *   onPhotos?: (fichiers: FileList) => void,
 *   onVideo?: (fichier: File) => void,
 *   onDemandeVideo?: () => void,
 *   onDemandeVideoTour?: () => void,
 *   onParcours?: () => void,
 *   onParcoursVideo?: () => void,
 *   onArreterParcours?: () => void,
 * }} handlers
 */
export function creerVueParcelle(refs, handlers = {}) {
  const appeler = (nom, ...args) => {
    if (typeof handlers[nom] === "function") handlers[nom](...args);
  };

  /* ---------- formulaire de création ---------- */

  /** Valeurs saisies pour une nouvelle parcelle. */
  function lireCreation() {
    return {
      nom: (refs.champNom.value || "").trim(),
      localite: (refs.champLocalite.value || "").trim(),
    };
  }

  function viderCreation() {
    refs.champNom.value = "";
    refs.champLocalite.value = "";
  }

  /** Amène le producteur sur la saisie manuelle de la localité (repli sans GPS). */
  function focusLocalite() {
    refs.champLocalite.focus();
    refs.champLocalite.scrollIntoView({ block: "center" });
  }

  /* ---------- liste des parcelles ---------- */

  function elementParcelle(parcelle, idActif) {
    const li = document.createElement("li");
    li.className = "parcelle-item" + (parcelle.identifiant === idActif ? " actif" : "");

    const bouton = document.createElement("button");
    bouton.type = "button";
    bouton.className = "parcelle-ouvrir";
    if (parcelle.identifiant === idActif) bouton.setAttribute("aria-current", "true");

    const nom = document.createElement("span");
    nom.className = "parcelle-nom";
    nom.textContent = parcelle.nom; // donnée producteur => texte, jamais du HTML

    const detail = document.createElement("span");
    detail.className = "parcelle-detail";
    const superficie =
      parcelle.geometrie && typeof parcelle.geometrie.superficieHa === "number"
        ? ` · ${parcelle.geometrie.superficieHa.toFixed(2)} ha`
        : "";
    detail.textContent = parcelle.localite + superficie;

    bouton.append(nom, detail);
    bouton.addEventListener("click", () => appeler("onSelectionner", parcelle.identifiant));
    li.appendChild(bouton);
    return li;
  }

  /**
   * Rend la liste des parcelles de l'appareil.
   * @param {Array} parcelles
   * @param {string|null} idActif
   */
  function rendreListe(parcelles, idActif) {
    refs.listeParcelles.textContent = "";
    if (!parcelles || parcelles.length === 0) {
      const vide = document.createElement("li");
      vide.className = "parcelle-vide";
      vide.textContent = LISTE_VIDE;
      refs.listeParcelles.appendChild(vide);
      return;
    }
    const fragment = document.createDocumentFragment();
    parcelles.forEach((p) => fragment.appendChild(elementParcelle(p, idActif)));
    refs.listeParcelles.appendChild(fragment);
  }

  /* ---------- parcelle active ---------- */

  function ligneMeta(libelle, valeur) {
    const div = document.createElement("div");
    div.className = "meta-ligne";
    const cle = document.createElement("span");
    cle.className = "meta-cle";
    cle.textContent = libelle;
    const val = document.createElement("span");
    val.className = "meta-valeur";
    val.textContent = valeur;
    div.append(cle, val);
    return div;
  }

  /** Affiche la parcelle choisie et ouvre les modalités de capture. */
  function rendreParcelle(parcelle) {
    refs.panneau.hidden = false;
    refs.titreParcelle.textContent = parcelle.nom;
    refs.metaParcelle.textContent = "";
    const fragment = document.createDocumentFragment();
    fragment.appendChild(ligneMeta("Localité", parcelle.localite || "non précisée"));
    fragment.appendChild(
      ligneMeta(
        "Direction régionale ANADER",
        parcelle.directionRegionale || "à confirmer avec votre agent"
      )
    );
    if (parcelle.geometrie && typeof parcelle.geometrie.superficieHa === "number") {
      fragment.appendChild(
        ligneMeta("Superficie relevée", parcelle.geometrie.superficieHa.toFixed(2) + " ha")
      );
      fragment.appendChild(
        ligneMeta("Points du contour", String(parcelle.geometrie.points.length))
      );
    } else {
      fragment.appendChild(ligneMeta("Contour", "pas encore relevé"));
    }
    refs.metaParcelle.appendChild(fragment);
  }

  /** Referme le panneau de capture (aucune parcelle choisie). */
  function viderParcelle() {
    refs.panneau.hidden = true;
    refs.metaParcelle.textContent = "";
    proposerVideoTour(false);
    viderResultats();
  }

  /* ---------- verdicts de recevabilité ---------- */

  function elementImage(image, indice) {
    const li = document.createElement("li");
    li.className = "verdict" + (image.recevabilite.recevable ? " ok" : " refus");

    const entete = document.createElement("div");
    entete.className = "verdict-entete";

    const numero = document.createElement("span");
    numero.className = "verdict-numero";
    numero.textContent = "Vue " + indice;

    const etat = document.createElement("span");
    etat.className = "verdict-etat";
    etat.textContent =
      (image.recevabilite.recevable ? "✓ " : "✕ ") + libelleMotif(image.recevabilite.motif);

    entete.append(numero, etat);

    const conseil = document.createElement("p");
    conseil.className = "verdict-conseil";
    conseil.textContent = conseilRecevabilite(image.recevabilite);

    li.append(entete, conseil);
    return li;
  }

  /** Affiche le résultat d'une capture : une ligne de verdict par image. */
  function rendreCapture(capture) {
    refs.resultats.textContent = "";
    const titre = document.createElement("h3");
    titre.className = "resultats-titre";
    const modalite = LIBELLE_MODALITE[capture.modalite] || "Capture";
    const retenues = capture.images.filter((i) => i.recevabilite.recevable).length;
    titre.textContent =
      capture.images.length > 0
        ? `${modalite} — ${retenues} photo(s) retenue(s) sur ${capture.images.length}`
        : `${modalite} — ${capture.trace.length} point(s) enregistré(s)`;
    refs.resultats.appendChild(titre);

    if (capture.images.length > 0) {
      const liste = document.createElement("ul");
      liste.className = "verdicts";
      capture.images.forEach((image, i) => liste.appendChild(elementImage(image, i + 1)));
      refs.resultats.appendChild(liste);
    }
  }

  function viderResultats() {
    refs.resultats.textContent = "";
  }

  /* ---------- statut, progression, parcours ---------- */

  /**
   * Message d'état unique, lu par les lecteurs d'écran (aria-live sur l'élément).
   * @param {string} texte
   * @param {"info"|"ok"|"err"} [type]
   */
  function statut(texte, type = "info") {
    refs.statut.textContent = texte || "";
    refs.statut.className = "statut " + type;
    refs.statut.hidden = !texte;
  }

  /** Met à jour l'état visible du relevé de contour. */
  function majParcours(nombrePoints, actif) {
    refs.zoneParcours.hidden = !actif;
    refs.parcoursEtat.textContent = actif
      ? `Relevé en cours — ${nombrePoints} point(s) retenu(s). Marchez le long du contour.`
      : "";
  }

  /**
   * Affiche (ou retire) l'invitation à joindre la vidéo du tour. Second temps de la
   * modalité « Parcours + vidéo » : le sélecteur ne peut s'ouvrir que sur un geste
   * du producteur, jamais tout seul après l'envoi du parcours.
   */
  function proposerVideoTour(actif) {
    if (refs.zoneVideoTour) refs.zoneVideoTour.hidden = !actif;
  }

  /** Désactive les commandes pendant un traitement long (encodage, envoi). */
  function occupe(enCours) {
    [
      refs.btnPhotos,
      refs.btnVideo,
      refs.btnParcours,
      refs.btnParcoursVideo,
      refs.btnVideoTour,
      refs.btnCreer,
    ]
      .filter(Boolean)
      .forEach((b) => {
        b.disabled = enCours;
      });
  }

  /**
   * Repli quand la géolocalisation est refusée ou absente : on explique, on oriente
   * vers la saisie manuelle de la localité — jamais un blocage.
   */
  function montrerAideGeo(message) {
    refs.geoAide.hidden = false;
    refs.geoAideTexte.textContent = message;
  }

  function cacherAideGeo() {
    refs.geoAide.hidden = true;
  }

  /* ---------- ouverture des sélecteurs de fichier ---------- */

  /** Ouvre l'appareil photo (ou la galerie) du téléphone. */
  function demanderPhotos() {
    refs.fichierPhotos.value = "";
    refs.fichierPhotos.click();
  }

  /** Ouvre la caméra vidéo (ou la galerie) du téléphone. */
  function demanderVideo() {
    refs.fichierVideo.value = "";
    refs.fichierVideo.click();
  }

  /* ---------- câblage des événements ---------- */

  refs.formCreation?.addEventListener("submit", (e) => {
    e.preventDefault();
    appeler("onCreer", lireCreation());
  });
  refs.btnPhotos?.addEventListener("click", demanderPhotos);
  refs.btnVideo?.addEventListener("click", () => {
    // Signale l'origine du choix : une vidéo demandée ici est une capture « vidéo »
    // seule, pas la suite d'un parcours.
    appeler("onDemandeVideo");
    demanderVideo();
  });
  refs.btnVideoTour?.addEventListener("click", () => {
    proposerVideoTour(false);
    appeler("onDemandeVideoTour");
    demanderVideo(); // ouverture synchrone : le geste du producteur est encore valide
  });
  refs.btnParcours?.addEventListener("click", () => appeler("onParcours"));
  refs.btnParcoursVideo?.addEventListener("click", () => appeler("onParcoursVideo"));
  refs.btnArreter?.addEventListener("click", () => appeler("onArreterParcours"));
  refs.fichierPhotos?.addEventListener("change", () => {
    const fichiers = refs.fichierPhotos.files;
    if (fichiers && fichiers.length > 0) appeler("onPhotos", fichiers);
  });
  refs.fichierVideo?.addEventListener("change", () => {
    const fichier = refs.fichierVideo.files && refs.fichierVideo.files[0];
    appeler("onVideo", fichier || null);
  });
  refs.btnSaisieManuelle?.addEventListener("click", () => {
    cacherAideGeo();
    focusLocalite();
  });

  return Object.freeze({
    lireCreation,
    viderCreation,
    focusLocalite,
    rendreListe,
    rendreParcelle,
    viderParcelle,
    rendreCapture,
    viderResultats,
    statut,
    majParcours,
    proposerVideoTour,
    occupe,
    montrerAideGeo,
    cacherAideGeo,
    demanderPhotos,
    demanderVideo,
  });
}
