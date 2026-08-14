/**
 * La coquille unique tient-elle ses promesses envers les trois racines de composition ?
 *
 * Ce test lit le VRAI `index.html` et les VRAIES racines. Il existe parce que le
 * regroupement des trois écrans dans une seule page a créé deux façons de tout casser
 * en silence, dont aucune ne se voit dans un simulacre de DOM :
 *
 * 1. Un identifiant réclamé par une racine et absent de la page. `getElementById`
 *    rend `null`, et l'écran meurt au premier clic — en scène.
 * 2. Un identifiant présent DEUX fois. Trois pages ont fusionné, et elles portaient
 *    sept identifiants communs. Le navigateur n'en rend qu'un : les messages de
 *    l'atelier seraient partis dans la zone de statut de la parcelle.
 *
 * Il ne dit rien de la mise en page — cela se regarde à l'écran. Il dit que le contrat
 * entre le balisage et le code est tenu.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const PAGE = new URL("../index.html", import.meta.url);
const RACINES = ["../src/main.js", "../src/parcelle-main.js", "../src/rapport-main.js"];

/** Tous les identifiants déclarés dans la page, dans l'ordre d'apparition. */
function identifiantsDeLaPage() {
  const html = readFileSync(PAGE, "utf8");
  return [...html.matchAll(/\sid="([^"]+)"/g)].map((trouve) => trouve[1]);
}

/** Identifiants réclamés par une racine de composition, via son raccourci `$("…")`. */
function identifiantsReclames(chemin) {
  const source = readFileSync(new URL(chemin, import.meta.url), "utf8");
  return [...source.matchAll(/\$\("([^"]+)"\)/g)].map((trouve) => trouve[1]);
}

test("aucun identifiant n'est déclaré deux fois dans la page", () => {
  const identifiants = identifiantsDeLaPage();
  const vus = new Set();
  const doublons = new Set();
  for (const identifiant of identifiants) {
    if (vus.has(identifiant)) doublons.add(identifiant);
    vus.add(identifiant);
  }
  assert.deepEqual([...doublons], []);
});

for (const racine of RACINES) {
  test(`la page fournit tout ce que ${racine.split("/").pop()} réclame`, () => {
    const disponibles = new Set(identifiantsDeLaPage());
    const manquants = identifiantsReclames(racine).filter((id) => !disponibles.has(id));
    assert.deepEqual(manquants, []);
  });
}

test("les trois destinations et leurs liens existent", () => {
  const disponibles = new Set(identifiantsDeLaPage());
  for (const nom of ["Chat", "Parcelle", "Atelier"]) {
    assert.ok(disponibles.has(`vue${nom}`), `vue${nom} manquante`);
    assert.ok(disponibles.has(`lien${nom}`), `lien${nom} manquant`);
  }
});

test("les deux anciennes pages redirigent vers la destination correspondante", () => {
  // Des liens ont ete partages et mis en favori. Une page supprimee rendrait un 404 ;
  // une page conservee mais orpheline montrerait un ecran mort, ce qui est pire.
  for (const [fichier, fragment] of [
    ["../parcelle.html", "index.html#/parcelle"],
    ["../rapport.html", "index.html#/atelier"],
  ]) {
    const html = readFileSync(new URL(fichier, import.meta.url), "utf8");
    assert.match(html, /http-equiv="refresh"/);
    assert.ok(html.includes(fragment), `${fichier} ne redirige pas vers ${fragment}`);
    // La redirection ne doit pas dependre de JavaScript : la politique de securite
    // du contenu interdit le script en ligne.
    assert.ok(!/<script/.test(html), `${fichier} ne doit porter aucun script`);
  }
});
