/**
 * Tests de l'orchestration de l'atelier — client d'API simulé, aucun réseau.
 *
 *     node --test web/tests/*.test.js
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  SUJET_MAX,
  Suite,
  creerAtelier,
  deciderSuite,
  suiteDirecte,
} from "../src/application/rapports.js";
import { EtatProduction, EtatSection } from "../src/domain/rapport.js";

const GABARIT = {
  identifiant: "bulletin_regional",
  titre: "Bulletin régional — {sujet}",
  public: "producteurs, ANADER",
  mention: "",
  sections: ["Conditions météorologiques", "Prix du cacao", "Alertes de la zone"],
};

/**
 * Client simulé qui rejoue une suite d'événements.
 *
 * @param {Array} evenements Événements à émettre, dans l'ordre.
 */
function clientSimule(evenements, options = {}) {
  const appels = { crees: [], suivis: [], exports: [] };
  return {
    appels,
    async listerGabarits() {
      return options.gabarits === undefined ? [GABARIT] : options.gabarits;
    },
    async creerRapport(demande) {
      appels.crees.push(demande);
      if (options.erreurCreation) throw options.erreurCreation;
      return { identifiant: "r1", etat: "en_attente" };
    },
    async suivreRapport(identifiant, onEvenement) {
      appels.suivis.push(identifiant);
      for (const evenement of evenements) onEvenement(evenement);
    },
    async listerRapports() {
      return options.rapports === undefined ? [] : options.rapports;
    },
    async exporterRapport(identifiant, format) {
      appels.exports.push({ identifiant, format });
      return { blob: "octets", nom: `bulletin_regional-${identifiant}.${format}` };
    },
  };
}

function evenementSection(titre, rang, total, lacune = false) {
  return {
    type: "section",
    titre,
    corps: lacune ? "Aucune source mobilisable." : `Prose de « ${titre} ».`,
    lacune,
    faites: rang,
    total,
  };
}

describe("chargement des gabarits", () => {
  it("rend les gabarits avec leur plan", async () => {
    const atelier = creerAtelier(clientSimule([]));
    const gabarits = await atelier.chargerGabarits();
    assert.equal(gabarits.length, 1);
    assert.equal(gabarits[0].sections.length, 3);
  });

  it("distingue « atelier absent » de « aucun gabarit »", async () => {
    // null = le serveur ne sert pas l'atelier ; [] = il le sert mais n'a rien.
    // L'écran doit pouvoir dire deux choses différentes.
    assert.equal(await creerAtelier(clientSimule([], { gabarits: null })).chargerGabarits(), null);
    assert.deepEqual(await creerAtelier(clientSimule([], { gabarits: [] })).chargerGabarits(), []);
  });
});

describe("production d'un document", () => {
  it("affiche le plan complet avant la première génération", async () => {
    const etats = [];
    const atelier = creerAtelier(clientSimule([]));
    await atelier.produire({ gabarit: GABARIT, sujet: "Daloa", onEtat: (s) => etats.push(s) });

    // Le tout premier état notifié porte déjà les trois titres, toutes en attente.
    assert.deepEqual(
      etats[0].sections.map((s) => s.titre),
      GABARIT.sections
    );
    assert.ok(etats[0].sections.every((s) => s.etat === EtatSection.ATTENTE));
  });

  it("transmet le gabarit et le sujet au serveur", async () => {
    const client = clientSimule([]);
    await creerAtelier(client).produire({ gabarit: GABARIT, sujet: "Soubré" });
    assert.deepEqual(client.appels.crees, [
      { gabarit: "bulletin_regional", sujet: "Soubré" },
    ]);
    assert.deepEqual(client.appels.suivis, ["r1"]);
  });

  it("remplit le sommaire dans l'ordre, puis devient terminé", async () => {
    const etats = [];
    const client = clientSimule([
      { type: "progress", message: "Préparation du document…" },
      evenementSection("Conditions météorologiques", 1, 3),
      evenementSection("Prix du cacao", 2, 3),
      evenementSection("Alertes de la zone", 3, 3, true),
      { type: "final", titre: "Bulletin régional — Daloa" },
    ]);
    const { identifiant, sommaire } = await creerAtelier(client).produire({
      gabarit: GABARIT,
      sujet: "Daloa",
      onEtat: (s) => etats.push(s),
    });

    assert.equal(identifiant, "r1");
    assert.equal(sommaire.etat, EtatProduction.TERMINE);
    assert.deepEqual(
      sommaire.sections.map((s) => s.etat),
      [EtatSection.REDIGEE, EtatSection.REDIGEE, EtatSection.LACUNE]
    );
    assert.match(sommaire.sections[1].corps, /Prix du cacao/);
  });

  it("notifie la vue à chaque avancée, pas seulement à la fin", async () => {
    // Sans cela, l'écran resterait figé pendant plusieurs minutes.
    const etats = [];
    const client = clientSimule([
      evenementSection("Conditions météorologiques", 1, 3),
      evenementSection("Prix du cacao", 2, 3),
      { type: "final" },
    ]);
    await creerAtelier(client).produire({
      gabarit: GABARIT,
      sujet: "Daloa",
      onEtat: (s) => etats.push(s),
    });
    // plan + démarrage + 2 sections + final
    assert.equal(etats.length, 5);
  });

  it("ne notifie pas pour un événement sans effet", async () => {
    const etats = [];
    const client = clientSimule([{ type: "type_inconnu_du_futur" }, { type: "final" }]);
    await creerAtelier(client).produire({
      gabarit: GABARIT,
      sujet: "Daloa",
      onEtat: (s) => etats.push(s),
    });
    // plan + démarrage + final : l'événement inconnu n'a rien déclenché.
    assert.equal(etats.length, 3);
  });

  it("remonte l'attente dans la file avant toute rédaction", async () => {
    const etats = [];
    const client = clientSimule([
      { type: "attente", position: 2, attente_s: 80, message: "Position 2 dans la file" },
      evenementSection("Conditions météorologiques", 1, 3),
      { type: "final" },
    ]);
    await creerAtelier(client).produire({
      gabarit: GABARIT,
      sujet: "Daloa",
      onEtat: (s) => etats.push(s),
    });
    const attente = etats.find((s) => s.etat === EtatProduction.ATTENTE_FILE);
    assert.ok(attente, "l'attente doit être notifiée");
    assert.equal(attente.position, 2);
  });

  it("remonte un refus du serveur comme un état d'échec", async () => {
    const etats = [];
    const client = clientSimule([
      { type: "error", message: "Ce document est déjà en cours de génération ou terminé." },
    ]);
    const { sommaire } = await creerAtelier(client).produire({
      gabarit: GABARIT,
      sujet: "Daloa",
      onEtat: (s) => etats.push(s),
    });
    assert.equal(sommaire.etat, EtatProduction.ECHOUE);
    assert.match(sommaire.message, /déjà en cours/);
  });

  it("signale un flux coupé avant la fin au lieu de rester figé", async () => {
    // Éviction de pod, OOM de l'inférence, coupure du répartiteur : le corps se ferme
    // proprement après 2 sections sur 3. Sans ce garde-fou l'écran reste sur « 2 sur
    // 3 » avec une ligne qui pulse, sans erreur, pour un document qui n'arrivera pas.
    const client = clientSimule([
      evenementSection("Conditions météorologiques", 1, 3),
      evenementSection("Prix du cacao", 2, 3),
    ]);
    const { sommaire } = await creerAtelier(client).produire({
      gabarit: GABARIT,
      sujet: "Daloa",
    });
    assert.equal(sommaire.etat, EtatProduction.ECHOUE);
    assert.match(sommaire.message, /interrompu/i);
  });

  it("ne crie pas au flux coupé quand la production a abouti", async () => {
    const client = clientSimule([evenementSection("Prix du cacao", 1, 3), { type: "final" }]);
    const { sommaire } = await creerAtelier(client).produire({
      gabarit: GABARIT,
      sujet: "Daloa",
    });
    assert.equal(sommaire.etat, EtatProduction.TERMINE);
  });

  it("laisse remonter une erreur de création", async () => {
    // L'écran doit pouvoir afficher « gabarit inconnu » ou « atelier désactivé ».
    const client = clientSimule([], { erreurCreation: new Error("Gabarit inconnu.") });
    await assert.rejects(
      () => creerAtelier(client).produire({ gabarit: GABARIT, sujet: "Daloa" }),
      /Gabarit inconnu/
    );
  });

  it("fonctionne sans rappel d'état", async () => {
    const client = clientSimule([{ type: "final" }]);
    const { sommaire } = await creerAtelier(client).produire({
      gabarit: GABARIT,
      sujet: "Daloa",
    });
    assert.equal(sommaire.etat, EtatProduction.TERMINE);
  });
});

describe("suite à donner à une demande comprise", () => {
  const CATALOGUE = [GABARIT, { ...GABARIT, identifiant: "etude_filiere" }];

  it("produit quand le type et le sujet sont établis", () => {
    const decision = deciderSuite(
      { gabarit: "bulletin_regional", sujet: "Daloa", certaine: true, candidats: [] },
      CATALOGUE
    );
    assert.equal(decision.suite, Suite.PRODUIRE);
    assert.equal(decision.gabarit.identifiant, "bulletin_regional");
    assert.equal(decision.sujet, "Daloa");
  });

  it("fait choisir quand le type est ambigu, en gardant le sujet vu", () => {
    const candidats = [GABARIT, { ...GABARIT, identifiant: "etude_filiere" }];
    const decision = deciderSuite(
      { gabarit: "", sujet: "les cours du cacao", certaine: false, candidats },
      CATALOGUE
    );
    assert.equal(decision.suite, Suite.CHOISIR);
    assert.equal(decision.candidats.length, 2);
    // Le sujet est acquis : la question porte sur le type, et sur lui seul.
    assert.equal(decision.sujet, "les cours du cacao");
  });

  it("propose tout le catalogue si le serveur ne nomme aucun candidat", () => {
    const decision = deciderSuite({ gabarit: "", sujet: "x", candidats: [] }, CATALOGUE);
    assert.equal(decision.candidats.length, 2);
  });

  it("demande de préciser quand le type est connu mais le sujet absent", () => {
    const decision = deciderSuite(
      { gabarit: "bulletin_regional", sujet: "", certaine: false, candidats: [] },
      CATALOGUE
    );
    assert.equal(decision.suite, Suite.PRECISER);
    assert.equal(decision.gabarit.identifiant, "bulletin_regional");
  });

  it("fait choisir plutôt que produire un type qu'il ne connaît pas", () => {
    // Serveur plus récent que cet écran : mieux vaut poser la question que produire
    // un document dont on ne sait pas afficher le plan.
    const decision = deciderSuite(
      { gabarit: "note_de_synthese", sujet: "Daloa", certaine: true, candidats: [] },
      CATALOGUE
    );
    assert.equal(decision.suite, Suite.CHOISIR);
  });

  it("supporte une réponse vide sans casser", () => {
    assert.equal(deciderSuite(null, CATALOGUE).suite, Suite.CHOISIR);
  });

  it("ne pose pas une question sans réponse possible", () => {
    // Catalogue vide et aucun candidat : afficher « Que puis-je produire ? » sous zéro
    // bouton est une impasse dont on ne sort qu'en rechargeant.
    assert.equal(deciderSuite({}, null).suite, Suite.IMPOSSIBLE);
    assert.equal(deciderSuite({ gabarit: "", sujet: "x", candidats: [] }, []).suite, Suite.IMPOSSIBLE);
  });

  it("borne le sujet compris par le serveur", () => {
    const decision = deciderSuite(
      { gabarit: "bulletin_regional", sujet: "a".repeat(500), candidats: [] },
      CATALOGUE
    );
    assert.equal(decision.sujet.length, SUJET_MAX);
  });
});

describe("sujet dicté après une question", () => {
  const GAB = { ...GABARIT };

  it("produit avec la phrase saisie pour sujet", () => {
    const decision = suiteDirecte(GAB, "  la campagne 2025-2026  ");
    assert.equal(decision.suite, Suite.PRODUIRE);
    assert.equal(decision.sujet, "la campagne 2025-2026");
  });

  it("applique la MÊME borne que l'autre voie", () => {
    // La troncature vivait dans l'écran et n'était appliquée que sur cette voie-ci :
    // un sujet compris par le serveur passait sans borne.
    assert.equal(suiteDirecte(GAB, "b".repeat(500)).sujet.length, SUJET_MAX);
  });

  it("redemande plutôt que de produire sur du vide", () => {
    assert.equal(suiteDirecte(GAB, "   ").suite, Suite.PRECISER);
  });
});

describe("historique et téléchargement", () => {
  it("rend les documents de l'appareil", async () => {
    const client = clientSimule([], { rapports: [{ identifiant: "r1", etat: "termine" }] });
    assert.equal((await creerAtelier(client).historique()).length, 1);
  });

  it("distingue « atelier absent » d'un historique vide", async () => {
    assert.equal(await creerAtelier(clientSimule([], { rapports: null })).historique(), null);
  });

  it("télécharge dans le format demandé", async () => {
    const client = clientSimule([]);
    const { nom } = await creerAtelier(client).telecharger("r1", "docx");
    assert.deepEqual(client.appels.exports, [{ identifiant: "r1", format: "docx" }]);
    assert.match(nom, /\.docx$/);
  });
});
