/**
 * Navigation d'une coquille unique — une fenêtre, trois destinations.
 *
 * OpenCacao avait trois écrans dans trois pages : le chat, « Ma parcelle », l'atelier
 * de livrables. Chacun renvoyait vers l'accueil, l'accueil ne renvoyait vers aucun :
 * on ne pouvait les atteindre qu'en tapant l'URL. Ce module les réunit dans une seule
 * fenêtre, sur le modèle d'interaction d'un chat moderne — la barre latérale porte les
 * destinations, la zone centrale change sans rechargement.
 *
 * **Les modules sont chargés à la demande, pas au démarrage.** Les racines de
 * composition de la parcelle et de l'atelier s'exécutent à l'import : les charger
 * d'emblée ferait payer leur initialisation — et leurs éventuels appels réseau — à
 * quelqu'un qui vient seulement poser une question sur son cacao.
 *
 * **Une destination en panne n'emporte pas la coquille.** Le chat tourne en production
 * depuis des semaines ; il ne doit pas disparaître parce qu'un module livré la veille
 * lève à l'import. L'échec est signalé, la navigation continue.
 */

/**
 * Déduit la destination d'un fragment d'URL.
 *
 * Le fragment vient de la barre d'adresse : il n'a donc aucune autorité au-delà du
 * choix d'un nom DÉJÀ connu. On ne construit rien à partir de lui — on cherche une
 * correspondance dans la liste fournie, et tout le reste retombe sur le défaut.
 *
 * @param {string} hash Fragment, avec ou sans « # » et « / » de tête.
 * @param {Array<string>} noms Destinations connues.
 * @param {string} defaut Destination servie quand le fragment ne désigne rien.
 * @returns {string} Le nom de la destination.
 */
export function nomDepuisHash(hash, noms, defaut) {
  const nom = String(hash || "").replace(/^#/, "").replace(/^\//, "");
  return noms.includes(nom) ? nom : defaut;
}

// Quelle capacité déclarée par l'API ouvre quelle destination. La conversation n'y
// figure pas : elle est le produit, elle ne se ferme jamais.
const CAPACITE_PAR_DESTINATION = { parcelle: "parcelles", atelier: "rapports" };

/**
 * Retire de la barre latérale les destinations que l'API ne sert pas.
 *
 * « Ma parcelle » et l'atelier vivent derrière des drapeaux qu'on baisse — après une
 * démonstration, ou parce qu'une étude coûte des minutes de CPU quand l'inférence ne
 * sert qu'une requête à la fois. Les proposer quand même donnerait une porte qui ne
 * mène nulle part.
 *
 * @param {Object<string, object>} liens Nœud cliquable de chaque destination.
 * @param {object|null} capacites Capacités déclarées par `/v1/version`, ou `null` si
 *   l'API n'a pas répondu — auquel cas on ne masque RIEN : le chat ne fonctionne pas
 *   davantage, et faire disparaître les destinations donnerait à croire qu'elles ont
 *   été retirées.
 * @returns {Array<string>} Les destinations effectivement fermées.
 */
export function masquerDestinationsFermees(liens, capacites) {
  if (!capacites) return [];
  const fermees = [];
  for (const [destination, capacite] of Object.entries(CAPACITE_PAR_DESTINATION)) {
    const ouverte = capacites[capacite] === true;
    if (liens[destination]) liens[destination].hidden = !ouverte;
    if (!ouverte) fermees.push(destination);
  }
  return fermees;
}

/**
 * Crée la navigation entre les destinations de la coquille.
 *
 * @param {object} options Dépendances, injectées (aucune recherche dans le document).
 * @param {Object<string, object>} options.vues Nœud de chaque destination, par nom.
 *   Le PREMIER nom est la destination par défaut.
 * @param {Object<string, object>} options.liens Nœud cliquable de chaque destination.
 * @param {Object<string, function>} [options.chargeurs] Chargeur de module par nom,
 *   appelé à la première activation RÉUSSIE de la destination.
 * @param {function} [options.surEchec] Appelé avec (nom, erreur) si un chargeur lève.
 * @param {function} [options.surChangement] Appelé avec le nom une fois la vue montrée.
 *   L'appelant s'en sert pour tenir l'URL à jour ; le module, lui, ignore tout de
 *   `location` — c'est ce qui le rend testable sans navigateur.
 * @returns {{activer: function, actuelle: function, enAttente: function}} La navigation.
 */
export function creerNavigation({
  vues,
  liens,
  chargeurs = {},
  surEchec = () => {},
  surChangement = () => {},
}) {
  const noms = Object.keys(vues);
  const defaut = noms[0];
  const charges = new Set();
  let courante = null;
  let enCours = Promise.resolve();

  /**
   * Affiche une destination et masque les autres.
   *
   * @param {string} nom Destination visée ; inconnue, on sert la destination par défaut.
   * @returns {Promise<void>} Résolue quand le module de la destination a été tenté.
   */
  async function activer(nom) {
    const cible = noms.includes(nom) ? nom : defaut;
    courante = cible;
    for (const autre of noms) {
      vues[autre].hidden = autre !== cible;
      if (autre === cible) liens[autre].setAttribute("aria-current", "page");
      else liens[autre].removeAttribute("aria-current");
    }
    // Signalé AVANT le chargement, comme l'affichage : l'URL doit refléter ce que
    // l'écran montre déjà, pas attendre qu'un module lointain ait fini d'arriver.
    surChangement(cible);
    // La vue est montrée AVANT le chargement : on préfère un écran vide qui se
    // remplit à un clic qui ne produit rien pendant que le module arrive.
    if (chargeurs[cible] && !charges.has(cible)) {
      try {
        await chargeurs[cible]();
        // Mémorisé seulement en cas de succès : une coupure réseau passagère ne doit
        // pas condamner la destination pour toute la session.
        charges.add(cible);
      } catch (erreur) {
        surEchec(cible, erreur);
      }
    }
  }

  for (const nom of noms) {
    liens[nom].addEventListener("click", (evenement) => {
      evenement.preventDefault?.();
      enCours = activer(nom);
    });
  }

  return {
    activer(nom) {
      enCours = activer(nom);
      return enCours;
    },
    actuelle: () => courante,
    /** Promesse de l'activation en cours — les écouteurs de clic sont asynchrones. */
    enAttente: () => enCours,
  };
}
