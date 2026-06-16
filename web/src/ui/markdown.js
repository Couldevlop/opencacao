// Couche PRÉSENTATION (utilitaire) — rendu markdown MINIMAL et SÛR.
// OWASP A03 (Injection/XSS) : on échappe TOUT le HTML d'abord, puis on
// applique seulement gras / italique / listes / paragraphes. Aucune balise
// arbitraire issue du modèle ne peut être injectée.

const ECHAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

const echapper = (s) => String(s).replace(/[&<>"']/g, (c) => ECHAP[c]);

function enligne(s) {
  return s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
}

/** Rend un texte (déjà échappé) en HTML restreint. */
export function rendreMarkdown(texte) {
  const lignes = echapper(texte || "").split(/\r?\n/);
  let html = "";
  let liste = false;
  for (const brute of lignes) {
    const l = brute.trim();
    if (/^[-*•]\s+/.test(l)) {
      if (!liste) {
        html += "<ul>";
        liste = true;
      }
      html += "<li>" + enligne(l.replace(/^[-*•]\s+/, "")) + "</li>";
    } else {
      if (liste) {
        html += "</ul>";
        liste = false;
      }
      if (l) html += "<p>" + enligne(l) + "</p>";
    }
  }
  if (liste) html += "</ul>";
  return html || "<p></p>";
}
