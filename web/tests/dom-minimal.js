/**
 * DOM minimal, écrit à la main — aucune dépendance.
 *
 * Le front du projet n'a aucune dépendance tierce, et ses tests non plus : ajouter
 * jsdom pour tester la vue reviendrait à installer un navigateur entier pour vérifier
 * qu'on appelle `textContent` au bon endroit. Ce module implémente le strict
 * sous-ensemble du DOM que la vue utilise réellement.
 *
 * Ce qu'il teste : NOTRE logique de rendu. Ce qu'il ne teste pas : le navigateur —
 * la mise en page, le style calculé, l'ordre de focus réel. Cette limite est assumée
 * et il faut la connaître : un test vert ici ne dit pas que l'écran est beau, il dit
 * que la bonne donnée arrive au bon nœud, par `textContent` et jamais par du HTML.
 */

/** Nœud minimal. */
class Noeud {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.parent = null;
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.type = "";
    this.value = "";
    this.dataset = {};
    this.attributs = new Map();
    this.ecouteurs = new Map();
    this.focalise = false;
    this._texte = "";
  }

  /** Texte propre au nœud ; le lire agrège celui des descendants, comme le DOM. */
  set textContent(valeur) {
    this._texte = String(valeur);
    this.children = [];
  }

  get textContent() {
    if (this.children.length === 0) return this._texte;
    return this._texte + this.children.map((enfant) => enfant.textContent).join("");
  }

  append(...noeuds) {
    for (const noeud of noeuds) {
      noeud.parent = this;
      this.children.push(noeud);
    }
  }

  replaceChildren(...noeuds) {
    this.children = [];
    this._texte = "";
    this.append(...noeuds);
  }

  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((enfant) => enfant !== this);
    this.parent = null;
  }

  setAttribute(nom, valeur) {
    this.attributs.set(nom, String(valeur));
  }

  getAttribute(nom) {
    return this.attributs.has(nom) ? this.attributs.get(nom) : null;
  }

  removeAttribute(nom) {
    this.attributs.delete(nom);
  }

  addEventListener(type, rappel) {
    if (!this.ecouteurs.has(type)) this.ecouteurs.set(type, []);
    this.ecouteurs.get(type).push(rappel);
  }

  /** Déclenche les écouteurs d'un type, sans propagation (la vue n'en dépend pas). */
  emettre(type, evenement = {}) {
    for (const rappel of this.ecouteurs.get(type) || []) {
      rappel({ target: this, preventDefault() {}, ...evenement });
    }
  }

  click() {
    this.emettre("click");
  }

  focus() {
    this.focalise = true;
  }

  /** Sélection volontairement pauvre : uniquement par nom de balise. */
  querySelectorAll(selecteur) {
    const tags = selecteur.split(",").map((part) => part.trim().toUpperCase());
    const trouves = [];
    const parcourir = (noeud) => {
      for (const enfant of noeud.children) {
        if (tags.includes(enfant.tagName)) trouves.push(enfant);
        parcourir(enfant);
      }
    };
    parcourir(this);
    return trouves;
  }

  /** Tous les descendants, à plat — commodité de test, pas une API du DOM. */
  descendants() {
    const tous = [];
    const parcourir = (noeud) => {
      for (const enfant of noeud.children) {
        tous.push(enfant);
        parcourir(enfant);
      }
    };
    parcourir(this);
    return tous;
  }

  /**
   * Le HTML **littéral** produit par ce nœud.
   *
   * Il n'existe que pour une assertion : si un jour un `innerHTML` se glissait dans la
   * vue, le balisage injecté apparaîtrait ici comme structure, alors qu'un texte posé
   * par `textContent` y reste du texte. C'est la propriété anti-XSS de cet écran.
   */
  texteBrut() {
    return this.textContent;
  }
}

/**
 * Installe un `document` global minimal et rend les références demandées.
 *
 * @param {Array<string>} identifiants Identifiants des nœuds à créer.
 * @returns {object} Les nœuds, par identifiant.
 */
export function monterDom(identifiants) {
  const parNom = {};
  for (const nom of identifiants) parNom[nom] = new Noeud();

  const corps = new Noeud("body");
  globalThis.document = {
    body: corps,
    createElement: (tag) => new Noeud(tag),
    getElementById: (nom) => parNom[nom] || null,
    addEventListener: () => {},
  };
  return parNom;
}

export { Noeud };
