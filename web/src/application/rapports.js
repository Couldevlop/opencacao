/**
 * Orchestration de l'atelier de livrables. Aucun DOM : le client d'API est injecté,
 * ce qui rend ce module testable sans navigateur.
 *
 * Le rôle est mince et c'est voulu : créer un job, suivre son flux en réduisant chaque
 * événement sur le sommaire, et notifier la vue à chaque état. La logique de transition
 * vit dans le domaine ; ici on ne fait que l'enchaîner.
 */

import {
  EtatProduction,
  appliquerEvenement,
  demarrer,
  sommaireDepuisPlan,
} from "../domain/rapport.js";

/** Dit si un sommaire a conclu, d'une façon ou d'une autre. */
const termine = (sommaire) =>
  sommaire.etat === EtatProduction.TERMINE || sommaire.etat === EtatProduction.ECHOUE;

/**
 * Crée le service de l'atelier.
 *
 * @param {object} client Client d'API (listerGabarits, creerRapport, suivreRapport…).
 * @returns {object} Le service, figé.
 */
export function creerAtelier(client) {
  /**
   * Charge les gabarits disponibles.
   *
   * @returns {Promise<Array|null>} Les gabarits, ou `null` si l'atelier est absent du
   *   serveur — l'écran doit pouvoir le dire au lieu d'afficher une liste vide.
   */
  async function chargerGabarits() {
    return client.listerGabarits();
  }

  /**
   * Demande au serveur ce qu'il comprend d'une phrase.
   *
   * @param {string} demande Ce que la personne a écrit.
   * @returns {Promise<object>} L'intention comprise.
   */
  async function comprendre(demande) {
    return client.comprendreDemande(demande);
  }

  /**
   * Produit un document et rend l'état du sommaire à chaque avancée.
   *
   * @param {{gabarit: object, sujet: string, onEtat: (sommaire: object) => void}} demande
   *   `gabarit` porte son plan (`sections`), ce qui permet d'afficher le sommaire
   *   AVANT la première génération.
   * @returns {Promise<{identifiant: string, sommaire: object}>} L'état final.
   */
  async function produire({ gabarit, sujet, pages, onEtat }) {
    const notifier = typeof onEtat === "function" ? onEtat : () => {};

    // Le plan s'affiche d'emblée : le lecteur voit où va le document avant qu'une
    // seule ligne ne soit écrite.
    let sommaire = sommaireDepuisPlan(gabarit.sections);
    notifier(sommaire);

    const rapport = await client.creerRapport({
      gabarit: gabarit.identifiant,
      sujet,
      // Toujours un nombre : laisser passer `undefined` ferait varier le corps de la
      // requête selon le chemin d'appel, et le contrat avec le serveur avec lui.
      pages: Number(pages) || 0,
    });

    sommaire = demarrer(sommaire);
    notifier(sommaire);

    await client.suivreRapport(rapport.identifiant, (evenement) => {
      const suivant = appliquerEvenement(sommaire, evenement);
      // On ne notifie que si l'état a réellement changé : un événement inconnu ne doit
      // pas provoquer un rendu inutile.
      if (suivant !== sommaire) {
        sommaire = suivant;
        notifier(sommaire);
      }
    });

    // Le flux peut se fermer proprement SANS conclure : pod évincé, inférence tuée par
    // l'OOM, coupure du répartiteur. Sans ce garde-fou l'écran reste indéfiniment sur
    // « 2 sections sur 6 », sans erreur, pour un document qui n'arrivera jamais. Le
    // silence est ici pire qu'un échec annoncé.
    if (!termine(sommaire)) {
      sommaire = appliquerEvenement(sommaire, {
        type: "error",
        message: "Le flux s'est interrompu avant la fin du document.",
      });
      notifier(sommaire);
    }

    return { identifiant: rapport.identifiant, sommaire };
  }

  /** Liste les documents de cet appareil. */
  async function historique() {
    return client.listerRapports();
  }

  /** Télécharge un document et rend `{blob, nom}`. */
  async function telecharger(identifiant, format) {
    return client.exporterRapport(identifiant, format);
  }

  return Object.freeze({ chargerGabarits, comprendre, produire, historique, telecharger });
}

// Plafond du sujet, aligné sur celui du serveur (`CreerRapportRequest.sujet`). Il vit
// ici et pas dans l'écran : les deux voies — sujet compris par le serveur, sujet dicté
// ensuite par la personne — doivent le respecter, et une seule des deux le faisait.
export const SUJET_MAX = 200;

/** Ramène un texte à un sujet présentable et borné. */
const borner = (texte) => (texte || "").trim().slice(0, SUJET_MAX);

/**
 * Longueur visée d'un extrait de section affiché à l'écran. Assez pour savoir de quoi
 * la section parle, trop court pour noyer le lecteur.
 */
export const EXTRAIT_MAX = 320;

/**
 * Aperçu d'une section, pour l'ÉCRAN. L'export, lui, reste complet.
 *
 * L'atelier déversait la prose entière au fil de l'eau : une étude de vingt pages y
 * devenait illisible, et ce n'est pas ainsi que procèdent les autres assistants — ils
 * montrent un aperçu et proposent le document. Le texte intégral n'est jamais perdu,
 * il n'est simplement pas déversé.
 *
 * La coupe se fait à une frontière de PHRASE quand c'est possible, à défaut sur un
 * espace : un extrait qui s'arrête en plein mot donne l'impression d'un texte tronqué
 * par erreur, ce qui est précisément l'effet qu'on veut éviter.
 *
 * @param {string} texte Prose complète de la section.
 * @returns {string} L'extrait, terminé par une ellipse s'il a été coupé.
 */
export function extrait(texte) {
  const prose = (texte || "").trim();
  if (prose.length <= EXTRAIT_MAX) return prose;

  const debut = prose.slice(0, EXTRAIT_MAX);
  const finDePhrase = Math.max(
    debut.lastIndexOf(". "),
    debut.lastIndexOf("! "),
    debut.lastIndexOf("? ")
  );
  // Une frontière de phrase trop précoce donnerait un extrait d'une ligne là où on en
  // voulait trois : en dessous de la moitié, on préfère couper sur un espace.
  if (finDePhrase > EXTRAIT_MAX / 2) return `${debut.slice(0, finDePhrase + 1)} …`;

  const espace = debut.lastIndexOf(" ");
  return `${(espace > 0 ? debut.slice(0, espace) : debut).trimEnd()} …`;
}

/** Ce que l'écran doit faire d'une intention. */
export const Suite = Object.freeze({
  PRODUIRE: "produire",
  CHOISIR: "choisir",
  PRECISER: "preciser",
  IMPOSSIBLE: "impossible",
});

/**
 * Suite à donner quand le type est déjà acquis et que la phrase saisie EST le sujet.
 *
 * Cette règle vivait dans l'écran, avec sa propre troncature — que l'autre voie
 * n'appliquait pas. Elle est ici pour être unique et testée.
 *
 * @param {object} gabarit Type de document déjà retenu.
 * @param {string} texte Phrase saisie, tenue pour le sujet.
 * @returns {object} La décision, figée.
 */
export function suiteDirecte(gabarit, texte, pages = 0) {
  const sujet = borner(texte);
  return Object.freeze({
    suite: sujet ? Suite.PRODUIRE : Suite.PRECISER,
    gabarit,
    sujet,
    candidats: [],
    pages,
  });
}

/**
 * Décide de la suite à donner à une intention comprise.
 *
 * Fonction pure, et volontairement aveugle au drapeau `certaine` du serveur : ce qui
 * décide, c'est ce qu'on a en main. Un type inconnu de cet écran — serveur plus
 * récent — retombe ainsi sur un choix au lieu de produire un document au hasard.
 *
 * @param {object} intention Réponse de `/v1/rapports/intention`.
 * @param {Array} catalogue Gabarits connus de cet écran, plan compris.
 * @returns {object} `{suite, gabarit, sujet, candidats}`, figé.
 */
export function deciderSuite(intention, catalogue) {
  const connus = catalogue || [];
  const sujet = borner(intention?.sujet);
  // Ampleur lue par le serveur. L'écran ne l'interprète pas : il la transporte
  // jusqu'à la création, où le moteur décidera de ce qu'il peut réellement produire.
  const pages = Number(intention?.pages) || 0;
  const gabarit = connus.find((g) => g.identifiant === intention?.gabarit) || null;

  if (!gabarit) {
    // Les candidats du serveur portent déjà leur plan ; à défaut, tout le catalogue.
    const proposes = intention?.candidats?.length ? intention.candidats : connus;
    // Une question sans réponse possible est une impasse : mieux vaut dire que rien
    // n'est disponible que d'afficher « Que puis-je produire ? » sous zéro bouton.
    const suite = proposes.length ? Suite.CHOISIR : Suite.IMPOSSIBLE;
    return Object.freeze({ suite, gabarit: null, sujet, candidats: proposes, pages });
  }
  if (!sujet) {
    return Object.freeze({ suite: Suite.PRECISER, gabarit, sujet: "", candidats: [], pages });
  }
  return Object.freeze({ suite: Suite.PRODUIRE, gabarit, sujet, candidats: [], pages });
}
