#!/usr/bin/env bash
# Sert le modèle de VISION (qwen3-vl 8B) sur le pod GPU, à côté de l'inférence texte.
#
# POURQUOI UN SECOND SERVEUR, ET PAS LE MÊME. Le texte et la vision sont deux jeux de
# poids distincts. Les charger dans un seul llama-server n'est pas possible ; on lance
# donc deux processus sur la même carte. Sur une 4090 (24 Go) : texte ~5 Go + KV ~5 Go,
# vision ~7 Go — il reste de la marge, mais c'est le budget à surveiller.
#
# D1 : ce serveur écoute sur le port 8002 du TUNNEL PRIVÉ uniquement. Jamais de proxy
# RunPod, jamais d'Ingress. L'API le consomme en interne, ce qui est la seule façon
# d'appliquer les garde-fous de sortie sur CHAQUE description produite.
#
# ⚠ Le service de vision n'exige aujourd'hui aucun jeton : le client `vlm.py` n'en
# envoie pas. Reste de sécurité relevé le 19/08, non corrigé — il n'est protégé que par
# le tunnel. À traiter avant toute ouverture plus large.
#
# Usage, SUR LE POD :
#   bash /workspace/vision_serve.sh
#
# Le téléchargement (~7 Go) est repris s'il a déjà commencé (`curl -C -`).
set -euo pipefail

VOLUME="${VOLUME:-/workspace}"
PORT="${PORT:-8002}"
LLAMA="${LLAMA:-${VOLUME}/llama.cpp/build/bin/llama-server}"
JOURNAL="${JOURNAL:-${VOLUME}/vision-server.log}"
# Une image consomme beaucoup de contexte. Un seul emplacement : le constat visuel est
# séquentiel, et `-c` n'est donc pas divisé (cf. l'incident du 11/09 côté texte).
CONTEXTE="${CONTEXTE:-8192}"
SLOTS="${SLOTS:-1}"

MODELE="${VOLUME}/qwen3-vl-8b-Q4_K_M.gguf"
MMPROJ="${VOLUME}/qwen3-vl-8b-mmproj-f16.gguf"
# Dépôt OFFICIEL Qwen. Les noms de fichiers y sont « Qwen3VL » sans tiret — une autre
# graphie rend 401 (Hugging Face répond « non autorisé », pas « introuvable », ce qui
# fait chercher une clé d'API là où il n'y a qu'une faute de frappe).
DEPOT="${DEPOT:-https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct-GGUF/resolve/main}"
FICHIER_MODELE="${FICHIER_MODELE:-Qwen3VL-8B-Instruct-Q4_K_M.gguf}"
FICHIER_MMPROJ="${FICHIER_MMPROJ:-mmproj-Qwen3VL-8B-Instruct-F16.gguf}"
# En dessous, ce n'est pas un modèle : c'est une page d'erreur enregistrée sous son nom.
TAILLE_MIN_OCTETS=100000000
TAILLE_MIN_MMPROJ=100000000

echo "==> 1/4  Vérifications"
command -v nvidia-smi >/dev/null 2>&1 || { echo "✗ pas de GPU ici." >&2; exit 1; }
nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader | sed 's/^/    /'
[ -x "${LLAMA}" ] || { echo "✗ llama-server absent : ${LLAMA}" >&2; exit 2; }

echo "==> 2/4  Poids du modèle de vision"
telecharger() {
  local url="$1" cible="$2" mini="$3"
  # Un téléchargement raté laisse un fichier de quelques octets (page d'erreur), que
  # « curl -C - » reprendrait ensuite comme un début valide. On le jette d'abord.
  if [ -f "${cible}" ] && [ "$(stat -c%s "${cible}" 2>/dev/null || echo 0)" -lt 1000000 ]; then
    echo "    reste tronqué écarté : $(basename "${cible}")"
    rm -f "${cible}"
  fi
  if [ -f "${cible}" ] && [ "$(stat -c%s "${cible}" 2>/dev/null || echo 0)" -ge "${mini}" ]; then
    echo "    déjà présent : $(basename "${cible}") ($(du -h "${cible}" | cut -f1))"
    return 0
  fi
  echo "    téléchargement de $(basename "${cible}")…"
  curl -fL -C - --retry 3 -o "${cible}" "${url}"
  local taille
  taille="$(stat -c%s "${cible}" 2>/dev/null || echo 0)"
  if [ "${taille}" -lt "${mini}" ]; then
    echo "✗ $(basename "${cible}") ne fait que ${taille} octets : téléchargement incomplet." >&2
    rm -f "${cible}"
    exit 5
  fi
}
telecharger "${DEPOT}/${FICHIER_MODELE}" "${MODELE}" "${TAILLE_MIN_OCTETS}"
telecharger "${DEPOT}/${FICHIER_MMPROJ}" "${MMPROJ}" "${TAILLE_MIN_MMPROJ}"

echo "==> 3/4  Démarrage"
if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "✗ Un serveur répond déjà sur le port ${PORT} (pkill -f 'port ${PORT}')." >&2
  exit 3
fi
nohup "${LLAMA}" \
  -m "${MODELE}" \
  --mmproj "${MMPROJ}" \
  --alias qwen3-vl \
  --host 0.0.0.0 --port "${PORT}" \
  -ngl 99 \
  -c "${CONTEXTE}" \
  -np "${SLOTS}" \
  -fa on \
  --cache-ram 1024 \
  --no-webui \
  >"${JOURNAL}" 2>&1 &
echo "    pid $!, journaux : ${JOURNAL}"

echo "==> 4/4  Attente du chargement"
for _ in $(seq 1 240); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "    prêt"
    nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader | sed 's/^/    VRAM : /'
    echo
    echo "OK → vision servie sur le tunnel, port ${PORT}."
    echo "   Côté cluster :  VISION_URL=http://<adresse_tailscale>:${PORT}  puis VISION_ENABLED=true"
    exit 0
  fi
  sleep 1
done
echo "✗ Toujours pas prêt après 240 s." >&2
tail -20 "${JOURNAL}" >&2
exit 4
