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
    const compteur = $("onglet-compteur");
    if (compteur) compteur.textContent = s.a_curer ? `(${s.a_curer})` : "";
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
  recherche_sources: "Recherche sources",
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
  // Barre de progression (tâches longues en cours : recherche, constitution).
  const d = job.details || {};
  if (job.statut === "en_cours" && d.objectif) {
    const pct = Math.min(100, Math.round((100 * (d.courant || 0)) / d.objectif));
    const barre = document.createElement("div");
    barre.className = "progress";
    const jauge = document.createElement("div");
    jauge.className = "progress-jauge";
    jauge.style.width = pct + "%";
    barre.appendChild(jauge);
    const etiquette = document.createElement("span");
    etiquette.className = "progress-pct";
    etiquette.textContent = `${pct}% (${d.courant || 0}/${d.objectif})`;
    div.append(barre, etiquette);
  }
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
  // Sonde tant qu'un job est en cours (et rafraîchit la liste des documents,
  // qui se remplit pendant la recherche/constitution).
  const actif = jobs.some((j) => j.statut === "en_cours");
  if (actif && !sondageJobs) {
    sondageJobs = setInterval(() => {
      rafraichirJobs();
      rafraichirDocuments();
    }, 4000);
  }
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

$("btn-recherche").addEventListener("click", () =>
  lancerAction("btn-recherche", "etat-upload", "/api/recherche", "Recherche des sources")
);

$("btn-url").addEventListener("click", async () => {
  const champ = $("url-page");
  const url = champ.value.trim();
  const etat = $("etat-upload");
  if (!/^https?:\/\//.test(url)) {
    etat.textContent = "⚠ URL invalide (http/https)";
    etat.className = "etat err";
    return;
  }
  const btn = $("btn-url");
  btn.disabled = true;
  etat.textContent = "Ajout de la page…";
  etat.className = "etat muted";
  try {
    await api("/api/documents/url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    champ.value = "";
    etat.textContent = "✓ Page ajoutée";
    etat.className = "etat ok";
    await rafraichirDocuments();
  } catch (e) {
    if (e instanceof NonAutorise) return montrerLogin();
    etat.textContent = "⚠ " + e.message;
    etat.className = "etat err";
  } finally {
    btn.disabled = false;
  }
});

/* ---------- Étape ③ Fine-tuning ---------- */
$("btn-ft").addEventListener("click", () =>
  lancerAction("btn-ft", "etat-ft", "/api/finetuning/prepare", "Préparation")
);

/* ---------- Statistiques (visites) ---------- */
function drapeau(code) {
  if (!code || code.length !== 2 || !/^[A-Za-z]{2}$/.test(code)) return "🌍";
  return String.fromCodePoint(
    ...[...code.toUpperCase()].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65)
  );
}

function carteStat(libelle, valeur) {
  const d = document.createElement("div");
  d.className = "carte-stat";
  const v = document.createElement("div");
  v.className = "carte-stat-val";
  v.textContent = valeur ?? 0;
  const l = document.createElement("div");
  l.className = "carte-stat-lib";
  l.textContent = libelle;
  d.append(v, l);
  return d;
}

async function chargerStats() {
  let a;
  try {
    a = await api("/api/analytics");
  } catch (e) {
    if (e instanceof NonAutorise) montrerLogin();
    return;
  }
  const cartes = $("cartes-stats");
  cartes.innerHTML = "";
  [
    ["Aujourd'hui", a.aujourdhui],
    ["7 jours", a.semaine],
    ["Ce mois", a.mois],
    ["Cette année", a.annee],
    ["Total", a.total],
  ].forEach(([lib, val]) => cartes.appendChild(carteStat(lib, val)));

  const chart = $("chart-jours");
  chart.innerHTML = "";
  const jours = a.par_jour || [];
  const max = Math.max(1, ...jours.map((j) => j.n));
  jours.forEach((j) => {
    const col = document.createElement("div");
    col.className = "chart-col";
    col.title = `${j.date} : ${j.n} visite(s)`;
    const barre = document.createElement("div");
    barre.className = "chart-barre";
    barre.style.height = Math.round((100 * j.n) / max) + "%";
    col.appendChild(barre);
    chart.appendChild(col);
  });

  const pl = $("pays-liste");
  pl.innerHTML = "";
  const pays = a.par_pays || [];
  if (!pays.length) {
    pl.innerHTML = '<p class="vide">Aucune visite enregistrée pour l\'instant.</p>';
    return;
  }
  pays.forEach((p) => {
    const row = document.createElement("div");
    row.className = "pays-row";
    const nom = document.createElement("span");
    nom.textContent = `${drapeau(p.pays)} ${p.pays || "??"}`;
    const n = document.createElement("span");
    n.className = "pays-n";
    n.textContent = p.n;
    row.append(nom, n);
    pl.appendChild(row);
  });
}

/* ---------- Onglets (Curation / Pipeline / Statistiques) ---------- */
document.querySelectorAll(".onglet").forEach((onglet) => {
  onglet.addEventListener("click", () => {
    document.querySelectorAll(".onglet").forEach((o) => o.classList.toggle("actif", o === onglet));
    document.querySelectorAll(".vue").forEach((v) => {
      v.hidden = v.id !== onglet.dataset.vue;
    });
    if (onglet.dataset.vue === "vue-stats") chargerStats();
  });
});

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
