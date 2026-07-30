/**
 * Tests du domaine du livrable — le sommaire et son évolution.
 *
 * Exécutés par le lanceur de tests de Node (aucune dépendance) :
 *     node --test web/tests/*.test.js
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  EtatProduction,
  EtatSection,
  appliquerEvenement,
  avancement,
  demarrer,
  exportable,
  sommaireDepuisPlan,
} from "../src/domain/rapport.js";

const PLAN = ["Contexte de la filière", "Marché et prix", "Limites de l'étude"];

/** Applique une suite d'événements, dans l'ordre. */
function rejouer(sommaire, ...evenements) {
  return evenements.reduce(appliquerEvenement, sommaire);
}

describe("sommaire initial", () => {
  it("affiche le plan avant toute génération", () => {
    // C'est tout l'intérêt du gabarit : le plan est connu d'avance, le lecteur le voit
    // avant que la première ligne ne soit écrite.
    const sommaire = sommaireDepuisPlan(PLAN);
    assert.deepEqual(
      sommaire.sections.map((s) => s.titre),
      PLAN
    );
    assert.ok(sommaire.sections.every((s) => s.etat === EtatSection.ATTENTE));
    assert.equal(sommaire.etat, EtatProduction.REPOS);
  });

  it("tolère un plan absent", () => {
    assert.deepEqual(sommaireDepuisPlan(undefined).sections, []);
  });

  it("est immuable", () => {
    // Le sommaire est partagé avec la vue : une mutation accidentelle serait
    // invisible et indéboguable.
    const sommaire = sommaireDepuisPlan(PLAN);
    assert.throws(() => {
      sommaire.sections.push({});
    });
  });
});

describe("progression du sommaire", () => {
  it("marque la première section en cours au démarrage", () => {
    const sommaire = demarrer(sommaireDepuisPlan(PLAN));
    assert.equal(sommaire.sections[0].etat, EtatSection.EN_COURS);
    assert.equal(sommaire.sections[1].etat, EtatSection.ATTENTE);
  });

  it("inscrit le texte de la section reçue, et avance le curseur", () => {
    // Le cœur de l'écran : la prose apparaît à sa place dans le plan, et la ligne
    // suivante s'allume — le document s'assemble sous les yeux du lecteur.
    const sommaire = rejouer(demarrer(sommaireDepuisPlan(PLAN)), {
      type: "section",
      titre: "Contexte de la filière",
      corps: "La filière cacao ivoirienne demeure le premier employeur agricole.",
      lacune: false,
      faites: 1,
      total: 3,
    });

    assert.equal(sommaire.sections[0].etat, EtatSection.REDIGEE);
    assert.match(sommaire.sections[0].corps, /premier employeur/);
    assert.equal(sommaire.sections[1].etat, EtatSection.EN_COURS);
    assert.deepEqual(avancement(sommaire), { faites: 1, total: 3 });
  });

  it("distingue une lacune d'une section rédigée", () => {
    // Une lacune n'est pas un échec : le document dit ce qui manque au lieu d'estimer.
    const sommaire = rejouer(sommaireDepuisPlan(PLAN), {
      type: "section",
      titre: "Limites de l'étude",
      corps: "Aucune source mobilisable n'a été trouvée pour cette section.",
      lacune: true,
      faites: 3,
      total: 3,
    });
    const limites = sommaire.sections.find((s) => s.titre === "Limites de l'étude");
    assert.equal(limites.etat, EtatSection.LACUNE);
    assert.ok(limites.corps, "le constat de lacune doit être affichable");
    // Elle compte comme traitée : le lecteur ne doit pas croire qu'on l'a oubliée.
    assert.equal(avancement(sommaire).faites, 1);
  });

  it("repère la section par son titre, pas par son rang", () => {
    // Si le plan local et le plan réel divergent, un indice se décale en silence.
    const sommaire = rejouer(sommaireDepuisPlan(PLAN), {
      type: "section",
      titre: "Marché et prix",
      corps: "Le prix bord-champ est fixé par campagne.",
      lacune: false,
      faites: 2,
      total: 3,
    });
    assert.equal(sommaire.sections[1].etat, EtatSection.REDIGEE);
    assert.equal(sommaire.sections[0].etat, EtatSection.EN_COURS);
  });

  it("complète le plan si le serveur en annonce davantage", () => {
    // Le serveur fait foi : un gabarit modifié entre le chargement de la page et la
    // génération ne doit pas faire perdre une section.
    const sommaire = rejouer(sommaireDepuisPlan(["Une"]), {
      type: "section",
      titre: "Une",
      corps: "Texte.",
      lacune: false,
      faites: 1,
      total: 4,
    });
    assert.equal(sommaire.sections.length, 4);
    assert.equal(avancement(sommaire).total, 4);
  });

  it("ne mute pas le sommaire précédent", () => {
    const avant = demarrer(sommaireDepuisPlan(PLAN));
    const apres = appliquerEvenement(avant, {
      type: "section",
      titre: "Contexte de la filière",
      corps: "Texte.",
      lacune: false,
      faites: 1,
      total: 3,
    });
    assert.equal(avant.sections[0].etat, EtatSection.EN_COURS);
    assert.equal(apres.sections[0].etat, EtatSection.REDIGEE);
  });
});

describe("attente et messages", () => {
  it("annonce la position dans la file", () => {
    // « Le pire scénario n'est pas la lenteur, c'est la lenteur muette. »
    const sommaire = rejouer(sommaireDepuisPlan(PLAN), {
      type: "attente",
      position: 2,
      attente_s: 80,
      message: "Position 2 dans la file d'attente, environ 1 minutes d'attente.",
    });
    assert.equal(sommaire.etat, EtatProduction.ATTENTE_FILE);
    assert.equal(sommaire.position, 2);
    assert.match(sommaire.message, /Position 2/);
  });

  it("efface le message d'attente dès que la rédaction commence", () => {
    const sommaire = rejouer(
      sommaireDepuisPlan(PLAN),
      { type: "attente", position: 1, message: "Position 1" },
      { type: "section", titre: "Contexte de la filière", corps: "T", faites: 1, total: 3 }
    );
    assert.equal(sommaire.message, "");
    assert.equal(sommaire.position, 0);
  });

  it("relaie le message de progression", () => {
    const sommaire = rejouer(sommaireDepuisPlan(PLAN), {
      type: "progress",
      message: "Préparation du document…",
    });
    assert.equal(sommaire.etat, EtatProduction.EN_COURS);
    assert.match(sommaire.message, /Préparation/);
  });
});

describe("fin de production", () => {
  it("devient exportable une fois terminé", () => {
    const sommaire = rejouer(sommaireDepuisPlan(PLAN), { type: "final", titre: "Étude — cacao" });
    assert.equal(sommaire.etat, EtatProduction.TERMINE);
    assert.equal(sommaire.titre, "Étude — cacao");
    assert.ok(exportable(sommaire));
  });

  it("n'est pas exportable avant la fin", () => {
    assert.ok(!exportable(sommaireDepuisPlan(PLAN)));
    assert.ok(!exportable(demarrer(sommaireDepuisPlan(PLAN))));
  });

  it("éteint la section restée en cours à la fin", () => {
    // Sinon une ligne clignoterait indéfiniment en promettant un texte qui ne
    // viendra pas.
    const sommaire = rejouer(demarrer(sommaireDepuisPlan(PLAN)), { type: "final" });
    assert.ok(sommaire.sections.every((s) => s.etat !== EtatSection.EN_COURS));
  });

  it("porte le message d'erreur et redevient non exportable", () => {
    const sommaire = rejouer(demarrer(sommaireDepuisPlan(PLAN)), {
      type: "error",
      message: "Ce document est déjà en cours de génération ou terminé.",
    });
    assert.equal(sommaire.etat, EtatProduction.ECHOUE);
    assert.match(sommaire.message, /déjà en cours/);
    assert.ok(!exportable(sommaire));
    assert.ok(sommaire.sections.every((s) => s.etat !== EtatSection.EN_COURS));
  });

  it("donne un message par défaut si le serveur n'en fournit pas", () => {
    const sommaire = rejouer(sommaireDepuisPlan(PLAN), { type: "error" });
    assert.ok(sommaire.message.length > 0);
  });
});

describe("robustesse du flux", () => {
  it("ignore un type d'événement inconnu", () => {
    // Le serveur doit pouvoir enrichir le flux sans casser un client déployé.
    const avant = sommaireDepuisPlan(PLAN);
    assert.equal(appliquerEvenement(avant, { type: "chaleur_du_cafe" }), avant);
  });

  it("ignore un événement vide ou mal formé", () => {
    const avant = sommaireDepuisPlan(PLAN);
    assert.equal(appliquerEvenement(avant, null), avant);
    assert.equal(appliquerEvenement(avant, {}), avant);
  });

  it("ignore une section hors du plan", () => {
    const avant = sommaireDepuisPlan(PLAN);
    const apres = appliquerEvenement(avant, {
      type: "section",
      titre: "Inconnue",
      faites: 0,
      total: 3,
    });
    assert.equal(apres, avant);
  });

  it("traverse un flux complet sans perdre une section", () => {
    let sommaire = demarrer(sommaireDepuisPlan(PLAN));
    sommaire = rejouer(
      sommaire,
      { type: "progress", message: "Préparation du document…" },
      { type: "section", titre: PLAN[0], corps: "A.", lacune: false, faites: 1, total: 3 },
      { type: "section", titre: PLAN[1], corps: "B.", lacune: false, faites: 2, total: 3 },
      { type: "section", titre: PLAN[2], corps: "Rien à affirmer.", lacune: true, faites: 3, total: 3 },
      { type: "final", titre: "Étude de filière — le cacao" }
    );
    assert.deepEqual(avancement(sommaire), { faites: 3, total: 3 });
    assert.deepEqual(
      sommaire.sections.map((s) => s.etat),
      [EtatSection.REDIGEE, EtatSection.REDIGEE, EtatSection.LACUNE]
    );
    assert.ok(exportable(sommaire));
  });
});
