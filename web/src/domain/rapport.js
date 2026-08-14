/**
 * Domaine du livrable : le sommaire et son évolution. Aucun DOM, aucun réseau.
 *
 * Le plan du document est connu AVANT toute génération — il vient du gabarit, pas du
 * modèle. C'est ce qui permet d'afficher le sommaire d'emblée, puis de le voir se
 * remplir dans l'ordre. Ce module ne fait que cela : tenir l'état du sommaire et le
 * faire avancer d'un événement à l'autre.
 *
 * Les transitions sont pures et immuables : on rend un nouveau sommaire, jamais une
 * mutation. Une réduction pure se teste sans navigateur, et c'est là qu'est la logique.
 */

/** États possibles d'une section du sommaire. */
export const EtatSection = Object.freeze({
  /** Prévue par le plan, pas encore abordée. */
  ATTENTE: "attente",
  /** En cours de rédaction. */
  EN_COURS: "en_cours",
  /** Rédigée, avec son texte. */
  REDIGEE: "redigee",
  /**
   * Aucune source mobilisable. Ce n'est PAS un échec : le document le dit au lieu
   * d'estimer. L'affichage doit rester sobre — un rouge d'alerte mentirait.
   */
  LACUNE: "lacune",
});

/** États possibles de la production elle-même. */
export const EtatProduction = Object.freeze({
  REPOS: "repos",
  ATTENTE_FILE: "attente_file",
  EN_COURS: "en_cours",
  TERMINE: "termine",
  ECHOUE: "echoue",
});

/**
 * Construit le sommaire initial depuis le plan d'un gabarit.
 *
 * @param {string[]} titres Titres des sections, dans l'ordre du plan.
 * @returns {{sections: Array, etat: string, message: string, position: number}}
 */
export function sommaireDepuisPlan(titres) {
  return Object.freeze({
    sections: Object.freeze(
      (titres || []).map((titre) =>
        Object.freeze({ titre, etat: EtatSection.ATTENTE, corps: "" })
      )
    ),
    etat: EtatProduction.REPOS,
    message: "",
    position: 0,
  });
}

/** Remplace la section d'indice donné, sans muter le tableau d'origine. */
function avecSection(sections, indice, remplacement) {
  return Object.freeze(
    sections.map((section, rang) =>
      rang === indice ? Object.freeze({ ...section, ...remplacement }) : section
    )
  );
}

/** Indice de la première section encore en attente, ou -1. */
function prochaineEnAttente(sections) {
  return sections.findIndex((section) => section.etat === EtatSection.ATTENTE);
}

/**
 * Applique un événement du flux au sommaire et rend le nouvel état.
 *
 * Le plan local peut être plus court que le plan réel (gabarit modifié côté serveur
 * entre le chargement de la page et la génération) : on complète alors le sommaire
 * plutôt que de perdre une section. Le serveur fait foi.
 *
 * @param {object} sommaire État courant.
 * @param {{type: string}} evenement Événement reçu du flux.
 * @returns {object} Le nouvel état.
 */
export function appliquerEvenement(sommaire, evenement) {
  if (!evenement || !evenement.type) return sommaire;

  switch (evenement.type) {
    case "progress":
      return Object.freeze({
        ...sommaire,
        etat: EtatProduction.EN_COURS,
        message: evenement.message || "",
      });

    case "attente":
      return Object.freeze({
        ...sommaire,
        etat: EtatProduction.ATTENTE_FILE,
        position: evenement.position || 0,
        message: evenement.message || "",
      });

    case "section": {
      const total = evenement.total || sommaire.sections.length;
      const completees = sommaire.sections.slice();
      // Le serveur fait foi sur le NOMBRE de sections.
      while (completees.length < total) {
        completees.push(Object.freeze({ titre: "", etat: EtatSection.ATTENTE, corps: "" }));
      }
      let sections = Object.freeze(completees);
      // Le TITRE fait foi. Un repli sur le rang annoncé écrirait la prose d'une
      // section dans une autre si les plans avaient divergé — une erreur invisible
      // dans un document qui se veut vérifiable. On n'accepte le rang que si
      // l'événement ne porte aucun titre.
      let indice = evenement.titre
        ? sections.findIndex((section) => section.titre === evenement.titre)
        : (evenement.faites || 0) - 1;
      if (indice < 0 || indice >= sections.length) return sommaire;

      sections = avecSection(sections, indice, {
        titre: evenement.titre || sections[indice].titre,
        etat: evenement.lacune ? EtatSection.LACUNE : EtatSection.REDIGEE,
        corps: evenement.corps || "",
      });

      // La suivante passe « en cours » : c'est ce qui fait avancer le curseur sous les
      // yeux du lecteur, sans attendre l'événement suivant.
      const suivante = prochaineEnAttente(sections);
      if (suivante >= 0) {
        sections = avecSection(sections, suivante, { etat: EtatSection.EN_COURS });
      }
      return Object.freeze({
        ...sommaire,
        sections,
        etat: EtatProduction.EN_COURS,
        message: "",
        position: 0,
      });
    }

    case "final":
      return Object.freeze({
        ...sommaire,
        // Une section restée « en cours » à la fin n'a jamais été produite : la
        // laisser clignoter ferait attendre un texte qui ne viendra pas.
        sections: Object.freeze(
          sommaire.sections.map((section) =>
            section.etat === EtatSection.EN_COURS
              ? Object.freeze({ ...section, etat: EtatSection.ATTENTE })
              : section
          )
        ),
        etat: EtatProduction.TERMINE,
        titre: evenement.titre || "",
        message: "",
      });

    case "error":
      return Object.freeze({
        ...sommaire,
        sections: Object.freeze(
          sommaire.sections.map((section) =>
            section.etat === EtatSection.EN_COURS
              ? Object.freeze({ ...section, etat: EtatSection.ATTENTE })
              : section
          )
        ),
        etat: EtatProduction.ECHOUE,
        message: evenement.message || "La génération n'a pas abouti.",
      });

    default:
      // Un type inconnu est ignoré : le serveur peut enrichir le flux sans casser un
      // client déployé.
      return sommaire;
  }
}

/** Marque la première section comme en cours, au lancement. */
export function demarrer(sommaire) {
  const suivante = prochaineEnAttente(sommaire.sections);
  return Object.freeze({
    ...sommaire,
    etat: EtatProduction.EN_COURS,
    message: "",
    position: 0,
    sections:
      suivante >= 0
        ? avecSection(sommaire.sections, suivante, { etat: EtatSection.EN_COURS })
        : sommaire.sections,
  });
}

/** Nombre de sections rédigées ou déclarées en lacune. */
export function avancement(sommaire) {
  const faites = sommaire.sections.filter(
    (section) => section.etat === EtatSection.REDIGEE || section.etat === EtatSection.LACUNE
  ).length;
  return { faites, total: sommaire.sections.length };
}

/** Vrai si le document est prêt à être téléchargé. */
export function exportable(sommaire) {
  return sommaire.etat === EtatProduction.TERMINE;
}
