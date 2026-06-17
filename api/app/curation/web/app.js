// Console de curation — session + login + curation (vanilla JS).

const $ = (id) => document.getElementById(id);

class NonAutorise extends Error {}

async function api(chemin, options) {
  const resp = await fetch(chemin, options);
  if (resp.status === 401) throw new NonAutorise();
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
        mot_de_passe: $("mot_de_passe").value,
      }),
    });
    $("mot_de_passe").value = "";
    montrerConsole();
  } catch (err) {
    erreur.textContent =
      err instanceof NonAutorise ? "Utilisateur ou mot de passe incorrect." : "Service indisponible.";
  } finally {
    btn.disabled = false;
    btn.textContent = "Se connecter";
  }
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
