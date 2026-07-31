/**
 * Tests du CLIENT D'API pour l'atelier — la couche qui n'en avait aucun.
 *
 *     node --test web/tests/*.test.js
 *
 * C'est ici qu'a vécu le défaut le plus visible du chantier : `exporterRapport`
 * préfixait la base deux fois, produisant « https://hoteHttps://hote/v1/… ». L'hôte
 * restait syntaxiquement valide, donc aucune exception — juste un échec DNS présenté à
 * l'utilisateur comme « API injoignable ». Les quatre boutons de téléchargement et
 * celui de l'historique étaient morts. Les tests d'application simulaient le client :
 * ils ne pouvaient pas le voir.
 *
 * `fetch` est remplacé par un double : aucun réseau, et l'URL réellement demandée
 * devient assertable.
 */

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

const BASE = "https://opencacao.example";

let appels;
let creerClientApi;

/** Installe le minimum d'environnement navigateur dont le client dépend. */
function monterEnvironnement(reponses) {
  const magasin = new Map([["opencacao.deviceId", "appareil-test"]]);
  globalThis.localStorage = {
    getItem: (cle) => (magasin.has(cle) ? magasin.get(cle) : null),
    setItem: (cle, valeur) => magasin.set(cle, String(valeur)),
    removeItem: (cle) => magasin.delete(cle),
  };

  appels = [];
  globalThis.fetch = async (url, init = {}) => {
    appels.push({ url, init });
    const suivante = reponses.shift();
    if (!suivante) throw new Error(`aucune réponse simulée pour ${url}`);
    if (suivante.reseau) throw new TypeError("échec réseau");
    return reponseSimulee(suivante);
  };
}

/** Fabrique une Response minimale — seulement ce que le client consulte. */
function reponseSimulee({ status = 200, corps = {}, entetes = {}, flux = null, octets = null }) {
  const table = new Map(Object.entries(entetes).map(([cle, val]) => [cle.toLowerCase(), val]));
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (nom) => table.get(nom.toLowerCase()) ?? null },
    async json() {
      return corps;
    },
    async text() {
      return typeof corps === "string" ? corps : JSON.stringify(corps);
    },
    async blob() {
      return octets ?? "octets";
    },
    body: flux ? fluxDepuis(flux) : null,
  };
}

/** Corps lisible morceau par morceau, comme celui d'un flux SSE. */
function fluxDepuis(morceaux) {
  const encodeur = new TextEncoder();
  let rang = 0;
  return {
    getReader() {
      return {
        async read() {
          if (rang >= morceaux.length) return { done: true, value: undefined };
          return { done: false, value: encodeur.encode(morceaux[rang++]) };
        },
      };
    },
  };
}

beforeEach(async () => {
  ({ creerClientApi } = await import("../src/infrastructure/api-client.js"));
});

describe("téléchargement d'un document", () => {
  it("demande UNE seule fois la base — le défaut qui tuait tous les exports", async () => {
    monterEnvironnement([{ octets: "docx" }]);
    const client = creerClientApi(() => BASE);
    await client.exporterRapport("r1", "docx");

    const { url } = appels[0];
    assert.equal(url, `${BASE}/v1/rapports/r1/export?format=docx`);
    // La preuve directe : la base ne doit apparaître qu'une fois dans l'URL.
    assert.equal(url.split(BASE).length - 1, 1, `base dupliquée : ${url}`);
  });

  it("construit le nom de fichier lui-même, sans croire le serveur", async () => {
    // Un `Content-Disposition` peut porter U+202E et faire lire « facture.exe »
    // comme « facture.docx » dans la barre de téléchargement. Le format est déjà
    // connu localement : on n'a aucune raison de faire confiance à l'en-tête.
    monterEnvironnement([
      {
        octets: "docx",
        entetes: { "Content-Disposition": 'attachment; filename="facture‮xcod.exe"' },
      },
    ]);
    const client = creerClientApi(() => BASE);
    const { nom } = await client.exporterRapport("r1", "docx");

    assert.equal(nom, "opencacao-r1.docx");
    assert.ok(!nom.includes("‮"));
    assert.ok(nom.endsWith(".docx"));
  });

  it("échappe l'identifiant et le format dans l'URL", async () => {
    monterEnvironnement([{ octets: "x" }]);
    await creerClientApi(() => BASE).exporterRapport("r 1/../secret", "d?x");
    assert.ok(!appels[0].url.includes("../"));
    assert.match(appels[0].url, /r%201%2F\.\.%2Fsecret/);
  });

  it("porte l'en-tête d'appareil, seule clé d'accès aux documents", async () => {
    monterEnvironnement([{ octets: "x" }]);
    await creerClientApi(() => BASE).exporterRapport("r1", "docx");
    assert.equal(appels[0].init.headers["X-Device-Id"], "appareil-test");
  });
});

describe("catalogue des types de documents", () => {
  it("rend les gabarits tels que le serveur les donne", async () => {
    monterEnvironnement([{ corps: [{ identifiant: "etude_filiere", sections: ["A"] }] }]);
    const gabarits = await creerClientApi(() => BASE).listerGabarits();
    assert.equal(gabarits.length, 1);
    assert.equal(appels[0].url, `${BASE}/v1/rapports/gabarits`);
  });

  it("distingue « atelier absent » d'une liste vide", async () => {
    // 404 = la route n'existe pas sur ce serveur. L'écran doit pouvoir le DIRE au
    // lieu d'afficher un catalogue vide, qui voudrait dire autre chose.
    monterEnvironnement([{ status: 404 }]);
    assert.equal(await creerClientApi(() => BASE).listerGabarits(), null);
  });

  it("rend une liste vide si le serveur répond autre chose qu'un tableau", async () => {
    monterEnvironnement([{ corps: { erreur: "inattendu" } }]);
    assert.deepEqual(await creerClientApi(() => BASE).listerGabarits(), []);
  });
});

describe("interprétation d'une demande", () => {
  it("poste la phrase et rend ce que le serveur a compris", async () => {
    monterEnvironnement([
      { corps: { gabarit: "etude_filiere", sujet: "la campagne", certaine: true, candidats: [] } },
    ]);
    const intention = await creerClientApi(() => BASE).comprendreDemande("une étude sur la campagne");

    assert.equal(appels[0].url, `${BASE}/v1/rapports/intention`);
    assert.equal(appels[0].init.method, "POST");
    assert.deepEqual(JSON.parse(appels[0].init.body), { demande: "une étude sur la campagne" });
    assert.equal(intention.gabarit, "etude_filiere");
  });

  it("remonte une erreur lisible quand l'API est injoignable", async () => {
    monterEnvironnement([{ reseau: true }]);
    await assert.rejects(() => creerClientApi(() => BASE).comprendreDemande("une étude"), {
      kind: "reseau",
    });
  });
});

describe("suivi du flux de rédaction", () => {
  it("relaie chaque événement, y compris un type inconnu", async () => {
    // Un serveur plus récent ne doit pas casser cet écran : on relaie tel quel, à
    // l'appelant d'ignorer ce qu'il ne connaît pas.
    monterEnvironnement([
      {
        flux: [
          'data: {"type":"progress","message":"Préparation…"}\n\n',
          'data: {"type":"section","titre":"Prix","faites":1,"total":2}\n\n',
          'data: {"type":"type_du_futur"}\n\ndata: {"type":"final"}\n\n',
        ],
      },
    ]);

    const recus = [];
    await creerClientApi(() => BASE).suivreRapport("r1", (e) => recus.push(e));

    assert.deepEqual(
      recus.map((e) => e.type),
      ["progress", "section", "type_du_futur", "final"]
    );
    assert.equal(recus[1].titre, "Prix");
  });

  it("recolle un événement coupé entre deux morceaux du flux", async () => {
    // Le réseau ne respecte aucune frontière : un bloc SSE peut arriver en deux
    // paquets. Sans tampon, la moitié des sections d'une étude se perdrait.
    monterEnvironnement([{ flux: ['data: {"type":"sec', 'tion","titre":"Prix"}\n\n'] }]);
    const recus = [];
    await creerClientApi(() => BASE).suivreRapport("r1", (e) => recus.push(e));
    assert.deepEqual(recus, [{ type: "section", titre: "Prix" }]);
  });

  it("ne s'interrompt pas sur un bloc illisible", async () => {
    monterEnvironnement([
      { flux: ["data: {ceci n'est pas du json}\n\n", 'data: {"type":"final"}\n\n'] },
    ]);
    const recus = [];
    await creerClientApi(() => BASE).suivreRapport("r1", (e) => recus.push(e));
    assert.deepEqual(recus, [{ type: "final" }]);
  });

  it("signale un document inconnu plutôt que de rester muet", async () => {
    monterEnvironnement([{ status: 404 }]);
    await assert.rejects(() => creerClientApi(() => BASE).suivreRapport("r1", () => {}), {
      kind: "introuvable",
    });
  });
});
