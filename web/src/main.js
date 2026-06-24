// COMPOSITION ROOT — assemble les couches et relie l'UI aux événements.
// C'est le seul module qui touche au DOM concret et qui injecte les dépendances
// (client API -> cas d'usage -> vues). Aucune logique métier ici.

import { creerCasUsageConseilStream } from "./application/conseil.js";
import { creerCasUsageSessions } from "./application/sessions.js";
import { ConseilError, ErreurKind } from "./domain/models.js";
import { creerClientApi } from "./infrastructure/api-client.js";
import { ecrireSessionActive, lireSessionActive } from "./infrastructure/session-store-local.js";
import { creerVue } from "./ui/chat-view.js";
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
const vue = creerVue(refs, {
  onFeedback: (interactionId, vote) => client.envoyerFeedback(interactionId, vote),
});
const sidebar = creerSidebar(refs, {
  onNouvelle: () => nouvelleConversation(),
  onSelectionner: (id) => ouvrirConversation(id),
  onSupprimer: (id, titre) => supprimerConversation(id, titre),
});

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

/** Recharge la liste des conversations et surligne l'active (best-effort). */
async function rafraichirSidebar() {
  if (!sessionsDispo) return;
  try {
    const liste = await sessions.lister();
    sidebar.rendre(liste, sessionActive);
  } catch {
    /* la liste n'est pas critique : on n'interrompt pas l'expérience */
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
      await rafraichirSidebar();
      sidebar.fermer();
      return;
    }
    sessionActive = detail.session.id;
    ecrireSessionActive(sessionActive);
    vue.rejouer(detail.messages);
    await rafraichirSidebar();
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
  vue.reinitialiser();
  rafraichirSidebar();
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
    await rafraichirSidebar();
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
      await rafraichirSidebar();
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
      await rafraichirSidebar();
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
    // Serveur sans sessions (ou injoignable) : repli V1 « sans état », sidebar masquée.
    sessionsDispo = false;
    sessionActive = null;
    document.body.classList.remove("avec-sidebar");
    if (refs.toggle) refs.toggle.hidden = true;
    return;
  }

  sessionsDispo = true;
  document.body.classList.add("avec-sidebar");
  if (refs.toggle) refs.toggle.hidden = false;

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

majBouton(false);
initialiserSessions();
