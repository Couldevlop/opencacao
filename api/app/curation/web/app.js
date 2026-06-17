// Console de curation — logique UI (vanilla JS, sans dépendance).
// Lit /api/a-curer, affiche les interactions prioritaires, et permet de
// valider (vers le corpus) ou rejeter chaque réponse.

const $ = (id) => document.getElementById(id);

async function api(chemin, options) {
  const resp = await fetch(chemin, options);
  if (!resp.ok) {
    const corps = await resp.json().catch(() => ({}));
    throw new Error(corps.detail || "Erreur " + resp.status);
  }
  return resp.status === 202 ? {} : resp.json();
}

async function rafraichirStats() {
  try {
    const s = await api("/api/stats");
    $("stats").textContent = `À curer : ${s.a_curer} · Validés : ${s.valides} · Rejetés : ${s.rejetes} · Total : ${s.total}`;
  } catch {
    $("stats").textContent = "";
  }
}

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

  art.querySelector(".valider").addEventListener("click", async () => {
    etat.textContent = "Envoi…";
    try {
      await api("/api/valider", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          interaction_id: item.id,
          instruction: item.question,
          output: reponse.value,
        }),
      });
      verrouiller("✓ Ajouté au corpus", "ok");
      setTimeout(() => art.remove(), 800);
      rafraichirStats();
    } catch (e) {
      etat.textContent = "⚠ " + e.message;
      etat.className = "etat err";
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
      etat.textContent = "⚠ " + e.message;
      etat.className = "etat err";
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
    $("liste").innerHTML = '<p class="vide err">Erreur : ' + e.message + "</p>";
  }
}

charger();
