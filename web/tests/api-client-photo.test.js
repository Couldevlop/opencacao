/**
 * Une photo jointe doit partir sur sa propre route, et le chemin textuel ne doit pas
 * bouger d'un octet.
 *
 * Le budget d'analyse d'une image se compte en dizaines de secondes, là où Cloudflare
 * coupe vers 100 s. Mélanger les deux chemins alourdirait le contrat de la totalité du
 * trafic — dont la quasi-totalité est du texte. Ce test verrouille l'aiguillage : il
 * échouerait si quelqu'un « simplifiait » en réunifiant les deux appels.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { creerClientApi } from "../src/infrastructure/api-client.js";

/** Flux SSE minimal : un token puis un done. */
function fluxSSE(texte) {
  const blocs = [
    `data: ${JSON.stringify({ type: "token", text: texte })}\n\n`,
    `data: ${JSON.stringify({ type: "done", sources: [], confiance: "moyenne" })}\n\n`,
  ];
  let i = 0;
  return {
    status: 200,
    ok: true,
    body: {
      getReader: () => ({
        read: async () =>
          i < blocs.length
            ? { done: false, value: new TextEncoder().encode(blocs[i++]) }
            : { done: true, value: undefined },
      }),
    },
  };
}

/** Capture l'URL et le corps du dernier appel. */
function espion(texte) {
  const vu = {};
  globalThis.fetch = async (url, options) => {
    vu.url = url;
    vu.corps = JSON.parse(options.body);
    return fluxSSE(texte);
  };
  return vu;
}

const PHOTO = {
  contenu_base64: "AAAA",
  largeur: 640,
  hauteur: 480,
  score_nettete: 90,
  luminance_moyenne: 140,
};

test("sans photo, la question part sur la route textuelle", async () => {
  const vu = espion("Taillez après la récolte.");
  const client = creerClientApi(() => "https://exemple.test");

  await client.demanderStream("Quand tailler ?", () => {});

  assert.equal(vu.url, "https://exemple.test/v1/chat/stream");
  assert.equal(vu.corps.images, undefined);
});

test("avec une photo, la question part sur la route photo", async () => {
  const vu = espion("Je vois des taches brunes.");
  const client = creerClientApi(() => "https://exemple.test");

  await client.demanderStream("Qu'est-ce que c'est ?", () => {}, { images: [PHOTO] });

  assert.equal(vu.url, "https://exemple.test/v1/chat/photo");
  assert.equal(vu.corps.images.length, 1);
});

test("une liste d'images vide reste du texte", async () => {
  const vu = espion("Réponse.");
  const client = creerClientApi(() => "https://exemple.test");

  await client.demanderStream("Quand tailler ?", () => {}, { images: [] });

  assert.equal(vu.url, "https://exemple.test/v1/chat/stream");
});

test("le flux de la route photo se lit comme celui du texte", async () => {
  espion("Je vois un jaunissement du limbe.");
  const client = creerClientApi(() => "https://exemple.test");
  const recus = [];

  const conseil = await client.demanderStream("Et ça ?", (t) => recus.push(t), {
    images: [PHOTO],
  });

  assert.equal(recus.join(""), "Je vois un jaunissement du limbe.");
  assert.equal(conseil.reponse, "Je vois un jaunissement du limbe.");
});
