// Console de curation — session + login + curation (vanilla JS).

const $ = (id) => document.getElementById(id);

class NonAutorise extends Error {}
class TropDeRequetes extends Error {}

async function api(chemin, options) {
  const resp = await fetch(chemin, options);
  if (resp.status === 401) throw new NonAutorise();
  if (resp.status === 429) {
    const corps = await resp.json().catch(() => ({}));
    throw new TropDeRequetes(corps.detail || "Trop de tentatives. Réessayez plus tard.");
  }
  if (!resp.ok) {
    const corps = await resp.json().catch(() => ({}));
    throw new Error(corps.detail || "Erreur " + resp.status);
  }
  return resp.status === 202 ? {} : resp.json();
}

/* ---------- Affichage login / console ---------- */
function montrerLogin() {
  $("console").hidden = true;
  $("login").hidden = false;
  $("utilisateur").focus();
}

function montrerConsole() {
  $("login").hidden = true;
  $("console").hidden = false;
  charger();
}

/* ---------- Statistiques ---------- */
async function rafraichirStats() {
  try {
    const s = await api("/api/stats");
    $("stats").textContent = `À curer : ${s.a_curer} · Validés : ${s.valides} · Rejetés : ${s.rejetes} · Total : ${s.total}`;
  } catch (e) {
    if (e instanceof NonAutorise) montrerLogin();
  }
}

/* ---------- Carte d'interaction ---------- */
function carte(item) {
  const frag = $("modele-carte").content.cloneNode(true);
  const art = frag.querySelector(".carte");
  art.querySelector(".confiance").textContent = "Confiance : " + (item.confiance || "?");
  art.querySelector(".votes").textContent = `👍 ${item.votes.up} · 👎 ${item.votes.down}`;
  const src = art.querySelector(".sources");
  src.textContent = item.sources && item.sources.length ? "📚 " + item.sources.join(", ") : "Aucune source";
  art.querySelector(".question").textContent = item.question;
  const reponse = art.querySelector(".reponse");
  reponse.value = item.reponse || "";
  const etat = art.querySelector(".etat");

  const verrouiller = (texte, classe) => {
    art.querySelectorAll("button").forEach((b) => (b.disabled = true));
    etat.textContent = texte;
    etat.className = "etat " + classe;
  };
  const gererErreur = (e) => {
    if (e instanceof NonAutorise) return montrerLogin();
    etat.textContent = "⚠ " + e.message;
    etat.className = "etat err";
  };

  art.querySelector(".valider").addEventListener("click", async () => {
    etat.textContent = "Envoi…";
    try {
      await api("/api/valider", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interaction_id: item.id, instruction: item.question, output: reponse.value }),
      });
      verrouiller("✓ Ajouté au corpus", "ok");
      setTimeout(() => art.remove(), 800);
      rafraichirStats();
    } catch (e) {
      gererErreur(e);
    }
  });

  art.querySelector(".rejeter").addEventListener("click", async () => {
    try {
      await api("/api/rejeter", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interaction_id: item.id }),
      });
      verrouiller("✕ Rejeté", "muted");
      setTimeout(() => art.remove(), 600);
      rafraichirStats();
    } catch (e) {
      gererErreur(e);
    }
  });

  return frag;
}

async function charger() {
  await rafraichirStats();
  await rafraichirDocuments();
  await rafraichirJobs();
  try {
    const items = await api("/api/a-curer");
    const liste = $("liste");
    liste.innerHTML = "";
    if (!items.length) {
      liste.innerHTML = '<p class="vide">🎉 Rien à curer pour le moment.</p>';
      return;
    }
    items.forEach((item) => liste.appendChild(carte(item)));
  } catch (e) {
    if (e instanceof NonAutorise) montrerLogin();
    else $("liste").innerHTML = '<p class="vide err">Erreur : ' + e.message + "</p>";
  }
}

/* ---------- Pipeline (documents, RAG, fine-tuning, jobs) ---------- */
const STATUT_LIB = {
  en_cours: { txt: "⏳ en cours", cls: "muted" },
  reussi: { txt: "✓ réussi", cls: "ok" },
  echec: { txt: "✕ échec", cls: "err" },
};
const TYPE_LIB = {
  rag_constitution: "Constitution RAG",
  rag_reindex: "RAG (faits curés)",
  finetuning_prepare: "Fine-tuning",
};
let sondageJobs = null;

function ligneJob(job) {
  const div = document.createElement("div");
  div.className = "job";
  const s = STATUT_LIB[job.statut] || { txt: job.statut, cls: "muted" };
  const type = TYPE_LIB[job.type] || job.type;
  div.innerHTML = `<span class="job-type">${type}</span><span class="etat ${s.cls}">${s.txt}</span>`;
  const msg = document.createElement("p");
  msg.className = "job-msg";
  msg.textContent = job.message || "…";
  div.appendChild(msg);
  // Procédure d'entraînement (fine-tuning) : affichée telle quelle.
  if (job.details && job.details.procedure) {
    const proc = $("procedure");
    proc.textContent = job.details.procedure;
    proc.hidden = false;
  }
  return div;
}

function badgeEtape(id, job) {
  const el = $(id);
  if (!el) return;
  if (!job) {
    el.textContent = "à faire";
    el.className = "badge-etat";
    return;
  }
  const s = STATUT_LIB[job.statut] || { txt: job.statut, cls: "muted" };
  el.textContent = s.txt;
  el.className = "badge-etat " + s.cls;
}

async function rafraichirJobs() {
  let jobs;
  try {
    jobs = await api("/api/jobs");
  } catch (e) {
    if (e instanceof NonAutorise) montrerLogin();
    return;
  }
  const conteneur = $("jobs");
  conteneur.innerHTML = "";
  if (!jobs.length) {
    conteneur.innerHTML = '<p class="vide">Aucun job pour l\'instant.</p>';
  } else {
    jobs.slice(0, 8).forEach((job) => conteneur.appendChild(ligneJob(job)));
  }
  // Badges d'état par étape (le 1er job d'un type est le plus récent).
  const dernier = (type) => jobs.find((j) => j.type === type);
  badgeEtape("etat-constituer", dernier("rag_constitution"));
  badgeEtape("etat-ft-badge", dernier("finetuning_prepare"));
  // Sonde tant qu'un job est en cours, sinon arrête.
  const actif = jobs.some((j) => j.statut === "en_cours");
  if (actif && !sondageJobs) sondageJobs = setInterval(rafraichirJobs, 4000);
  if (!actif && sondageJobs) {
    clearInterval(sondageJobs);
    sondageJobs = null;
  }
}

async function lancerAction(boutonId, etatId, chemin, libelle) {
  const btn = $(boutonId);
  const etat = $(etatId);
  btn.disabled = true;
  etat.textContent = "Envoi…";
  etat.className = "etat muted";
  try {
    await api(chemin, { method: "POST" });
    etat.textContent = "✓ " + libelle + " lancé(e)";
    etat.className = "etat ok";
    await rafraichirJobs();
  } catch (e) {
    if (e instanceof NonAutorise) return montrerLogin();
    etat.textContent = "⚠ " + e.message;
    etat.className = "etat err";
  } finally {
    btn.disabled = false;
  }
}

/* ---------- Étape ① Documents ---------- */
function lireBase64(fichier) {
  return new Promise((resoudre, rejeter) => {
    const lecteur = new FileReader();
    lecteur.onload = () => resoudre(String(lecteur.result).split(",")[1] || "");
    lecteur.onerror = () => rejeter(new Error("lecture du fichier impossible"));
    lecteur.readAsDataURL(fichier);
  });
}

function ligneDoc(doc) {
  const li = document.createElement("li");
  li.className = "doc";
  const nom = document.createElement("span");
  nom.className = "doc-nom";
  nom.textContent = doc.nom;
  const taille = document.createElement("span");
  taille.className = "doc-taille";
  taille.textContent = Math.max(1, Math.round(doc.taille / 1024)) + " Ko";
  const suppr = document.createElement("button");
  suppr.type = "button";
  suppr.className = "doc-suppr";
  suppr.textContent = "✕";
  suppr.title = "Supprimer";
  suppr.addEventListener("click", async () => {
    try {
      await api("/api/documents/" + encodeURIComponent(doc.nom), { method: "DELETE" });
      await rafraichirDocuments();
    } catch (e) {
      if (e instanceof NonAutorise) montrerLogin();
    }
  });
  li.append(nom, taille, suppr);
  return li;
}

async function rafraichirDocuments() {
  let docs;
  try {
    docs = await api("/api/documents");
  } catch (e) {
    if (e instanceof NonAutorise) montrerLogin();
    return;
  }
  const ul = $("docs-liste");
  ul.innerHTML = "";
  const badge = $("etat-docs");
  if (!docs.length) {
    ul.innerHTML = '<li class="vide-doc">Aucun document pour l\'instant.</li>';
    badge.textContent = "à faire";
    badge.className = "badge-etat";
  } else {
    docs.forEach((d) => ul.appendChild(ligneDoc(d)));
    badge.textContent = docs.length + " document(s)";
    badge.className = "badge-etat ok";
  }
}

$("fichier").addEventListener("change", async (e) => {
  const fichiers = Array.from(e.target.files || []);
  const etat = $("etat-upload");
  for (const f of fichiers) {
    etat.textContent = "Envoi de " + f.name + "…";
    etat.className = "etat muted";
    try {
      const contenu = await lireBase64(f);
      await api("/api/documents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nom: f.name, contenu_base64: contenu }),
      });
      etat.textContent = "✓ " + f.name;
      etat.className = "etat ok";
    } catch (err) {
      if (err instanceof NonAutorise) return montrerLogin();
      etat.textContent = "⚠ " + f.name + " : " + err.message;
      etat.className = "etat err";
    }
  }
  e.target.value = ""; // permet de re-téléverser le même fichier
  await rafraichirDocuments();
});

/* ---------- Étape ② Constitution RAG ---------- */
$("btn-constituer").addEventListener("click", () =>
  lancerAction("btn-constituer", "etat-rag", "/api/rag/constituer", "Constitution")
);
$("btn-rag").addEventListener("click", () =>
  lancerAction("btn-rag", "etat-rag", "/api/rag/reindex", "Reindex des faits curés")
);

/* ---------- Étape ③ Fine-tuning ---------- */
$("btn-ft").addEventListener("click", () =>
  lancerAction("btn-ft", "etat-ft", "/api/finetuning/prepare", "Préparation")
);

/* ---------- Authentification ---------- */
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("login-btn");
  const erreur = $("login-erreur");
  erreur.textContent = "";
  btn.disabled = true;
  btn.textContent = "Connexion…";
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        utilisateur: $("utilisateur").value.trim(),
        mot_de_passe: $("mot_de_passe").value.trim(),
      }),
    });
    $("mot_de_passe").value = "";
    montrerConsole();
  } catch (err) {
    if (err instanceof NonAutorise) {
      erreur.textContent = "Utilisateur ou mot de passe incorrect.";
    } else if (err instanceof TropDeRequetes) {
      erreur.textContent = err.message; // ex. « Trop de tentatives… »
    } else {
      erreur.textContent = "Service indisponible.";
    }
  } finally {
    btn.disabled = false;
    btn.textContent = "Se connecter";
  }
});

$("voir-mdp").addEventListener("change", (e) => {
  $("mot_de_passe").type = e.target.checked ? "text" : "password";
});

$("logout").addEventListener("click", async () => {
  try {
    await fetch("/api/logout", { method: "POST" });
  } catch {
    /* sans effet bloquant */
  }
  montrerLogin();
});

/* ---------- Démarrage ---------- */
(async () => {
  try {
    const etat = await fetch("/api/session").then((r) => r.json());
    if (etat.authentifie) montrerConsole();
    else montrerLogin();
  } catch {
    montrerLogin();
  }
})();
