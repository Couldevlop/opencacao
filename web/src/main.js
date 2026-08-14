// COMPOSITION ROOT — assemble les couches et relie l'UI aux événements.
// C'est le seul module qui touche au DOM concret et qui injecte les dépendances
// (client API -> cas d'usage -> vues). Aucune logique métier ici.

import { creerCasUsageAuth } from "./application/auth.js";
import { creerCasUsageConseilStream } from "./application/conseil.js";
import { creerCasUsageSessions } from "./application/sessions.js";
import { ConseilError, ErreurKind } from "./domain/models.js";
import { creerClientApi } from "./infrastructure/api-client.js";
import { ecrireCompte, lireCompte } from "./infrastructure/auth-store-local.js";
import { ecrireSessionActive, lireSessionActive } from "./infrastructure/session-store-local.js";
import { creerVue } from "./ui/chat-view.js";
import { appliquerCapacites, creerNavigation, nomDepuisHash } from "./ui/navigation.js";
import { creerSidebar } from "./ui/sidebar-view.js";

const CLE_API = "opencacao.apiUrl";
// Par défaut, on appelle l'API sur la MÊME origine (cas où l'API sert l'UI ->
// zéro CORS). En service statique séparé (nginx), régler l'URL via ⚙️.
const API_DEFAUT = window.location.protocol.startsWith("http")
  ? window.location.origin
  : "http://localhost:8080";

const $ = (id) => document.getElementById(id);
const refs = {
  chat: $("chat"),
  thread: $("thread"),
  form: $("composer"),
  input: $("input"),
  send: $("send"),
  suggestions: $("suggestions"),
  settingsBtn: $("settingsBtn"),
  modal: $("modal"),
  apiUrl: $("apiUrl"),
  modalSave: $("modalSave"),
  modalCancel: $("modalCancel"),
  // Sidebar des conversations (V2)
  sidebar: $("sidebar"),
  backdrop: $("sidebarBackdrop"),
  toggle: $("sidebarToggle"),
  liste: $("sessionList"),
  nouvelle: $("nouvelleConv"),
  recherche: $("rechercheConv"),
  // Authentification (D2)
  compteBtn: $("compteBtn"),
  authModal: $("authModal"),
  authEmail: $("authEmail"),
  authSend: $("authSend"),
  authCancel: $("authCancel"),
  authMessage: $("authMessage"),
};

let baseUrl = localStorage.getItem(CLE_API) || API_DEFAUT;
let enCours = false;
// Conversation active (mémoire serveur, V2). null = aucune (créée à la 1re question).
let sessionActive = null;
// Les sessions sont-elles disponibles côté serveur ? Sinon, repli « sans état » V1.
let sessionsDispo = false;
// Historique client : utilisé UNIQUEMENT en repli (serveur sans sessions). Borné.
let historique = [];
const MAX_HISTORIQUE = 20;

// Injection de dépendances (dépendances pointant vers l'intérieur).
const client = creerClientApi(() => baseUrl);
const demanderConseilStream = creerCasUsageConseilStream(client);
const sessions = creerCasUsageSessions(client);
const auth = creerCasUsageAuth(client);
const vue = creerVue(refs, {
  onFeedback: (interactionId, vote) => client.envoyerFeedback(interactionId, vote),
});
const sidebar = creerSidebar(refs, {
  onNouvelle: () => nouvelleConversation(),
  onSelectionner: (id) => ouvrirConversation(id),
  onSupprimer: (id, titre) => supprimerConversation(id, titre),
  onRenommer: (id, titre) => renommerConversation(id, titre),
  onRechercher: (valeur) => rechercher(valeur),
});

// Terme de recherche courant (C5) : tant qu'il est non vide, la liste affiche les
// résultats filtrés plutôt que toutes les conversations.
let rechercheActive = "";

// Message doux et orienté producteur quand le modèle ne peut pas répondre.
const MESSAGE_SANS_REPONSE =
  "Les données dont je dispose ne me permettent pas de répondre à votre question pour le moment. Reformulez-la ou réessayez dans un instant.";

const MESSAGES_ERREUR = {
  [ErreurKind.VALIDATION]: "Votre question doit faire entre 3 et 2000 caractères.",
  [ErreurKind.RATE_LIMIT]: "Trop de questions à la suite. Patientez une minute avant de réessayer.",
  [ErreurKind.INDISPONIBLE]:
    "Le service est momentanément indisponible. Merci de réessayer dans un instant.",
  [ErreurKind.HTTP]: MESSAGE_SANS_REPONSE,
  // RESEAU n'arrive qu'en cas d'API réellement injoignable (config ⚙️ en mode séparé).
  [ErreurKind.RESEAU]: "Service injoignable pour le moment. Vérifiez votre connexion, puis réessayez.",
};

function messageErreur(e) {
  if (e instanceof ConseilError && MESSAGES_ERREUR[e.kind]) return MESSAGES_ERREUR[e.kind];
  return MESSAGE_SANS_REPONSE;
}

/* ---------- conversations (sidebar) ---------- */

/** Recharge la liste (ou les résultats de recherche) et surligne l'active. */
async function rafraichirListe() {
  if (!sessionsDispo) return;
  try {
    if (rechercheActive) {
      const res = await sessions.rechercher(rechercheActive);
      sidebar.rendre(res, sessionActive, `Aucun résultat pour « ${rechercheActive} ».`);
    } else {
      const liste = await sessions.lister();
      sidebar.rendre(liste, sessionActive);
    }
  } catch {
    /* la liste n'est pas critique : on n'interrompt pas l'expérience */
  }
}

/** Filtre la liste par un terme de recherche (C5). */
async function rechercher(valeur) {
  rechercheActive = (valeur || "").trim();
  await rafraichirListe();
}

/** Renomme une conversation après saisie d'un nouveau titre (C3). */
async function renommerConversation(id, titreActuel) {
  const saisi = window.prompt("Renommer la conversation :", titreActuel);
  if (saisi === null) return; // annulé
  const titre = saisi.trim();
  if (!titre || titre === titreActuel) return;
  try {
    await sessions.renommer(id, titre);
    await rafraichirListe();
  } catch (e) {
    vue.ajouterErreur(messageErreur(e));
  }
}

/** Ouvre une conversation existante : rejoue ses messages dans le fil. */
async function ouvrirConversation(id) {
  if (enCours || id === sessionActive) {
    sidebar.fermer();
    return;
  }
  try {
    const detail = await sessions.ouvrir(id);
    if (!detail) {
      // Conversation disparue côté serveur : on repart d'une page neuve.
      sessionActive = null;
      ecrireSessionActive(null);
      vue.reinitialiser();
      await rafraichirListe();
      sidebar.fermer();
      return;
    }
    sessionActive = detail.session.id;
    ecrireSessionActive(sessionActive);
    vue.rejouer(detail.messages);
    await rafraichirListe();
  } catch (e) {
    vue.ajouterErreur(messageErreur(e));
  } finally {
    sidebar.fermer();
  }
}

/** Démarre une nouvelle conversation (créée côté serveur à la 1re question). */
function nouvelleConversation() {
  sessionActive = null;
  ecrireSessionActive(null);
  historique = [];
  rechercheActive = "";
  sidebar.viderRecherche();
  vue.reinitialiser();
  rafraichirListe();
  sidebar.fermer();
  refs.input.focus();
}

/** Supprime une conversation (avec confirmation). */
async function supprimerConversation(id, titre) {
  if (!window.confirm(`Supprimer la conversation « ${titre} » ?`)) return;
  try {
    await sessions.supprimer(id);
    if (id === sessionActive) {
      sessionActive = null;
      ecrireSessionActive(null);
      vue.reinitialiser();
    }
    await rafraichirListe();
  } catch (e) {
    vue.ajouterErreur(messageErreur(e));
  }
}

/* ---------- envoi d'une question ---------- */
async function envoyer(question) {
  const q = (question || "").trim();
  if (enCours || !q) return;
  enCours = true;
  majBouton(false);
  vue.ajouterUtilisateur(q);
  refs.input.value = "";
  autogrow();
  vue.montrerSaisie();

  let bulle = null;
  try {
    // Avec sessions : on s'assure qu'une conversation existe (créée à la volée),
    // puis le serveur tient la mémoire. Sans sessions : repli historique client.
    let options;
    if (sessionsDispo) {
      if (!sessionActive) {
        const creee = await sessions.creer();
        sessionActive = creee.id;
        ecrireSessionActive(sessionActive);
      }
      options = { sessionId: sessionActive };
    } else {
      options = { historique };
    }
    // Étapes serveur (« J'analyse… », « Je consulte… ») affichées pendant l'attente.
    options.onProgress = (texte) => vue.majSaisie(texte);

    const conseil = await demanderConseilStream(
      q,
      (texte) => {
        if (!bulle) {
          vue.cacherSaisie();
          bulle = vue.demarrerBot();
        }
        bulle.append(texte);
      },
      options
    );
    if (!bulle) {
      // Aucun token reçu (cas limite) : on rend la réponse d'un bloc.
      vue.cacherSaisie();
      bulle = vue.demarrerBot();
    }
    bulle.finaliser(conseil);

    if (sessionsDispo) {
      // Le serveur a pu auto-générer le titre (B3) et réordonner : on rafraîchit.
      await rafraichirListe();
    } else if (conseil?.reponse) {
      historique.push({ role: "user", content: q }, { role: "assistant", content: conseil.reponse });
      if (historique.length > MAX_HISTORIQUE) historique = historique.slice(-MAX_HISTORIQUE);
    }
  } catch (e) {
    vue.cacherSaisie();
    if (e instanceof ConseilError && e.kind === ErreurKind.SESSION_INCONNUE) {
      // La conversation a disparu côté serveur : on repart proprement.
      sessionActive = null;
      ecrireSessionActive(null);
      await rafraichirListe();
      vue.ajouterErreur("Cette conversation n'est plus disponible. Reposez votre question pour en ouvrir une nouvelle.");
    } else {
      vue.ajouterErreur(messageErreur(e));
    }
  } finally {
    enCours = false;
    autogrow();
  }
}

/* ---------- interactions ---------- */
function majBouton(force) {
  const ok = force !== undefined ? force : refs.input.value.trim().length > 0;
  refs.send.disabled = !ok || enCours;
}

function autogrow() {
  refs.input.style.height = "auto";
  refs.input.style.height = Math.min(refs.input.scrollHeight, 180) + "px";
  majBouton();
}

refs.input.addEventListener("input", autogrow);
refs.input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    envoyer(refs.input.value);
  }
});
refs.form.addEventListener("submit", (e) => {
  e.preventDefault();
  envoyer(refs.input.value);
});
refs.suggestions?.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) envoyer(chip.textContent.trim());
});

/* ---------- paramètres API ---------- */
function ouvrirModale() {
  refs.apiUrl.value = baseUrl;
  refs.modal.hidden = false;
  refs.apiUrl.focus();
}
function fermerModale() {
  refs.modal.hidden = true;
}
refs.settingsBtn.addEventListener("click", ouvrirModale);
refs.modalCancel.addEventListener("click", fermerModale);
refs.modal.addEventListener("click", (e) => {
  if (e.target === refs.modal) fermerModale();
});
refs.modalSave.addEventListener("click", () => {
  const v = refs.apiUrl.value.trim();
  if (v) {
    baseUrl = v;
    localStorage.setItem(CLE_API, v);
    // Nouvelle API : on réévalue la disponibilité des sessions.
    initialiserSessions();
  }
  fermerModale();
});

/* ---------- repli logos (sans onerror inline, conforme CSP) ---------- */
document.querySelectorAll("img.logo").forEach((img) => {
  img.addEventListener("error", () => img.classList.add("logo-missing"));
});

/* ---------- coquille unique : trois destinations, une fenêtre ---------- */
// La barre latérale existe toujours désormais : elle porte la navigation, et non
// plus seulement les conversations. Sa présence ne dépend donc plus de l'API.
if (refs.sidebar) {
  document.body.classList.add("avec-sidebar");
  if (refs.toggle) refs.toggle.hidden = false;
}

const NOMS_VUES = ["chat", "parcelle", "atelier"];
// Ce que portaient les en-têtes des trois anciennes pages. Le nom du produit reste
// dans la barre latérale ; l'en-tête dit où l'on se trouve.
const TITRES_VUES = {
  chat: ["OpenCacao", "Conseil agronomique cacao · Côte d'Ivoire"],
  parcelle: ["Ma parcelle", "Photos, vidéo et tour de la parcelle"],
  atelier: ["L'atelier", "Des documents qui disent d'où ils viennent"],
};
const vues = { chat: $("vueChat"), parcelle: $("vueParcelle"), atelier: $("vueAtelier") };
const liensVues = { chat: $("lienChat"), parcelle: $("lienParcelle"), atelier: $("lienAtelier") };

if (vues.chat && vues.parcelle && vues.atelier) {
  const navigation = creerNavigation({
    vues,
    liens: liensVues,
    // Chargés à la demande : la racine de composition de chaque destination
    // s'exécute à l'import, et personne ne doit payer l'initialisation de l'atelier
    // pour venir poser une question sur son cacao.
    chargeurs: {
      parcelle: () => import("./parcelle-main.js"),
      atelier: () => import("./rapport-main.js"),
    },
    surChangement: (nom) => {
      const [titre, sous_titre] = TITRES_VUES[nom];
      // textContent, jamais innerHTML : ces chaînes sont à nous, la règle ne l'est pas.
      if ($("wordmark")) $("wordmark").textContent = titre;
      if ($("tagline")) $("tagline").textContent = sous_titre;
      // L'adresse d'accueil reste propre : on n'écrit un fragment que si l'on quitte
      // la destination par défaut, ou si l'URL en portait déjà un. Sinon, la page
      // publique deviendrait « /#/chat » au premier chargement, pour rien.
      const fragment = `#/${nom}`;
      const aDejaUnFragment = window.location.hash !== "";
      if ((nom !== "chat" || aDejaUnFragment) && window.location.hash !== fragment) {
        window.location.hash = fragment;
      }
      // Sur téléphone, la barre latérale est un tiroir : le clic doit le refermer,
      // sinon la destination choisie s'ouvre derrière un panneau opaque.
      refs.sidebar?.classList.remove("ouvert");
      if (refs.backdrop) refs.backdrop.hidden = true;
    },
    surEchec: (nom, erreur) => {
      // Une destination en panne ne doit pas emporter le chat. On le dit à la
      // console plutôt que de laisser un écran vide sans explication.
      console.error(`Destination « ${nom} » indisponible`, erreur);
    },
  });

  window.addEventListener("hashchange", () => {
    navigation.activer(nomDepuisHash(window.location.hash, NOMS_VUES, "chat"));
  });
  navigation.activer(nomDepuisHash(window.location.hash, NOMS_VUES, "chat"));

  // Ce que l'API ouvre RÉELLEMENT. Les drapeaux se baissent — après une démonstration,
  // ou parce qu'une étude coûte des minutes de CPU quand l'inférence ne sert qu'une
  // requête à la fois. La barre latérale doit suivre, sinon elle propose une porte qui
  // ne mène nulle part. Interrogé après le premier affichage : la conversation ne
  // dépend pas de cette réponse et ne doit pas l'attendre.
  (async () => {
    let capacites = null;
    try {
      const reponse = await fetch(`${baseUrl}/v1/version`);
      if (reponse.ok) capacites = (await reponse.json()).capacites;
    } catch {
      // API injoignable : on ne masque rien (cf. masquerDestinationsFermees).
    }
    const fermees = appliquerCapacites(
      {
        parcelle: {
          lien: liensVues.parcelle,
          contenu: $("contenuParcelle"),
          annonce: $("annonceParcelle"),
        },
        atelier: {
          lien: liensVues.atelier,
          contenu: $("contenuAtelier"),
          annonce: $("annonceAtelier"),
        },
      },
      capacites,
    );
    // Une destination annoncée ne charge pas son module : il appellerait des routes
    // non montées, et l'erreur s'afficherait par-dessus l'annonce.
    navigation.fermer(fermees);
    // Si l'on est DÉJÀ sur une destination pas encore ouverte — lien partagé, ou
    // capacités arrivées après l'affichage — on la réactive pour montrer l'annonce à
    // la place du contenu, sans quitter la destination : la personne a demandé cet
    // écran, on lui répond, on ne la renvoie pas ailleurs sans explication.
    if (fermees.includes(navigation.actuelle())) navigation.activer(navigation.actuelle());
  })();
}

/* ---------- amorçage des conversations ---------- */
async function initialiserSessions() {
  if (!refs.sidebar) {
    sessionsDispo = false;
    return;
  }
  let liste;
  try {
    liste = await sessions.lister();
  } catch {
    // Serveur sans sessions (ou injoignable) : repli V1 « sans état ».
    // La barre latérale RESTE — elle porte la navigation entre les trois
    // destinations depuis le regroupement en une seule fenêtre. La masquer, comme
    // on le faisait, rendrait « Ma parcelle » et l'atelier inatteignables dès que
    // l'API des sessions bronche. Seule la liste des conversations s'efface.
    sessionsDispo = false;
    sessionActive = null;
    document.body.classList.remove("avec-conversations");
    return;
  }

  sessionsDispo = true;
  document.body.classList.add("avec-conversations");

  // Reprise de la dernière conversation ouverte (persistée localement, C4).
  const stocke = lireSessionActive();
  if (stocke) {
    try {
      const detail = await sessions.ouvrir(stocke);
      if (detail) {
        sessionActive = detail.session.id;
        vue.rejouer(detail.messages);
      } else {
        ecrireSessionActive(null);
      }
    } catch {
      /* reprise best-effort : on reste sur l'accueil */
    }
  }
  sidebar.rendre(liste, sessionActive);
}

/* ---------- authentification par lien magique (D2) ---------- */
function majCompte() {
  const compte = lireCompte();
  if (compte) {
    refs.compteBtn.textContent = "Déconnexion · " + (compte.email || "compte");
    refs.compteBtn.title = "Se déconnecter de " + (compte.email || "votre compte");
  } else {
    refs.compteBtn.textContent = "Se connecter";
    refs.compteBtn.title = "Recevoir un lien de connexion par email";
  }
}

function messageAuth(texte, type) {
  refs.authMessage.textContent = texte;
  refs.authMessage.className = "auth-message" + (type ? " " + type : "");
  refs.authMessage.hidden = !texte;
}

function ouvrirAuth() {
  messageAuth("", "");
  refs.authEmail.value = "";
  refs.authModal.hidden = false;
  refs.authEmail.focus();
}
function fermerAuth() {
  refs.authModal.hidden = true;
}

/** Réinitialise l'espace de conversations après un changement d'identité. */
function appliquerIdentite() {
  sessionActive = null;
  ecrireSessionActive(null);
  rechercheActive = "";
  sidebar.viderRecherche();
  vue.reinitialiser();
  rafraichirListe();
}

async function envoyerLien() {
  const email = (refs.authEmail.value || "").trim();
  refs.authSend.disabled = true;
  try {
    await auth.demander(email);
    messageAuth("Lien envoyé ! Consultez votre email et cliquez sur le lien.", "ok");
  } catch (e) {
    if (e instanceof ConseilError && e.kind === ErreurKind.VALIDATION) {
      messageAuth("Adresse email invalide.", "err");
    } else if (e instanceof ConseilError && e.kind === ErreurKind.AUTH_INDISPONIBLE) {
      messageAuth("La connexion par email n'est pas activée sur ce serveur.", "err");
    } else if (e instanceof ConseilError && e.kind === ErreurKind.RATE_LIMIT) {
      messageAuth("Trop de demandes. Réessayez dans une minute.", "err");
    } else {
      messageAuth("Service indisponible, réessayez plus tard.", "err");
    }
  } finally {
    refs.authSend.disabled = false;
  }
}

function deconnexion() {
  ecrireCompte(null);
  majCompte();
  appliquerIdentite();
}

refs.compteBtn?.addEventListener("click", () => {
  if (lireCompte()) {
    if (window.confirm("Se déconnecter ?")) deconnexion();
  } else {
    ouvrirAuth();
  }
});
refs.authSend?.addEventListener("click", envoyerLien);
refs.authCancel?.addEventListener("click", fermerAuth);
refs.authModal?.addEventListener("click", (e) => {
  if (e.target === refs.authModal) fermerAuth();
});
refs.authEmail?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    envoyerLien();
  }
});

/** Au chargement : si l'URL porte ?auth=<token>, vérifier le lien et connecter. */
async function traiterLienMagique() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("auth");
  if (!token) return;
  // Nettoie l'URL quoi qu'il arrive : le jeton ne doit pas rester visible/partageable.
  history.replaceState({}, document.title, window.location.pathname);
  try {
    const compte = await auth.verifier(token);
    if (compte) {
      ecrireCompte(compte);
      majCompte();
      appliquerIdentite();
    } else {
      vue.ajouterErreur("Lien de connexion invalide ou expiré. Demandez-en un nouveau.");
    }
  } catch {
    /* auth indisponible / réseau : on reste anonyme */
  }
}

majBouton(false);
majCompte();
initialiserSessions().then(traiterLienMagique);
