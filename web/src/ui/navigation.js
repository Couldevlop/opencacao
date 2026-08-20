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
 * Annonce comme « à venir » les destinations que l'API ne sert pas encore.
 *
 * **On ne les fait pas disparaître, et c'est le point.** « Ma parcelle » et l'atelier
 * vivent derrière des drapeaux qu'on baisse — après une démonstration, ou parce qu'une
 * étude coûte des minutes de CPU quand l'inférence ne sert qu'une requête à la fois.
 * Les retirer de l'écran laisserait croire qu'ils n'existent pas ; les laisser ouverts
 * mènerait sur une erreur. On les montre, et on dit qu'ils ne sont pas encore ouverts.
 *
 * @param {Object<string, {lien: object, contenu: object, annonce: object}>} destinations
 *   Pour chaque destination : son entrée de barre latérale, son contenu, et le panneau
 *   qui annonce sa mise à disposition.
 * @param {object|null} capacites Capacités déclarées par `/v1/version`, ou `null` si
 *   l'API n'a pas répondu — auquel cas on ne touche à RIEN : annoncer « bientôt » sur
 *   une panne passagère serait un mensonge, et masquer serait pire.
 * @param {string} [profil] Profil matériel déclaré par `/v1/version` (« cpu » / « gpu »),
 *   ou vide si l'API n'a pas répondu — on ne devine alors aucune cause.
 * @param {boolean} [replie] Vrai quand l'API déclare un repli automatique sur CPU.
 *   Change le SENS de la fermeture : « en pause » (le service se protège) au lieu de
 *   « bientôt » (pas encore ouverte). Dire « bientôt » à quelqu'un qui se servait de la
 *   fonction une minute plus tôt serait faux, et donnerait l'image d'un produit
 *   inachevé là où il s'agit d'un service qui tient debout.
 * @returns {Array<string>} Les destinations pas encore ouvertes.
 */
export function appliquerCapacites(destinations, capacites, replie = false, profil = "") {
  if (!capacites) return [];
  const fermees = [];
  for (const [nom, capacite] of Object.entries(CAPACITE_PAR_DESTINATION)) {
    const cible = destinations[nom];
    if (!cible) continue;
    const ouverte = capacites[capacite] === true;
    // `data-etat` plutôt qu'une classe : l'état vient du serveur, la feuille de style
    // s'y accroche, et rien dans le code ne dépend d'un nom de classe décoratif.
    if (ouverte) cible.lien.removeAttribute("data-etat");
    // Trois états, et le mot doit dire la VRAIE raison. « Bientôt » décrit un produit
    // inachevé : l'écrire sur « Ma parcelle » ou « L'atelier », qui tournaient le matin
    // même, deprécie un travail livré (constat Waopron, 20/08). Sur un profil CPU, la
    // cause n'est pas l'inachèvement mais l'absence de la carte — on le dit.
    // Le repli reste prioritaire : « le service se protège » se lit avant sa cause.
    else if (replie) cible.lien.setAttribute("data-etat", "pause");
    else cible.lien.setAttribute("data-etat", profil === "cpu" ? "gpu" : "bientot");
    // TROIS textes préparés dans la page, un seul montré : on n'écrit jamais de HTML
    // depuis le code, et la formulation reste relisible par un humain dans le gabarit.
    // Le message doit dire la même vérité que la pastille — sur CPU, « pas encore
    // ouverte au public » était faux pour une fonction qui servait le matin même.
    const surCpu = !replie && profil === "cpu";
    if (cible.texteAVenir) cible.texteAVenir.hidden = replie || surCpu;
    if (cible.texteGpu) cible.texteGpu.hidden = !surCpu;
    if (cible.texteRepli) cible.texteRepli.hidden = !replie;
    cible.annonce.hidden = ouverte;
    cible.contenu.hidden = !ouverte;
    if (!ouverte) fermees.push(nom);
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
  // Destinations pas encore ouvertes. Renseignées après coup : les capacités arrivent
  // de l'API, donc plus tard que la première activation.
  const fermees = new Set();
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
    //
    // Une destination pas encore ouverte ne charge RIEN : son module appellerait des
    // routes non montées, et l'erreur réseau s'afficherait par-dessus l'annonce — un
    // écran qui dit deux choses à la fois.
    if (chargeurs[cible] && !charges.has(cible) && !fermees.has(cible)) {
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
    /**
     * Déclare les destinations pas encore ouvertes : elles ne chargeront pas de module.
     *
     * @param {Array<string>} noms Destinations annoncées comme à venir.
     */
    fermer(noms_fermes) {
      fermees.clear();
      for (const nom of noms_fermes) fermees.add(nom);
    },
    /** Promesse de l'activation en cours — les écouteurs de clic sont asynchrones. */
    enAttente: () => enCours,
  };
}

/**
 * Affiche — ou masque — le bandeau qui annonce le repli automatique sur CPU.
 *
 * Visible, calme, et non bloquant : la conversation continue de fonctionner, c'est
 * même tout l'objet du repli. Le bandeau explique ce qui est mis en pause et pourquoi,
 * sans jargon et sans dramatiser.
 *
 * On ne l'affiche QUE sur une déclaration explicite de l'API (`repli_cpu === true`).
 * Une réponse absente ou illisible laisse le bandeau masqué : afficher un avis de
 * panne qui n'a pas eu lieu, devant une salle, coûte plus cher qu'un silence.
 *
 * @param {object} bandeau Le nœud du bandeau.
 * @param {boolean|null} replie Ce que déclare `/v1/version`, ou `null` si l'API n'a
 *   pas répondu.
 */
export function afficherAvisRepli(bandeau, replie) {
  bandeau.hidden = replie !== true;
}
