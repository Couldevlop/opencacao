// COMPOSITION ROOT — assemble les couches et relie l'UI aux événements.
// C'est le seul module qui touche au DOM concret et qui injecte les dépendances
// (client API -> cas d'usage -> vue). Aucune logique métier ici.

import { creerCasUsageConseilStream } from "./application/conseil.js";
import { ConseilError, ErreurKind } from "./domain/models.js";
import { creerClientApi } from "./infrastructure/api-client.js";
import { creerVue } from "./ui/chat-view.js";

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
};

let baseUrl = localStorage.getItem(CLE_API) || API_DEFAUT;
let enCours = false;
// Historique de conversation (multi-tours) : permet au modèle d'ouvrir une
// discussion (ex. demander la ville) et de tenir compte des échanges précédents.
// Serveur sans état : on renvoie ces tours à chaque requête. Borné à 20 messages.
let historique = [];
const MAX_HISTORIQUE = 20;

// Injection de dépendances (dépendances pointant vers l'intérieur).
const client = creerClientApi(() => baseUrl);
const demanderConseilStream = creerCasUsageConseilStream(client);
const vue = creerVue(refs, {
  onFeedback: (interactionId, vote) => client.envoyerFeedback(interactionId, vote),
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
    // On envoie les tours précédents (pas la question courante).
    const conseil = await demanderConseilStream(
      q,
      (texte) => {
        if (!bulle) {
          vue.cacherSaisie();
          bulle = vue.demarrerBot();
        }
        bulle.append(texte);
      },
      historique
    );
    if (!bulle) {
      // Aucun token reçu (cas limite) : on rend la réponse d'un bloc.
      vue.cacherSaisie();
      bulle = vue.demarrerBot();
    }
    bulle.finaliser(conseil);
    // Mémorise l'échange pour permettre la discussion (clarifications) au tour suivant.
    if (conseil?.reponse) {
      historique.push({ role: "user", content: q }, { role: "assistant", content: conseil.reponse });
      if (historique.length > MAX_HISTORIQUE) historique = historique.slice(-MAX_HISTORIQUE);
    }
  } catch (e) {
    vue.cacherSaisie();
    vue.ajouterErreur(messageErreur(e));
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
  }
  fermerModale();
});

/* ---------- repli logos (sans onerror inline, conforme CSP) ---------- */
document.querySelectorAll("img.logo").forEach((img) => {
  img.addEventListener("error", () => img.classList.add("logo-missing"));
});

majBouton(false);
