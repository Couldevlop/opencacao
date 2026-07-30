// VUE de l'écran « L'atelier ». Seule couche qui touche au DOM : elle reçoit un
// sommaire et le rend, sans jamais décider de rien. La logique de transition vit
// dans domain/rapport.js, l'orchestration dans application/rapports.js.
//
// Le rendu est reconstruit à chaque état plutôt que muté au coup par coup : un
// sommaire de six lignes est trop petit pour que la différence se voie, et une
// reconstruction ne peut pas désynchroniser l'écran de l'état.

import { EtatProduction, EtatSection, avancement } from "../domain/rapport.js";

// Libellé affiché pour chaque état de section. « À venir » n'y figure pas : au repos
// TOUTES les sections sont à venir, et le répéter à chaque ligne n'apprend rien. La
// marque ne sert qu'à signaler ce qui sort de l'ordinaire.
const LIBELLES = Object.freeze({
  [EtatSection.ATTENTE]: "",
  [EtatSection.EN_COURS]: "en cours",
  [EtatSection.REDIGEE]: "",
  [EtatSection.LACUNE]: "sans source",
});

/**
 * Crée la vue.
 *
 * @param {object} refs Références DOM de l'écran.
 * @returns {object} La vue, figée.
 */
export function creerVueRapport(refs) {
  /** Affiche un message d'état, ou l'efface. */
  function statut(message, variante) {
    if (!message) {
      refs.statut.hidden = true;
      refs.statut.textContent = "";
      return;
    }
    refs.statut.hidden = false;
    refs.statut.textContent = message;
    refs.statut.className = variante === "err" ? "statut err" : "statut";
  }

  /** Nom lisible d'un gabarit — son titre porte « {sujet} », inutile avant la saisie. */
  function nomGabarit(gabarit) {
    return (gabarit?.titre || "Document")
      .replace("{sujet}", "")
      .replace(/[\s—–-]+$/, "")
      .trim();
  }

  /**
   * Propose des demandes toutes faites, à cliquer.
   *
   * @param {Array<string>} phrases Demandes d'exemple.
   * @param {(phrase: string) => void} onChoix Appelé au clic.
   */
  function rendreExemples(phrases, onChoix) {
    refs.exemples.replaceChildren(
      ...phrases.map((phrase) => {
        const ligne = document.createElement("li");
        const bouton = document.createElement("button");
        bouton.type = "button";
        bouton.className = "exemple";
        bouton.textContent = phrase;
        bouton.addEventListener("click", () => onChoix(phrase));
        ligne.append(bouton);
        return ligne;
      })
    );
  }

  /**
   * Pose UNE question sur le type de document, ou l'efface.
   *
   * @param {string} question Question posée, vide pour effacer.
   * @param {Array} candidats Gabarits proposés.
   * @param {(gabarit: object) => void} onChoix Appelé au clic sur un type.
   */
  function poserQuestion(question, candidats, onChoix) {
    if (!question) {
      refs.panneauQuestion.hidden = true;
      refs.questionChoix.replaceChildren();
      return;
    }
    refs.panneauQuestion.hidden = false;
    refs.questionTitre.textContent = question;
    // La question apparaît seule au milieu de l'écran : sans déplacement du focus,
    // personne au clavier ni au lecteur d'écran n'apprend qu'on lui demande quelque
    // chose.
    refs.questionTitre.focus();
    refs.questionChoix.replaceChildren(
      ...(candidats || []).map((gabarit) => {
        const bouton = document.createElement("button");
        bouton.type = "button";
        bouton.className = "choix";

        const nom = document.createElement("span");
        nom.className = "choix-nom";
        nom.textContent = nomGabarit(gabarit);

        const detail = document.createElement("span");
        detail.className = "choix-detail";
        // Le public et le nombre de sections disent ce qu'on obtient. C'est ce dont
        // on a besoin pour trancher, et rien d'autre. Un gabarit incomplet venu d'un
        // serveur plus récent ne doit pas laisser une question sans réponse à l'écran.
        detail.textContent = `${gabarit.sections?.length ?? 0} sections · ${gabarit.public || ""}`;

        bouton.append(nom, detail);
        bouton.addEventListener("click", () => onChoix(gabarit));
        return bouton;
      })
    );
  }

  /** Rappelle ce qui a été compris de la demande, ou l'efface. */
  function rendreCompris(gabarit, sujet) {
    if (!gabarit) {
      refs.compris.hidden = true;
      return;
    }
    refs.compris.hidden = false;
    refs.compris.textContent = `${nomGabarit(gabarit)} · ${sujet}`;
  }

  /**
   * Rend le sommaire et son état.
   *
   * @param {object} sommaire État courant du sommaire.
   */
  function rendreSommaire(sommaire) {
    refs.panneau.hidden = false;
    refs.titreDocument.textContent = sommaire.titre || "Sommaire";

    if (sommaire.mention) {
      refs.mention.hidden = false;
      refs.mention.textContent = sommaire.mention;
    } else {
      refs.mention.hidden = true;
    }

    refs.etat.textContent = messageEtat(sommaire);
    refs.sommaire.replaceChildren(...sommaire.sections.map(ligneSection));
    refs.exports.hidden = sommaire.etat !== EtatProduction.TERMINE;
  }

  /** Rédige la ligne d'état sous le titre. */
  function messageEtat(sommaire) {
    if (sommaire.message) return sommaire.message;
    const { faites, total } = avancement(sommaire);
    switch (sommaire.etat) {
      case EtatProduction.REPOS:
        return `Plan en ${total} sections. Rien n'est encore rédigé.`;
      case EtatProduction.EN_COURS:
        return `${faites} section${faites > 1 ? "s" : ""} sur ${total}.`;
      case EtatProduction.TERMINE:
        return `Document complet — ${faites} section${faites > 1 ? "s" : ""} sur ${total}.`;
      case EtatProduction.ECHOUE:
        return "La génération s'est arrêtée.";
      default:
        return "";
    }
  }

  /** Construit une ligne du sommaire, prose comprise si elle existe. */
  function ligneSection(section) {
    const ligne = document.createElement("li");
    ligne.className = `section section-${section.etat}`;

    const entete = document.createElement("div");
    entete.className = "section-entete";

    const titre = document.createElement("span");
    titre.className = "section-titre";
    titre.textContent = section.titre || "Section à venir";

    entete.append(titre);

    const libelle = LIBELLES[section.etat];
    if (libelle) {
      const marque = document.createElement("span");
      marque.className = "section-marque";
      marque.textContent = libelle;
      entete.append(marque);
    }

    ligne.append(entete);

    if (section.corps) {
      const corps = document.createElement("p");
      // La prose est en serif : ce qui se produit ici est un DOCUMENT, et l'écran
      // finit par ressembler à une page plutôt qu'à un tableau de bord.
      corps.className = "section-corps";
      corps.textContent = section.corps;
      ligne.append(corps);
    }

    return ligne;
  }

  /**
   * Rend l'historique des documents de cet appareil.
   *
   * @param {Array} rapports Documents, les plus récents d'abord.
   * @param {(identifiant: string, format: string) => void} onTelecharger
   */
  function rendreHistorique(rapports, onTelecharger) {
    refs.panneauHistorique.hidden = !rapports || rapports.length === 0;
    if (!rapports) return;

    refs.listeDocuments.replaceChildren(
      ...rapports.map((rapport) => {
        const ligne = document.createElement("li");
        ligne.className = "document-ligne";

        const sujet = document.createElement("span");
        sujet.className = "document-sujet";
        sujet.textContent = rapport.sujet;

        const meta = document.createElement("span");
        meta.className = "document-meta";
        meta.textContent = etatLisible(rapport);

        ligne.append(sujet, meta);

        if (rapport.etat === EtatProduction.TERMINE) {
          const bouton = document.createElement("button");
          bouton.type = "button";
          bouton.className = "btn-ghost btn-mini";
          bouton.textContent = "Word";
          bouton.addEventListener("click", () => onTelecharger(rapport.identifiant, "docx"));
          ligne.append(bouton);
        }
        return ligne;
      })
    );
  }

  /** Traduit l'état d'un document en une phrase lisible. */
  function etatLisible(rapport) {
    // Les constantes du domaine, pas des littéraux recopiés : le jour où elles
    // changent, l'historique retomberait silencieusement sur « en attente ».
    switch (rapport.etat) {
      case EtatProduction.TERMINE:
        return "prêt";
      case EtatProduction.EN_COURS:
        return `${rapport.sections_faites} sur ${rapport.sections_total}`;
      case EtatProduction.ECHOUE:
        return "interrompu";
      default:
        return "en attente";
    }
  }

  /** Active ou désactive le bouton de production. */
  function produireActif(actif) {
    refs.btnProduire.disabled = !actif;
    refs.btnProduire.textContent = actif ? "Produire le document" : "Rédaction en cours…";
  }

  return Object.freeze({
    statut,
    rendreExemples,
    poserQuestion,
    rendreCompris,
    rendreSommaire,
    rendreHistorique,
    produireActif,
  });
}
