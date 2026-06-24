// Couche INFRASTRUCTURE — identifiant anonyme d'appareil (D1, V2).
// Un UUID opaque, généré une fois côté navigateur et conservé en localStorage,
// sert à cloisonner les conversations par appareil côté serveur (en-tête
// X-Device-Id). Ce n'est PAS une authentification ni une donnée personnelle : aucune
// IP, aucun nom — juste un jeton aléatoire pour qu'un navigateur ne voie que ses
// propres conversations. Tolérant aux pannes (mode privé, quota) : en dernier
// recours, un id de session volatile est renvoyé (cloisonnement le temps de l'onglet).

const CLE_DEVICE = "opencacao.deviceId";

let memoire = null; // repli si localStorage est indisponible

function genererId() {
  try {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return globalThis.crypto.randomUUID().replace(/-/g, "");
    }
  } catch {
    /* crypto indisponible : repli ci-dessous */
  }
  // Repli déterministe-faible suffisant pour un identifiant non sensible.
  return "dev" + Math.abs(Date.now() ^ (Math.random() * 1e9)).toString(36);
}

/** Identifiant d'appareil persistant (créé au premier appel). */
export function lireDeviceId() {
  if (memoire) return memoire;
  try {
    let id = localStorage.getItem(CLE_DEVICE);
    if (!id) {
      id = genererId();
      localStorage.setItem(CLE_DEVICE, id);
    }
    memoire = id;
    return id;
  } catch {
    // localStorage bloqué : on garde un id en mémoire pour la durée de l'onglet.
    if (!memoire) memoire = genererId();
    return memoire;
  }
}
