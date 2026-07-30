// COMPOSITION ROOT de l'écran « L'atelier » — assemble les couches et relie la vue
// aux cas d'usage. Seul module de cet écran qui touche au DOM concret et qui injecte
// les dépendances (client API -> cas d'usage -> vue).
//
// On demande un document, on ne le configure pas : la phrase part au serveur, qui dit
// ce qu'il a compris. Ce qu'on en fait est décidé par `deciderSuite`, fonction pure et
// testée ; ici on ne fait que la brancher.

import { Suite, creerAtelier, deciderSuite, suiteDirecte } from "./application/rapports.js";
import { ConseilError, ErreurKind } from "./domain/models.js";
import { creerClientApi } from "./infrastructure/api-client.js";
import { creerVueRapport } from "./ui/rapport-view.js";

const CLE_API = "opencacao.apiUrl";
// Même défaut que les autres écrans : l'API sert souvent l'interface (zéro CORS).
const API_DEFAUT = window.location.protocol.startsWith("http")
  ? window.location.origin
  : "http://localhost:8080";

// Trois demandes qui couvrent les trois types, dans la langue de la filière. Elles
// enseignent le vocabulaire que le serveur sait reconnaître.
const EXEMPLES = Object.freeze([
  "Une étude de la filière sur la campagne 2025-2026",
  "Un bulletin pour la région de Daloa",
  "Le dossier de la parcelle de Kouadio",
]);

const $ = (id) => document.getElementById(id);
const refs = {
  formDemande: $("formDemande"),
  champDemande: $("champDemande"),
  exemples: $("exemples"),
  panneauQuestion: $("panneauQuestion"),
  questionTitre: $("questionTitre"),
  questionChoix: $("questionChoix"),
  btnProduire: $("btnProduire"),
  panneau: $("panneauDocument"),
  mention: $("mentionDocument"),
  compris: $("comprisDocument"),
  titreDocument: $("titreDocument"),
  etat: $("etatDocument"),
  sommaire: $("sommaire"),
  exports: $("exports"),
  panneauHistorique: $("panneauHistorique"),
  listeDocuments: $("listeDocuments"),
  statut: $("statut"),
  settingsBtn: $("settingsBtn"),
  modal: $("modal"),
  apiUrl: $("apiUrl"),
  modalSave: $("modalSave"),
  modalCancel: $("modalCancel"),
};

let baseUrl = localStorage.getItem(CLE_API) || API_DEFAUT;
const client = creerClientApi(() => baseUrl);
const atelier = creerAtelier(client);
const vue = creerVueRapport(refs);

let catalogue = [];
let dernierRapport = null;
let enCours = false;
// Type retenu dont on attend encore le sujet : la prochaine phrase saisie EST le
// sujet, et il serait absurde de la réinterpréter comme une nouvelle demande.
let enAttenteDeSujet = null;

/** Traduit une erreur du domaine en phrase destinée au lecteur. */
function messageErreur(erreur) {
  if (!(erreur instanceof ConseilError)) return "Une erreur inattendue est survenue.";
  switch (erreur.kind) {
    case ErreurKind.RESEAU:
      return "L'API est injoignable. Vérifiez l'URL dans les paramètres.";
    case ErreurKind.LIMITE:
      return erreur.message || "Trop de documents demandés. Patientez quelques minutes.";
    case ErreurKind.INTROUVABLE:
      return erreur.message || "L'atelier de livrables n'est pas activé sur ce serveur.";
    default:
      return erreur.message || "La génération n'a pas abouti.";
  }
}

/** Charge le catalogue des types de documents. */
async function chargerGabarits() {
  let gabarits;
  try {
    gabarits = await atelier.chargerGabarits();
  } catch (erreur) {
    vue.statut(messageErreur(erreur), "err");
    return;
  }
  if (gabarits === null) {
    vue.statut("L'atelier de livrables n'est pas activé sur ce serveur.", "err");
    vue.produireActif(false);
    return;
  }
  catalogue = gabarits;
  if (gabarits.length === 0) vue.statut("Aucun type de document n'est disponible.", "err");
}

/** Point d'entrée : une phrase, et rien d'autre. */
async function soumettre(evenement) {
  evenement.preventDefault();
  if (enCours) return;

  const texte = refs.champDemande.value.trim();
  if (!texte) return;
  vue.statut("");

  // Le type est déjà retenu, il ne manquait que le sujet : on prend la phrase telle
  // quelle plutôt que d'y rechercher un type qui n'y sera pas. La règle — et sa
  // borne — vit dans la couche application, où elle est testée.
  if (enAttenteDeSujet) {
    const gabarit = enAttenteDeSujet;
    enAttenteDeSujet = null;
    appliquer(suiteDirecte(gabarit, texte));
    return;
  }

  let intention;
  try {
    intention = await atelier.comprendre(texte);
  } catch (erreur) {
    vue.statut(messageErreur(erreur), "err");
    return;
  }
  appliquer(deciderSuite(intention, catalogue));
}

/** Donne suite à ce qui a été compris. */
function appliquer(decision) {
  if (decision.suite === Suite.PRODUIRE) {
    vue.poserQuestion("");
    lancer(decision.gabarit, decision.sujet);
    return;
  }
  if (decision.suite === Suite.PRECISER) {
    demanderLeSujet(decision.gabarit);
    return;
  }
  if (decision.suite === Suite.IMPOSSIBLE) {
    vue.poserQuestion("");
    vue.statut("Aucun type de document n'est disponible sur ce serveur.", "err");
    return;
  }
  vue.poserQuestion(
    decision.sujet
      ? "De quel type de document s'agit-il ?"
      : "Que puis-je produire pour vous ?",
    decision.candidats,
    (gabarit) => {
      vue.poserQuestion("");
      if (decision.sujet) lancer(gabarit, decision.sujet);
      else demanderLeSujet(gabarit);
    }
  );
}

/** Demande l'objet du document, le type étant acquis. */
function demanderLeSujet(gabarit) {
  enAttenteDeSujet = gabarit;
  vue.poserQuestion("");
  vue.statut("Sur quoi doit porter ce document ?");
  refs.champDemande.value = "";
  refs.champDemande.focus();
}

/** Lance la production et suit son flux. */
async function lancer(gabarit, sujet) {
  enCours = true;
  dernierRapport = null;
  vue.produireActif(false);

  // TOUT est dans le try, y compris la mise en place. Un gabarit malformé venu du
  // serveur — sans `sections`, sans `titre` — faisait lever ces deux appels HORS du
  // try : `enCours` restait vrai à jamais, le bouton restait « Rédaction en cours… »
  // et la saisie devenait inerte. Rien n'en sortait qu'un rechargement.
  try {
    vue.rendreCompris(gabarit, sujet);
    // Le plan est connu d'avance : il s'affiche avant la première génération.
    vue.rendreSommaire({
      sections: (gabarit.sections || []).map((titre) => ({ titre, etat: "attente", corps: "" })),
      etat: "repos",
      message: "",
      titre: "",
      mention: gabarit.mention,
    });

    const { identifiant } = await atelier.produire({
      gabarit,
      sujet,
      onEtat: (sommaire) => vue.rendreSommaire({ ...sommaire, mention: gabarit.mention }),
    });
    dernierRapport = identifiant;
  } catch (erreur) {
    vue.statut(messageErreur(erreur), "err");
  } finally {
    enCours = false;
    vue.produireActif(true);
    rafraichirHistorique();
  }
}

/** Télécharge le document courant dans le format demandé. */
async function telecharger(identifiant, format) {
  if (!identifiant) return;
  vue.statut("Préparation du fichier…");
  try {
    const { blob, nom } = await atelier.telecharger(identifiant, format);
    // Téléchargement côté navigateur : l'en-tête X-Device-Id est obligatoire, donc un
    // simple lien ne suffirait pas — on récupère les octets puis on les propose.
    const url = URL.createObjectURL(blob);
    const lien = document.createElement("a");
    lien.href = url;
    lien.download = nom;
    // Attaché au document, et révoqué à la tâche SUIVANTE. Révoquer dans la même tâche
    // que le clic invalide l'URL avant que le navigateur n'ait fini de lire le blob :
    // sur Firefox et Safari, ou sur un .pptx un peu lourd, le fichier n'arrive jamais
    // — et l'effacement du statut juste après ne laisse même pas un message.
    lien.hidden = true;
    document.body.append(lien);
    lien.click();
    setTimeout(() => {
      lien.remove();
      URL.revokeObjectURL(url);
    }, 0);
    vue.statut("");
  } catch (erreur) {
    vue.statut(messageErreur(erreur), "err");
  }
}

/** Rafraîchit la liste des documents de cet appareil. */
async function rafraichirHistorique() {
  try {
    vue.rendreHistorique(await atelier.historique(), telecharger);
  } catch {
    // L'historique est un confort : son échec ne doit pas masquer l'écran principal.
  }
}

// ------------------------------------------------------------------ câblage DOM

refs.formDemande.addEventListener("submit", soumettre);

// Entrée envoie, Maj+Entrée va à la ligne : la convention d'une zone de saisie
// conversationnelle, que tout le monde connaît déjà.
refs.champDemande.addEventListener("keydown", (evenement) => {
  if (evenement.key === "Enter" && !evenement.shiftKey) {
    evenement.preventDefault();
    refs.formDemande.requestSubmit();
  }
});

refs.exports.addEventListener("click", (evenement) => {
  const format = evenement.target?.dataset?.format;
  if (format) telecharger(dernierRapport, format);
});

/** Ferme la modale et rend le focus à ce qui l'a ouverte. */
function fermerModale() {
  refs.modal.hidden = true;
  // Sans cela le focus retombe sur <body> et la tabulation suivante repart du haut
  // de la page — on perd sa place pour avoir consulté un réglage.
  refs.settingsBtn.focus();
}

refs.settingsBtn.addEventListener("click", () => {
  refs.apiUrl.value = baseUrl;
  refs.modal.hidden = false;
  refs.apiUrl.focus();
});

refs.modalCancel.addEventListener("click", fermerModale);

refs.modalSave.addEventListener("click", () => {
  const valeur = refs.apiUrl.value.trim();
  // Le champ est `type="url"` mais le bouton est hors de tout <form> : la validation
  // du navigateur ne se déclenche jamais. Or l'en-tête X-Device-Id — seule clé
  // d'accès aux documents de cet appareil — part vers l'adresse saisie ici.
  if (valeur && !/^https?:\/\//i.test(valeur)) {
    vue.statut("L'adresse doit commencer par http:// ou https://.", "err");
    return;
  }
  if (valeur) {
    baseUrl = valeur.replace(/\/+$/, "");
    localStorage.setItem(CLE_API, baseUrl);
  }
  fermerModale();
  chargerGabarits();
  rafraichirHistorique();
});

refs.modal.addEventListener("click", (evenement) => {
  if (evenement.target === refs.modal) fermerModale();
});

// Piège de focus : la modale se déclare `aria-modal`, la page dessous n'est ni inerte
// ni masquée. Sans cela on tabule hors du dialogue et on peut lancer une production
// pendant qu'il est ouvert.
refs.modal.addEventListener("keydown", (evenement) => {
  if (evenement.key !== "Tab") return;
  const focalisables = refs.modal.querySelectorAll("input, button");
  if (focalisables.length === 0) return;
  const premier = focalisables[0];
  const dernier = focalisables[focalisables.length - 1];
  if (evenement.shiftKey && document.activeElement === premier) {
    evenement.preventDefault();
    dernier.focus();
  } else if (!evenement.shiftKey && document.activeElement === dernier) {
    evenement.preventDefault();
    premier.focus();
  }
});

document.addEventListener("keydown", (evenement) => {
  if (evenement.key === "Escape" && !refs.modal.hidden) fermerModale();
});

vue.rendreExemples(EXEMPLES, (phrase) => {
  refs.champDemande.value = phrase;
  refs.formDemande.requestSubmit();
});

chargerGabarits();
rafraichirHistorique();
