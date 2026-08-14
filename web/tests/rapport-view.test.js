/**
 * Tests de la VUE de l'atelier — la couche que rien ne couvrait.
 *
 *     node --test web/tests/*.test.js
 *
 * C'est ici qu'ont vécu les défauts les plus visibles : marque répétée sur chaque
 * ligne, question posée sans réponse possible, libellés d'état recopiés à la main.
 * Les tests de domaine et d'application ne pouvaient rien en dire — ils ne touchent
 * pas au DOM.
 */

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import { monterDom } from "./dom-minimal.js";

const REFS = [
  "statutAtelier",
  "exemples",
  "panneauQuestion",
  "questionTitre",
  "questionChoix",
  "btnProduire",
  "panneauDocument",
  "mentionDocument",
  "comprisDocument",
  "titreDocument",
  "etatDocument",
  "sommaire",
  "exports",
  "panneauHistorique",
  "listeDocuments",
];

let refs;
let vue;
let creerVueRapport;

const GABARIT = {
  identifiant: "bulletin_regional",
  titre: "Bulletin régional — {sujet}",
  public: "producteurs, ANADER",
  mention: "",
  sections: ["Météo", "Prix", "Alertes"],
};

function sommaire(etat, sections, extra = {}) {
  return { etat, sections, message: "", titre: "", mention: "", ...extra };
}

const section = (titre, etat, corps = "") => ({ titre, etat, corps });

beforeEach(async () => {
  const noeuds = monterDom(REFS);
  // Le module de vue lit `document` à l'import : on le charge APRÈS le montage.
  ({ creerVueRapport } = await import("../src/ui/rapport-view.js"));
  refs = {
    statut: noeuds.statutAtelier,
    exemples: noeuds.exemples,
    panneauQuestion: noeuds.panneauQuestion,
    questionTitre: noeuds.questionTitre,
    questionChoix: noeuds.questionChoix,
    btnProduire: noeuds.btnProduire,
    panneau: noeuds.panneauDocument,
    mention: noeuds.mentionDocument,
    compris: noeuds.comprisDocument,
    titreDocument: noeuds.titreDocument,
    etat: noeuds.etatDocument,
    sommaire: noeuds.sommaire,
    exports: noeuds.exports,
    panneauHistorique: noeuds.panneauHistorique,
    listeDocuments: noeuds.listeDocuments,
  };
  vue = creerVueRapport(refs);
});

describe("le sommaire", () => {
  it("rend une ligne par section, dans l'ordre du plan", () => {
    vue.rendreSommaire(
      sommaire("repos", [
        section("Météo", "attente"),
        section("Prix", "attente"),
        section("Alertes", "attente"),
      ])
    );
    assert.equal(refs.sommaire.children.length, 3);
    assert.deepEqual(
      refs.sommaire.children.map((ligne) => ligne.textContent),
      ["Météo", "Prix", "Alertes"]
    );
  });

  it("ne répète pas « à venir » sur chaque ligne", () => {
    // Au repos, TOUTES les sections sont à venir : le répéter n'apprend rien et
    // noie les deux marques qui, elles, disent quelque chose.
    vue.rendreSommaire(sommaire("repos", [section("Météo", "attente")]));
    assert.equal(refs.sommaire.children[0].textContent, "Météo");
  });

  it("marque ce qui sort de l'ordinaire, et cela seulement", () => {
    vue.rendreSommaire(
      sommaire("en_cours", [
        section("Météo", "redigee", "Prose."),
        section("Prix", "en_cours"),
        section("Alertes", "lacune", "Aucune source."),
      ])
    );
    const textes = refs.sommaire.children.map((ligne) => ligne.textContent);
    assert.ok(!textes[0].includes("en cours"));
    assert.ok(textes[1].includes("en cours"));
    assert.ok(textes[2].includes("sans source"));
  });

  it("porte l'état de chaque section en classe, pour le style", () => {
    vue.rendreSommaire(
      sommaire("en_cours", [section("Météo", "lacune"), section("Prix", "redigee")])
    );
    assert.match(refs.sommaire.children[0].className, /section-lacune/);
    assert.match(refs.sommaire.children[1].className, /section-redigee/);
  });

  it("affiche la prose produite quand elle existe", () => {
    vue.rendreSommaire(sommaire("en_cours", [section("Prix", "redigee", "Le prix garanti.")]));
    assert.match(refs.sommaire.children[0].textContent, /Le prix garanti\./);
  });

  it("n'offre le téléchargement qu'une fois le document complet", () => {
    vue.rendreSommaire(sommaire("en_cours", [section("Prix", "redigee", "x")]));
    assert.equal(refs.exports.hidden, true);
    vue.rendreSommaire(sommaire("termine", [section("Prix", "redigee", "x")]));
    assert.equal(refs.exports.hidden, false);
  });

  it("affiche la mention du gabarit quand il y en a une", () => {
    vue.rendreSommaire(sommaire("repos", [], { mention: "Document préparatoire." }));
    assert.equal(refs.mention.hidden, false);
    assert.match(refs.mention.textContent, /préparatoire/);

    vue.rendreSommaire(sommaire("repos", []));
    assert.equal(refs.mention.hidden, true);
  });

  it("compte les sections faites, au singulier comme au pluriel", () => {
    vue.rendreSommaire(sommaire("en_cours", [section("A", "redigee"), section("B", "attente")]));
    assert.match(refs.etat.textContent, /1 section sur 2/);
    vue.rendreSommaire(sommaire("en_cours", [section("A", "redigee"), section("B", "redigee")]));
    assert.match(refs.etat.textContent, /2 sections sur 2/);
  });

  it("préfère le message du serveur à la phrase calculée", () => {
    vue.rendreSommaire(sommaire("echoue", [], { message: "Le flux s'est interrompu." }));
    assert.match(refs.etat.textContent, /interrompu/);
  });
});

describe("la question", () => {
  it("propose chaque candidat avec ce qu'il produit", () => {
    vue.poserQuestion("De quel type ?", [GABARIT], () => {});
    assert.equal(refs.panneauQuestion.hidden, false);
    const bouton = refs.questionChoix.children[0];
    // Le nom lisible, pas l'identifiant, et sans le « {sujet} » du gabarit.
    assert.match(bouton.textContent, /Bulletin régional/);
    assert.ok(!bouton.textContent.includes("{sujet}"));
    assert.match(bouton.textContent, /3 sections/);
    assert.match(bouton.textContent, /producteurs, ANADER/);
  });

  it("rend le gabarit choisi à l'appelant", () => {
    let choisi = null;
    vue.poserQuestion("De quel type ?", [GABARIT], (gabarit) => {
      choisi = gabarit;
    });
    refs.questionChoix.children[0].click();
    assert.equal(choisi, GABARIT);
  });

  it("déplace le focus sur la question", () => {
    // Elle apparaît seule au milieu de l'écran : sans cela, personne au clavier ni au
    // lecteur d'écran n'apprend qu'on lui demande quelque chose.
    vue.poserQuestion("De quel type ?", [GABARIT], () => {});
    assert.equal(refs.questionTitre.focalise, true);
  });

  it("s'efface quand il n'y a plus de question", () => {
    vue.poserQuestion("De quel type ?", [GABARIT], () => {});
    vue.poserQuestion("");
    assert.equal(refs.panneauQuestion.hidden, true);
    assert.equal(refs.questionChoix.children.length, 0);
  });

  it("survit à un gabarit incomplet venu d'un serveur plus récent", () => {
    // Un `sections` absent faisait lever en plein rendu, APRÈS que le panneau ait été
    // démasqué : l'utilisateur restait devant une question sans aucune réponse.
    vue.poserQuestion("De quel type ?", [{ identifiant: "x" }], () => {});
    assert.equal(refs.questionChoix.children.length, 1);
    assert.match(refs.questionChoix.children[0].textContent, /0 sections/);
  });
});

describe("les exemples", () => {
  it("rend une proposition cliquable par phrase", () => {
    let clique = "";
    vue.rendreExemples(["Une étude sur Daloa", "Un bulletin"], (phrase) => {
      clique = phrase;
    });
    assert.equal(refs.exemples.children.length, 2);
    refs.exemples.children[1].children[0].click();
    assert.equal(clique, "Un bulletin");
  });
});

describe("l'historique", () => {
  const doc = (etat, extra = {}) => ({
    identifiant: "r1",
    sujet: "Daloa",
    etat,
    sections_faites: 2,
    sections_total: 5,
    ...extra,
  });

  it("reste masqué tant qu'il n'y a rien", () => {
    vue.rendreHistorique([], () => {});
    assert.equal(refs.panneauHistorique.hidden, true);
  });

  it("n'offre le téléchargement que sur un document prêt", () => {
    vue.rendreHistorique([doc("termine"), doc("en_cours")], () => {});
    assert.match(refs.listeDocuments.children[0].textContent, /prêt/);
    assert.equal(refs.listeDocuments.children[0].querySelectorAll("button").length, 1);
    assert.equal(refs.listeDocuments.children[1].querySelectorAll("button").length, 0);
  });

  it("traduit chaque état en une phrase lisible", () => {
    vue.rendreHistorique([doc("en_cours"), doc("echoue"), doc("en_attente")], () => {});
    const textes = refs.listeDocuments.children.map((ligne) => ligne.textContent);
    assert.match(textes[0], /2 sur 5/);
    assert.match(textes[1], /interrompu/);
    assert.match(textes[2], /en attente/);
  });

  it("demande le téléchargement du bon document", () => {
    let demande = null;
    vue.rendreHistorique([doc("termine")], (identifiant, format) => {
      demande = { identifiant, format };
    });
    refs.listeDocuments.children[0].querySelectorAll("button")[0].click();
    assert.deepEqual(demande, { identifiant: "r1", format: "docx" });
  });

  it("supporte l'absence d'historique sans casser", () => {
    vue.rendreHistorique(null, () => {});
    assert.equal(refs.panneauHistorique.hidden, true);
  });
});

describe("le statut et le bouton", () => {
  it("efface le statut plutôt que d'afficher un vide", () => {
    vue.statut("Erreur", "err");
    assert.equal(refs.statut.hidden, false);
    assert.match(refs.statut.className, /err/);
    vue.statut("");
    assert.equal(refs.statut.hidden, true);
    assert.equal(refs.statut.textContent, "");
  });

  it("dit ce qui se passe sur le bouton lui-même", () => {
    vue.produireActif(false);
    assert.equal(refs.btnProduire.disabled, true);
    assert.match(refs.btnProduire.textContent, /en cours/);
    vue.produireActif(true);
    assert.equal(refs.btnProduire.disabled, false);
    assert.match(refs.btnProduire.textContent, /Produire/);
  });

  it("rappelle ce qui a été compris, ou l'efface", () => {
    vue.rendreCompris(GABARIT, "Daloa");
    assert.equal(refs.compris.hidden, false);
    assert.match(refs.compris.textContent, /Bulletin régional · Daloa/);
    vue.rendreCompris(null, "");
    assert.equal(refs.compris.hidden, true);
  });
});

describe("sûreté du rendu", () => {
  it("pose le texte du serveur comme TEXTE, jamais comme balisage", () => {
    // La prose vient du modèle, les titres d'un gabarit montable par ConfigMap, les
    // messages d'erreur du serveur. Aucun ne doit pouvoir devenir un élément.
    const hostile = '<img src=x onerror="alert(1)">';
    vue.rendreSommaire(sommaire("en_cours", [section(hostile, "redigee", hostile)]));

    const ligne = refs.sommaire.children[0];
    // Le texte est restitué à l'identique — donc conservé comme contenu…
    assert.match(ligne.textContent, /<img src=x/);
    // …et n'a créé AUCUN élément : un innerHTML en aurait fabriqué un.
    assert.ok(
      ligne.descendants().every((noeud) => noeud.tagName !== "IMG"),
      "un noeud a ete cree a partir du texte : le rendu ne passe plus par textContent"
    );
  });

  it("ne laisse pas un titre de gabarit hostile créer un élément", () => {
    vue.poserQuestion("?", [{ ...GABARIT, titre: "<script>x</script>" }], () => {});
    const bouton = refs.questionChoix.children[0];
    assert.ok(bouton.descendants().every((noeud) => noeud.tagName !== "SCRIPT"));
  });
});
