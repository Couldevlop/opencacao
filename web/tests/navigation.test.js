/**
 * Navigation d'une coquille unique — une fenêtre, trois destinations.
 *
 * Ce qui est vérifié ici est ce qui casse une démonstration : qu'UNE seule vue soit
 * visible à la fois, que le module d'une destination ne soit chargé qu'une fois, et
 * surtout qu'une destination en panne n'emporte pas les autres. Le chat tourne en
 * production depuis des semaines ; il ne doit pas mourir parce que l'atelier, livré
 * la veille, lève à l'import.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { monterDom } from "./dom-minimal.js";
import {
  afficherAvisRepli,
  appliquerCapacites,
  creerNavigation,
  nomDepuisHash,
} from "../src/ui/navigation.js";

test("le changement est signalé pour tenir l'URL à jour", async () => {
  const noeuds = monterDom(["vueChat", "vueParcelle", "lienChat", "lienParcelle"]);
  const vus = [];
  const navigation = creerNavigation({
    vues: { chat: noeuds.vueChat, parcelle: noeuds.vueParcelle },
    liens: { chat: noeuds.lienChat, parcelle: noeuds.lienParcelle },
    // Signale AVANT le chargement : l URL doit suivre l ecran, pas le reseau.
    chargeurs: { parcelle: async () => vus.push("chargement") },
    surChangement: (nom) => vus.push(nom),
  });

  await navigation.activer("parcelle");

  assert.deepEqual(vus, ["parcelle", "chargement"]);
});

/** Monte trois vues et trois liens, et rend la navigation prête à l'emploi. */
function monterCoquille(chargeurs = {}) {
  const noeuds = monterDom([
    "vueChat",
    "vueParcelle",
    "vueAtelier",
    "lienChat",
    "lienParcelle",
    "lienAtelier",
  ]);
  const echecs = [];
  const navigation = creerNavigation({
    vues: {
      chat: noeuds.vueChat,
      parcelle: noeuds.vueParcelle,
      atelier: noeuds.vueAtelier,
    },
    liens: {
      chat: noeuds.lienChat,
      parcelle: noeuds.lienParcelle,
      atelier: noeuds.lienAtelier,
    },
    chargeurs,
    surEchec: (nom, erreur) => echecs.push({ nom, erreur }),
  });
  return { noeuds, navigation, echecs };
}

test("une seule vue est visible à la fois", async () => {
  const { noeuds, navigation } = monterCoquille();

  await navigation.activer("parcelle");

  assert.equal(noeuds.vueParcelle.hidden, false);
  assert.equal(noeuds.vueChat.hidden, true);
  assert.equal(noeuds.vueAtelier.hidden, true);
});

test("le lien de la vue active est le seul marqué", async () => {
  const { noeuds, navigation } = monterCoquille();

  await navigation.activer("atelier");

  assert.equal(noeuds.lienAtelier.getAttribute("aria-current"), "page");
  assert.equal(noeuds.lienChat.getAttribute("aria-current"), null);
  assert.equal(noeuds.lienParcelle.getAttribute("aria-current"), null);
});

test("le lien précédemment actif perd sa marque", async () => {
  // Contre-epreuve du test precedent. Sans elle, un code qui POSE la marque sans
  // jamais la RETIRER reste vert : les tests n activaient qu une destination, et le
  // second lien marque ne se serait vu qu a l ecran, en scene.
  const { noeuds, navigation } = monterCoquille();

  await navigation.activer("atelier");
  await navigation.activer("chat");

  assert.equal(noeuds.lienChat.getAttribute("aria-current"), "page");
  assert.equal(noeuds.lienAtelier.getAttribute("aria-current"), null);
});

test("le module d'une destination n'est chargé qu'une seule fois", async () => {
  let appels = 0;
  const { navigation } = monterCoquille({
    parcelle: async () => {
      appels += 1;
    },
  });

  await navigation.activer("parcelle");
  await navigation.activer("chat");
  await navigation.activer("parcelle");

  // Le coût d'import est paye une fois ; la racine de composition de la parcelle
  // s'execute a l'import et rejouerait ses effets de bord a chaque retour.
  assert.equal(appels, 1);
});

test("une destination en panne n'emporte pas la coquille", async () => {
  const { noeuds, navigation, echecs } = monterCoquille({
    atelier: async () => {
      throw new Error("module absent");
    },
  });

  await navigation.activer("atelier");

  // La navigation a bien eu lieu : l'ecran montre la vue, vide plutot qu absente.
  assert.equal(noeuds.vueAtelier.hidden, false);
  assert.equal(echecs.length, 1);
  assert.equal(echecs[0].nom, "atelier");
  // Et on peut repartir ailleurs : la coquille n est pas restee bloquee.
  await navigation.activer("chat");
  assert.equal(noeuds.vueChat.hidden, false);
});

test("un chargeur en panne est retenté au retour", async () => {
  let appels = 0;
  const { navigation } = monterCoquille({
    atelier: async () => {
      appels += 1;
      throw new Error("réseau");
    },
  });

  await navigation.activer("atelier");
  await navigation.activer("chat");
  await navigation.activer("atelier");

  // Contre-epreuve du test precedent : on ne memorise que les chargements REUSSIS.
  // Une coupure reseau passagere ne doit pas condamner la destination pour la session.
  assert.equal(appels, 2);
});

test("une destination inconnue retombe sur la vue par défaut", async () => {
  const { noeuds, navigation } = monterCoquille();

  await navigation.activer("nimportequoi");

  assert.equal(noeuds.vueChat.hidden, false);
  assert.equal(navigation.actuelle(), "chat");
});

test("cliquer un lien active sa destination", async () => {
  const { noeuds, navigation } = monterCoquille();

  noeuds.lienParcelle.click();
  await navigation.enAttente();

  assert.equal(noeuds.vueParcelle.hidden, false);
  assert.equal(navigation.actuelle(), "parcelle");
});

/** Monte les noeuds d une destination : son lien, son contenu, son annonce. */
function monterDestinations() {
  const noeuds = monterDom([
    "lienParcelle",
    "lienAtelier",
    "contenuParcelle",
    "contenuAtelier",
    "annonceParcelle",
    "annonceAtelier",
  ]);
  return {
    noeuds,
    destinations: {
      parcelle: {
        lien: noeuds.lienParcelle,
        contenu: noeuds.contenuParcelle,
        annonce: noeuds.annonceParcelle,
      },
      atelier: {
        lien: noeuds.lienAtelier,
        contenu: noeuds.contenuAtelier,
        annonce: noeuds.annonceAtelier,
      },
    },
  };
}

test("une destination fermée reste visible et s'annonce à venir", () => {
  // La faire DISPARAITRE laisserait croire qu elle n existe pas. On la montre, on dit
  // qu elle n est pas encore ouverte, et on n ouvre pas une porte sur le vide.
  const { noeuds, destinations } = monterDestinations();

  const fermees = appliquerCapacites(destinations, {
    parcelles: true,
    rapports: false,
    vision: false,
  });

  assert.equal(noeuds.lienAtelier.hidden, false);
  assert.equal(noeuds.lienAtelier.getAttribute("data-etat"), "bientot");
  // Ce qu on voit en arrivant : l annonce, pas le formulaire qui echouerait.
  assert.equal(noeuds.annonceAtelier.hidden, false);
  assert.equal(noeuds.contenuAtelier.hidden, true);
  assert.deepEqual(fermees, ["atelier"]);
});

test("une destination ouverte montre son contenu, pas l'annonce", () => {
  // Contre-epreuve : sans elle, un code qui annonce TOUT resterait vert.
  const { noeuds, destinations } = monterDestinations();

  appliquerCapacites(destinations, { parcelles: true, rapports: true, vision: false });

  assert.equal(noeuds.annonceParcelle.hidden, true);
  assert.equal(noeuds.contenuParcelle.hidden, false);
  assert.equal(noeuds.lienParcelle.getAttribute("data-etat"), null);
});

test("sans réponse de l'API, l'écran reste exactement dans son état", () => {
  // L API injoignable, le chat ne marche pas non plus : annoncer « bientot » sur une
  // panne passagere serait un mensonge, et masquer serait pire. On ne touche a rien.
  const { noeuds, destinations } = monterDestinations();
  // Etat de depart tel qu il est dans la page : l annonce est repliee.
  noeuds.annonceAtelier.hidden = true;
  noeuds.contenuAtelier.hidden = false;

  const fermees = appliquerCapacites(destinations, null);

  assert.equal(noeuds.annonceAtelier.hidden, true);
  assert.equal(noeuds.contenuAtelier.hidden, false);
  assert.equal(noeuds.lienAtelier.getAttribute("data-etat"), null);
  assert.deepEqual(fermees, []);
});

test("une destination fermée ne charge pas son module", async () => {
  // Le module appellerait des routes qui ne sont pas montees : une erreur reseau
  // s afficherait par-dessus l annonce, et l ecran dirait deux choses a la fois.
  let charge = false;
  const { navigation } = monterCoquille({
    atelier: async () => {
      charge = true;
    },
  });
  navigation.fermer(["atelier"]);

  await navigation.activer("atelier");

  assert.equal(charge, false);
});

test("le fragment d'URL désigne la destination", () => {
  const noms = ["chat", "parcelle", "atelier"];
  assert.equal(nomDepuisHash("#/parcelle", noms, "chat"), "parcelle");
  assert.equal(nomDepuisHash("#parcelle", noms, "chat"), "parcelle");
  // Un lien partage ou un rechargement ne doit jamais tomber sur une page blanche.
  assert.equal(nomDepuisHash("", noms, "chat"), "chat");
  assert.equal(nomDepuisHash("#/inconnu", noms, "chat"), "chat");
  // Le fragment vient de la barre d adresse : il n a aucune autorite au-dela du nom.
  assert.equal(nomDepuisHash("#/<script>", noms, "chat"), "chat");
});

// ---------------------------------------------------------------------------
// Repli automatique sur CPU — l'écran doit le DIRE, et le dire gentiment.
// ---------------------------------------------------------------------------

/** Monte les destinations avec, en plus, les deux textes d'annonce et le bandeau. */
function monterAvecRepli() {
  const noeuds = monterDom([
    "vueChat",
    "vueParcelle",
    "vueAtelier",
    "lienChat",
    "lienParcelle",
    "lienAtelier",
    "contenuParcelle",
    "contenuAtelier",
    "annonceParcelle",
    "annonceAtelier",
    "texteAVenirParcelle",
    "texteRepliParcelle",
    "texteAVenirAtelier",
    "texteRepliAtelier",
    "bandeauRepli",
  ]);
  return {
    noeuds,
    destinations: {
      parcelle: {
        lien: noeuds.lienParcelle,
        contenu: noeuds.contenuParcelle,
        annonce: noeuds.annonceParcelle,
        texteAVenir: noeuds.texteAVenirParcelle,
        texteRepli: noeuds.texteRepliParcelle,
      },
      atelier: {
        lien: noeuds.lienAtelier,
        contenu: noeuds.contenuAtelier,
        annonce: noeuds.annonceAtelier,
        texteAVenir: noeuds.texteAVenirAtelier,
        texteRepli: noeuds.texteRepliAtelier,
      },
    },
  };
}

test("en repli, une fonction est « en pause » et non « bientôt »", () => {
  // « Bientot » serait un mensonge : la personne s en servait il y a une minute.
  const { noeuds, destinations } = monterAvecRepli();

  appliquerCapacites(destinations, { parcelles: false, rapports: false, vision: false }, true);

  assert.equal(noeuds.lienParcelle.getAttribute("data-etat"), "pause");
  assert.equal(noeuds.texteRepliParcelle.hidden, false);
  assert.equal(noeuds.texteAVenirParcelle.hidden, true);
});

test("hors repli, l'annonce reste celle d'une fonction pas encore ouverte", () => {
  // Contre-epreuve : sans elle, un code qui afficherait TOUJOURS le texte de repli
  // resterait vert, et la production annoncerait une panne inexistante.
  const { noeuds, destinations } = monterAvecRepli();

  appliquerCapacites(destinations, { parcelles: false, rapports: false, vision: false }, false);

  assert.equal(noeuds.lienParcelle.getAttribute("data-etat"), "bientot");
  assert.equal(noeuds.texteRepliParcelle.hidden, true);
  assert.equal(noeuds.texteAVenirParcelle.hidden, false);
});

test("le bandeau de repli s'affiche quand l'API le déclare", () => {
  const { noeuds } = monterAvecRepli();
  noeuds.bandeauRepli.hidden = true;

  afficherAvisRepli(noeuds.bandeauRepli, true);

  assert.equal(noeuds.bandeauRepli.hidden, false);
});

test("le bandeau reste masqué en marche normale", () => {
  const { noeuds } = monterAvecRepli();
  noeuds.bandeauRepli.hidden = false;

  afficherAvisRepli(noeuds.bandeauRepli, false);

  assert.equal(noeuds.bandeauRepli.hidden, true);
});

test("API injoignable : le bandeau ne s'invente pas", () => {
  // Annoncer un repli qui n a pas eu lieu inquieterait pour rien, et un bandeau
  // affiche par erreur devant 800 personnes est pire qu un silence.
  const { noeuds } = monterAvecRepli();
  noeuds.bandeauRepli.hidden = true;

  afficherAvisRepli(noeuds.bandeauRepli, null);

  assert.equal(noeuds.bandeauRepli.hidden, true);
});
