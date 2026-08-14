/**
 * Tests du COMPOSITION ROOT de l'atelier — la dernière couche sans test.
 *
 *     node --test web/tests/*.test.js
 *
 * Ce module câble tout et exécute au chargement : `getElementById` à l'import,
 * écouteurs posés, catalogue et historique demandés. On monte donc l'environnement
 * AVANT de l'importer, et on le réimporte à neuf pour chaque scénario.
 *
 * Ce qu'on y vérifie n'est pas cosmétique : la machine à états du sujet dicté, le
 * téléchargement (dont la révocation d'URL trop tôt annule silencieusement le
 * fichier), le piège de focus de la modale, et le refus d'une adresse d'API qui
 * emporterait la clé d'accès aux documents vers un hôte quelconque.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { monterDom } from "./dom-minimal.js";

const IDS = [
  "formDemande",
  "champDemande",
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
  "statutAtelier",
  "settingsBtn",
  "modal",
  "apiUrl",
  "modalSave",
  "modalCancel",
];

const GABARIT = {
  identifiant: "bulletin_regional",
  titre: "Bulletin régional — {sujet}",
  public: "producteurs",
  mention: "",
  sections: ["Météo", "Prix"],
};

/**
 * Monte l'écran complet et rend de quoi l'observer.
 *
 * @param {object} options Comportements du serveur simulé.
 */
async function monterEcran(options = {}) {
  const noeuds = monterDom(IDS);
  const journal = { intentions: [], crees: [], exports: [], revoques: 0, telecharges: [] };

  const magasin = new Map([["opencacao.deviceId", "appareil-test"]]);
  globalThis.localStorage = {
    getItem: (cle) => (magasin.has(cle) ? magasin.get(cle) : null),
    setItem: (cle, valeur) => magasin.set(cle, String(valeur)),
    removeItem: (cle) => magasin.delete(cle),
  };
  globalThis.window = { location: { protocol: "https:", origin: "https://exemple.test" } };
  globalThis.URL = {
    createObjectURL: () => "blob:faux",
    revokeObjectURL: () => {
      journal.revoques += 1;
    },
  };

  // Le client d'API est joint par `fetch` : on le double au plus bas niveau, ce qui
  // exerce AUSSI le client réel plutôt qu'un simulacre de client.
  globalThis.fetch = async (url, init = {}) => {
    const corpsEnvoye = init.body ? JSON.parse(init.body) : null;
    if (url.endsWith("/v1/rapports/gabarits")) {
      return reponse(options.gabarits === undefined ? [GABARIT] : options.gabarits);
    }
    if (url.endsWith("/v1/rapports/intention")) {
      journal.intentions.push(corpsEnvoye.demande);
      return reponse(options.intention || { gabarit: "", sujet: "", certaine: false, candidats: [] });
    }
    if (url.endsWith("/v1/rapports") && init.method === "POST") {
      journal.crees.push(corpsEnvoye);
      return reponse({ identifiant: "r1", etat: "en_attente" });
    }
    if (url.endsWith("/v1/rapports")) return reponse(options.historique || []);
    if (url.includes("/stream")) return flux(options.evenements || ['data: {"type":"final"}\n\n']);
    if (url.includes("/export")) {
      journal.exports.push(url);
      return reponse("octets", { blob: true });
    }
    throw new Error(`URL non simulée : ${url}`);
  };

  const { creerVueRapport } = await import("../src/ui/rapport-view.js");
  assert.ok(creerVueRapport, "la vue doit être chargeable");
  await import(`../src/rapport-main.js?t=${Math.random()}`);
  // Laisser aboutir le chargement du catalogue et de l'historique.
  await pause();
  return { noeuds, journal };
}

function reponse(corps, { blob = false } = {}) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    async json() {
      return corps;
    },
    async text() {
      return JSON.stringify(corps);
    },
    async blob() {
      return corps;
    },
    body: null,
  };
}

function flux(morceaux) {
  const encodeur = new TextEncoder();
  let rang = 0;
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    body: {
      getReader: () => ({
        async read() {
          if (rang >= morceaux.length) return { done: true, value: undefined };
          return { done: false, value: encodeur.encode(morceaux[rang++]) };
        },
      }),
    },
  };
}

/** Laisse tourner les promesses en attente. */
const pause = () => new Promise((resoudre) => setTimeout(resoudre, 5));

describe("la demande", () => {
  it("envoie la phrase telle quelle au serveur", async () => {
    const { noeuds, journal } = await monterEcran();
    noeuds.champDemande.value = "  une étude sur Daloa  ";
    noeuds.formDemande.emettre("submit");
    await pause();
    // Rognée, mais pas réécrite : c'est au serveur d'interpréter.
    assert.deepEqual(journal.intentions, ["une étude sur Daloa"]);
  });

  it("ne demande rien sur une saisie vide", async () => {
    const { noeuds, journal } = await monterEcran();
    noeuds.champDemande.value = "   ";
    noeuds.formDemande.emettre("submit");
    await pause();
    assert.deepEqual(journal.intentions, []);
  });

  it("produit directement quand le serveur a tout compris", async () => {
    const { noeuds, journal } = await monterEcran({
      intention: {
        gabarit: "bulletin_regional",
        sujet: "Daloa",
        certaine: true,
        candidats: [],
      },
    });
    noeuds.champDemande.value = "un bulletin pour Daloa";
    noeuds.formDemande.emettre("submit");
    await pause();
    assert.deepEqual(journal.crees, [{ gabarit: "bulletin_regional", sujet: "Daloa" }]);
  });

  it("pose une question quand le type est ambigu", async () => {
    const { noeuds, journal } = await monterEcran({
      intention: {
        gabarit: "",
        sujet: "les cours du cacao",
        certaine: false,
        candidats: [GABARIT],
      },
    });
    noeuds.champDemande.value = "les cours du cacao";
    noeuds.formDemande.emettre("submit");
    await pause();

    assert.equal(noeuds.panneauQuestion.hidden, false);
    assert.deepEqual(journal.crees, [], "rien ne doit être produit avant la réponse");

    // On répond : la production part, avec le sujet DÉJÀ compris — on ne le redemande
    // pas, ce serait faire répéter à la personne ce qu'elle vient d'écrire.
    noeuds.questionChoix.children[0].click();
    await pause();
    assert.deepEqual(journal.crees, [
      { gabarit: "bulletin_regional", sujet: "les cours du cacao" },
    ]);
  });
});

describe("le sujet dicté après une question", () => {
  it("prend la phrase suivante pour sujet, sans la réinterpréter", async () => {
    const { noeuds, journal } = await monterEcran({
      intention: {
        gabarit: "bulletin_regional",
        sujet: "",
        certaine: false,
        candidats: [GABARIT],
      },
    });
    noeuds.champDemande.value = "fais-moi un bulletin";
    noeuds.formDemande.emettre("submit");
    await pause();
    assert.deepEqual(journal.crees, [], "sans sujet, on demande au lieu de deviner");

    // La phrase suivante EST le sujet : la renvoyer à l'interprétation n'y trouverait
    // aucun type et ferait tourner en rond.
    noeuds.champDemande.value = "la région de Daloa";
    noeuds.formDemande.emettre("submit");
    await pause();
    assert.equal(journal.intentions.length, 1, "la seconde phrase ne doit pas être réinterprétée");
    assert.deepEqual(journal.crees, [
      { gabarit: "bulletin_regional", sujet: "la région de Daloa" },
    ]);
  });
});

describe("le téléchargement", () => {
  it("ne révoque pas l'URL du fichier dans la même tâche que le clic", async () => {
    // Révoquer tout de suite invalide le blob avant que le navigateur ne l'ait lu :
    // sur Firefox, Safari, ou un .pptx un peu lourd, le fichier n'arrive jamais — et
    // l'effacement du statut juste après ne laisse même pas un message d'erreur.
    const { noeuds, journal } = await monterEcran({
      historique: [
        { identifiant: "r1", sujet: "Daloa", etat: "termine", sections_faites: 2, sections_total: 2 },
      ],
    });

    const bouton = noeuds.listeDocuments.children[0].querySelectorAll("button")[0];
    bouton.click();

    // La course ne se joue pas dans l'ordre des APPELS — `click()` rend la main tout
    // de suite, la lecture du blob par le navigateur est asynchrone — mais entre
    // TÂCHES. On épuise donc les micro-tâches : à ce stade, une révocation immédiate
    // aurait déjà eu lieu, et le fichier serait perdu.
    for (let tour = 0; tour < 50; tour += 1) await Promise.resolve();
    assert.equal(journal.exports.length, 1, "le téléchargement doit être parti");
    assert.equal(journal.revoques, 0, "l'URL est révoquée trop tôt : le fichier n'arrivera pas");

    // À la tâche suivante, elle l'est — pas de fuite mémoire non plus.
    await pause();
    assert.equal(journal.revoques, 1);
    assert.match(journal.exports[0], /\/v1\/rapports\/r1\/export\?format=docx$/);
  });

  it("attache le lien au document avant de cliquer", async () => {
    // Une ancre détachée est historiquement ignorée par Safari.
    const { noeuds } = await monterEcran({
      historique: [
        { identifiant: "r1", sujet: "Daloa", etat: "termine", sections_faites: 2, sections_total: 2 },
      ],
    });
    let attache = false;
    const ajouter = document.body.append.bind(document.body);
    document.body.append = (...noeudsAjoutes) => {
      attache = true;
      ajouter(...noeudsAjoutes);
    };

    noeuds.listeDocuments.children[0].querySelectorAll("button")[0].click();
    await pause();
    assert.equal(attache, true);
  });
});

describe("les paramètres de connexion", () => {
  it("refuse une adresse qui n'est pas http(s)", async () => {
    // Le champ est `type="url"` mais le bouton est hors de tout formulaire : la
    // validation du navigateur ne se déclenche jamais. Or l'en-tête X-Device-Id —
    // seule clé d'accès aux documents de cet appareil — part vers l'adresse saisie.
    const { noeuds } = await monterEcran();
    noeuds.apiUrl.value = "javascript:alert(1)";
    noeuds.modalSave.click();
    await pause();

    assert.equal(noeuds.statutAtelier.hidden, false);
    assert.match(noeuds.statutAtelier.textContent, /http/);
    assert.equal(localStorage.getItem("opencacao.apiUrl"), null, "rien ne doit être retenu");
  });

  it("retient une adresse valable, sans sa barre finale", async () => {
    const { noeuds } = await monterEcran();
    noeuds.apiUrl.value = "https://api.exemple.test///";
    noeuds.modalSave.click();
    await pause();
    assert.equal(localStorage.getItem("opencacao.apiUrl"), "https://api.exemple.test");
  });

  it("rend le focus au bouton qui a ouvert la modale", async () => {
    // Sans cela le focus retombe sur <body> et la tabulation suivante repart du haut
    // de la page : on perd sa place pour avoir consulté un réglage.
    const { noeuds } = await monterEcran();
    noeuds.modalCancel.click();
    assert.equal(noeuds.modal.hidden, true);
    assert.equal(noeuds.settingsBtn.focalise, true);
  });
});
