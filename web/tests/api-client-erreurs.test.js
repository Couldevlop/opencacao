/**
 * Un 404 doit dire ce qui est introuvable, pas ce que le helper a supposé.
 *
 * Bug remonté par Waopron le 19/08/2026 : après avoir produit une étude, cliquer sur
 * « Word » affichait **« Parcelle inconnue »**. Il n'y avait aucune parcelle dans
 * l'histoire.
 *
 * Cause : `appelParcelle` porte un message par défaut codé en dur
 * (`messageAbsence = "Parcelle inconnue"`) et le lève sur tout 404, **en ignorant le
 * détail renvoyé par le serveur**. L'API disait pourtant exactement ce qu'il fallait :
 * « Rapport inconnu ou non terminé. » Le helper étant partagé par les parcelles et
 * l'atelier, l'atelier héritait du vocabulaire des parcelles.
 *
 * Devant un public, un message d'erreur qui parle d'autre chose que de ce qu'on vient
 * de faire est pire qu'un message générique : il donne l'impression que le logiciel ne
 * sait pas où il en est.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { creerClientApi } from "../src/infrastructure/api-client.js";

/** Client dont chaque appel réseau rend la réponse fournie. */
function clientAvec(reponse) {
  globalThis.fetch = async () => reponse;
  return creerClientApi(() => "https://exemple.test");
}

/** Réponse 404 portant un détail serveur, comme le fait FastAPI. */
function reponse404(detail) {
  return {
    status: 404,
    ok: false,
    json: async () => ({ detail }),
    text: async () => JSON.stringify({ detail }),
  };
}

test("un rapport introuvable dit qu'il s'agit d'un rapport", async () => {
  const client = clientAvec(reponse404("Rapport inconnu ou non terminé."));

  await assert.rejects(
    () => client.exporterRapport("abc123", "docx"),
    (erreur) => {
      assert.match(erreur.message, /Rapport/i);
      assert.doesNotMatch(erreur.message, /Parcelle/i);
      return true;
    },
  );
});

test("sans détail serveur, le message par défaut reste affiché", async () => {
  // Contre-épreuve : un code qui viderait le message laisserait un « » à l'écran.
  const client = clientAvec({
    status: 404,
    ok: false,
    json: async () => {
      throw new Error("pas de JSON");
    },
    text: async () => "",
  });

  await assert.rejects(
    () => client.exporterRapport("abc123", "docx"),
    (erreur) => {
      assert.ok(erreur.message.length > 0);
      return true;
    },
  );
});
