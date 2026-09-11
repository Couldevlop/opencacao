#!/usr/bin/env bash
# Démarrage automatique du pod GPU loué — tunnel privé, puis inférence.
#
# POURQUOI CE FICHIER EST ICI. Il a vécu jusqu'au 10/09 uniquement sur le volume réseau
# RunPod. Le volume a disparu avec son pod, et avec lui la seule copie : il a fallu le
# réécrire de mémoire à 3 h du matin. Un fichier dont l'unique exemplaire est sur une
# machine louée n'est pas sauvegardé, il est emprunté. Il est donc versionné ici, et
# c'est cette copie qui fait foi.
#
# COMMENT IL EST APPELÉ. RunPod efface `/pre_start.sh` à chaque arrêt du conteneur mais
# conserve `/workspace`. La *Container Start Command* du pod le recopie au démarrage :
#
#   bash -c 'if [ -f /workspace/pre_start.sh ]; then cp -f /workspace/pre_start.sh /pre_start.sh; fi; exec /start.sh'
#
# INSTALLATION sur un pod neuf (le volume monté sur /workspace) :
#   cp deploy/scripts/pod/pre_start.sh /workspace/pre_start.sh && chmod +x /workspace/pre_start.sh
#
# Idempotent : relançable à la main sans rien casser (`bash /workspace/pre_start.sh`),
# ce qui est le mode dégradé quand la *Container Start Command* n'a pas été renseignée.
#
# CE QU'IL NE FAIT PAS, ET C'EST VOULU :
#   * il n'expose AUCUN port public — D1 : l'inférence n'est joignable que par le
#     tunnel privé, jamais par le proxy RunPod ;
#   * il ne va pas chercher le modèle sur le nœud Hetzner : cela exigerait d'y déposer
#     une clé du serveur de production, sur une machine dont on ne maîtrise ni le
#     disque ni la fin de vie. Le modèle se POUSSE depuis le nœud (`runpodctl send`) ;
#   * il ne passe JAMAIS le jeton par la ligne de commande : `--api-key-file`, parce
#     que `ps` est lisible par tout le monde sur le pod.
set -u

export PATH="/usr/local/bin:/usr/bin:/bin:${PATH:-}"

VOLUME="${VOLUME:-/workspace}"
MODELE="${MODELE:-${VOLUME}/opencacao-8b-Q4_K_M.gguf}"
JETON_FICHIER="${JETON_FICHIER:-${VOLUME}/.opencacao_api_key}"
LLAMA="${LLAMA:-${VOLUME}/llama.cpp/build/bin/llama-server}"
PORT="${PORT:-8000}"
CONTEXTE="${CONTEXTE:-8192}"
SLOTS="${SLOTS:-4}"
NOM_TUNNEL="${NOM_TUNNEL:-opencacao-runpod}"
JOURNAL="${JOURNAL:-${VOLUME}/demarrage.log}"
JOURNAL_LLAMA="${JOURNAL_LLAMA:-${VOLUME}/llama-server.log}"

dire() { echo "[$(date -Is)] $*" >> "${JOURNAL}"; }

dire "=== pre_start : demarrage ==="

# ── 1. Tunnel privé ──────────────────────────────────────────────────────────
# L'état vit sur le VOLUME : c'est lui qui garantit la même adresse d'un pod à l'autre.
# Quand il était sur le disque du conteneur (jusqu'au 19/08), chaque redémarrage donnait
# une nouvelle identité — et la reprise automatique du matin frappait à une porte morte.
command -v tailscaled >/dev/null 2>&1 || {
  dire "tailscale absent, installation"
  curl -fsSL https://tailscale.com/install.sh | sh >>"${JOURNAL}" 2>&1
}

mkdir -p /var/run/tailscale "${VOLUME}/tailscale"

if ! pgrep -x tailscaled >/dev/null 2>&1; then
  # `--tun=userspace-networking` : /dev/net/tun est absent des conteneurs RunPod. Le
  # mode utilisateur relaie l'entrant vers 127.0.0.1 — ce qui suffit, l'inférence
  # n'ayant aucune raison de sortir. Corollaire à connaître : le pod ne peut pas non
  # plus TIRER depuis le nœud par le tunnel (mesuré : 0 octet en 120 s).
  nohup tailscaled \
    --tun=userspace-networking \
    --state="${VOLUME}/tailscale/tailscaled.state" \
    --socket=/var/run/tailscale/tailscaled.sock \
    >>"${JOURNAL}" 2>&1 &
  sleep 3
  dire "tailscaled lance"
fi

tailscale up --hostname="${NOM_TUNNEL}" --accept-dns=false >>"${JOURNAL}" 2>&1 || \
  dire "⚠ tailscale up a echoue — le pod restera injoignable"
dire "adresse du tunnel : $(tailscale ip -4 2>/dev/null | head -1 || echo INCONNUE)"

# ── 2. Inférence ─────────────────────────────────────────────────────────────
if pgrep -f llama-server >/dev/null 2>&1; then
  dire "llama-server deja en cours — rien a faire"
  exit 0
fi

for requis in "${MODELE}" "${JETON_FICHIER}" "${LLAMA}"; do
  if [ ! -s "${requis}" ]; then
    dire "✗ manquant ou vide : ${requis} — inference NON demarree"
    exit 1
  fi
done

# Réglages identiques à ceux de training/scripts/pod_serve.sh : mêmes poids, même
# moteur, même chemin de code que le CPU de production. `-ngl 99` est le seul drapeau
# qui décide vraiment — sans lui le serveur démarre et sert… à la vitesse du CPU.
nohup "${LLAMA}" \
  -m "${MODELE}" \
  --alias opencacao-8b \
  --host 0.0.0.0 --port "${PORT}" \
  -ngl 99 \
  -c "${CONTEXTE}" \
  -np "${SLOTS}" \
  -fa on \
  --cache-type-k f16 --cache-type-v f16 \
  -b 2048 -ub 512 \
  --no-webui \
  --api-key-file "${JETON_FICHIER}" \
  >>"${JOURNAL_LLAMA}" 2>&1 &

dire "llama-server lance (pid $!), journal ${JOURNAL_LLAMA}"

# On attend la disponibilité réelle plutôt que de rendre la main sur un lancement : un
# processus vivant n'est pas un modèle chargé.
for _ in $(seq 1 180); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    dire "inference prete"
    exit 0
  fi
  sleep 1
done

dire "⚠ toujours pas pret apres 180 s — voir ${JOURNAL_LLAMA}"
exit 1
