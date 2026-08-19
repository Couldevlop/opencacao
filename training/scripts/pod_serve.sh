#!/usr/bin/env bash
# Sert OpenCacao-8B sur le GPU d'un pod loué (RunPod), avec llama.cpp.
#
# POURQUOI llama.cpp ET PAS vLLM. La spec §4.4 prévoyait vLLM sur un modèle fusionné
# quantifié en AWQ. Ce script prend l'autre chemin, et c'est un choix mesuré :
#
#   * on sert le GGUF Q4_K_M **déjà en production** — mêmes poids, même moteur, même
#     chemin de code que le CPU. Rien de nouveau entre l'API et le modèle : les
#     garde-fous et les gabarits de prompt n'ont pas à être revalidés la veille ;
#   * 5,2 Go au lieu de 16 : le décodage étant borné par la bande passante mémoire, un
#     modèle 3× plus petit se relit 3× plus vite. Sur une carte à ~900 Go/s, c'est
#     ~170 tok/s de plafond théorique contre ~56 en BF16 ;
#   * il reste alors >20 Go de VRAM libres sur une carte de 32 Go — de quoi loger le
#     modèle de vision à côté, si on l'allume ;
#   * aucun modèle sous licence à télécharger, aucune fusion LoRA, aucun jeton
#     Hugging Face. Moins d'étapes = moins de choses qui cassent un matin de
#     présentation.
#
# Ce qu'on perd : le regroupement de requêtes de vLLM (§4.4). Sans objet pour une
# démonstration menée par une personne ; à reconsidérer pour l'ouverture au public.
#
# CE QUE CE SCRIPT NE FAIT PAS, ET C'EST VOULU :
#   * il ne va PAS chercher le modèle sur le nœud Hetzner. Tirer depuis le pod
#     exigerait d'y déposer une clé SSH du serveur de production — sur une machine
#     LOUÉE, dont on ne maîtrise ni le disque ni la fin de vie. Le modèle se POUSSE
#     depuis le nœud (la commande exacte est affichée s'il manque) ;
#   * il n'ouvre aucun port public. D1 interdit d'exposer l'inférence : le pod n'est
#     joignable que par le tunnel privé (§4.5).
#
# Usage, SUR LE POD :
#   INFERENCE_API_KEY=<jeton> bash pod_serve.sh
#   MODELE=/runpod-volume/opencacao-8b-Q4_K_M.gguf bash pod_serve.sh   # autre chemin
#
# Sortie : llama-server en écoute sur 0.0.0.0:8000, journaux dans ${JOURNAL},
# et le temps « pod froid -> premier token » (mitigation M3 de la spec).

set -euo pipefail

# Le volume RÉSEAU, pas le disque du conteneur : il survit à la perte du pod, ce qui
# est toute la raison d'être de la mitigation M1. Un pod perdu redevient un pod prêt
# en une minute au lieu d'un quart d'heure.
MODELE="${MODELE:-/runpod-volume/opencacao-8b-Q4_K_M.gguf}"
PORT="${PORT:-8000}"
CONTEXTE="${CONTEXTE:-8192}"
# Emplacements parallèles : l'atelier de livrables enchaîne une génération par
# section. Sans slots, une étude bloquerait la conversation — le défaut du CPU, et
# précisément ce que le GPU doit supprimer.
SLOTS="${SLOTS:-4}"
JOURNAL="${JOURNAL:-/workspace/llama-server.log}"
LLAMA="${LLAMA:-/workspace/llama.cpp}"

echo "==> 1/6  Vérifications"

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "✗ nvidia-smi absent : ce script doit tourner sur un pod GPU." >&2
  exit 1
fi
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader | sed 's/^/    /'

# Capacité de calcul -> architecture CUDA à compiler. À faire explicitement : les
# cartes Blackwell (sm_120) sont trop récentes pour les binaires précompilés courants,
# et une compilation « générique » produit un binaire qui démarre puis tombe sur le
# premier noyau. Mieux vaut compiler pour la carte qu'on a.
CAP="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d '.[:space:]')"
echo "    architecture CUDA visée : sm_${CAP}"
if [ "${CAP}" -ge 120 ] 2>/dev/null; then
  echo "    ⚠ Blackwell : compilation depuis les sources OBLIGATOIRE (binaires publics trop anciens)"
fi

if [ -z "${INFERENCE_API_KEY:-}" ]; then
  echo "✗ INFERENCE_API_KEY est vide." >&2
  echo "  L'inférence ne doit JAMAIS répondre sans jeton, même derrière un tunnel" >&2
  echo "  (défense en profondeur, spec §4.5). Générer un jeton :" >&2
  echo "      openssl rand -hex 32" >&2
  echo "  puis créer le MÊME jeton côté cluster :" >&2
  echo "      kubectl -n opencacao create secret generic opencacao-inference \\" >&2
  echo "        --from-literal=INFERENCE_API_KEY=<jeton>" >&2
  exit 2
fi

echo "==> 2/6  Modèle"

if [ ! -f "${MODELE}" ]; then
  echo "✗ Modèle absent : ${MODELE}" >&2
  echo "" >&2
  echo "  À POUSSER DEPUIS LE NŒUD HETZNER (jamais tiré depuis ici : on ne dépose pas" >&2
  echo "  une clé du serveur de production sur une machine louée). Sur le nœud :" >&2
  echo "" >&2
  echo "      scp -P <port_ssh_du_pod> /opt/opencacao/models/opencacao-8b-Q4_K_M.gguf \\" >&2
  echo "          root@<hote_du_pod>:${MODELE}" >&2
  echo "" >&2
  echo "  ~5,2 Go, une à deux minutes de serveur à serveur." >&2
  exit 3
fi
TAILLE="$(du -h "${MODELE}" | cut -f1)"
echo "    ${MODELE}  (${TAILLE})"

echo "==> 3/6  llama.cpp compilé pour cette carte"

if [ ! -x "${LLAMA}/build/bin/llama-server" ]; then
  command -v cmake >/dev/null 2>&1 || pip install --no-cache-dir cmake
  [ -d "${LLAMA}" ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp "${LLAMA}"
  # -DGGML_CUDA=ON : sans ce drapeau la compilation réussit et le serveur démarre…
  # entièrement sur CPU. On aurait loué un GPU pour rien, et on ne s'en apercevrait
  # qu'au débit. Le message d'aide de l'étape 6 le vérifie explicitement.
  cmake -S "${LLAMA}" -B "${LLAMA}/build" \
    -DGGML_CUDA=ON \
    -DCMAKE_CUDA_ARCHITECTURES="${CAP}" \
    -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build "${LLAMA}/build" --config Release -j"$(nproc)" --target llama-server
fi
echo "    $("${LLAMA}/build/bin/llama-server" --version 2>&1 | head -1)"

echo "==> 4/6  Démarrage"

# Un serveur déjà en écoute sur le port se ferait doubler en silence : on le dit.
if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
  echo "✗ Un serveur répond déjà sur le port ${PORT}. L'arrêter d'abord :" >&2
  echo "      pkill -f llama-server" >&2
  exit 4
fi

DEPART="${SECONDS}"
# Réglages GPU, et en quoi ils diffèrent du CPU (cf. deploy/k8s/inference.yaml) :
#   -ngl 99            toutes les couches sur la carte — le seul drapeau qui compte
#   -c 8192            contexte doublé : la VRAM le permet, le CPU non
#   --cache-type-* f16 le KV en q8_0 était une économie de RAM devenue inutile ici
#   -np                slots parallèles (chat + atelier en même temps)
#   --api-key          l'inférence ne répond jamais nue, même derrière le tunnel
#   --no-webui         llama.cpp sert une interface web ; l'inférence n'a pas à en
#                      exposer une, fût-ce sur un réseau privé (D1, moindre surface)
nohup "${LLAMA}/build/bin/llama-server" \
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
  --api-key "${INFERENCE_API_KEY}" \
  >"${JOURNAL}" 2>&1 &
PID=$!
echo "    pid ${PID}, journaux : ${JOURNAL}"

echo "==> 5/6  Attente du chargement"

PRET=0
for _ in $(seq 1 180); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    PRET=1
    break
  fi
  if ! kill -0 "${PID}" 2>/dev/null; then
    echo "✗ Le serveur s'est arrêté pendant le chargement. Dernières lignes :" >&2
    tail -20 "${JOURNAL}" >&2
    exit 5
  fi
  sleep 1
done
[ "${PRET}" = "1" ] || { echo "✗ Toujours pas prêt après 180 s." >&2; tail -20 "${JOURNAL}" >&2; exit 6; }
CHARGEMENT="$((SECONDS - DEPART))"

echo "==> 6/6  Vérification"

# Les couches sont-elles VRAIMENT sur la carte ? Une compilation sans CUDA donne un
# serveur parfaitement fonctionnel… et lent comme le CPU. C'est le seul contrôle qui
# distingue « ça marche » de « on a loué un GPU pour rien ».
if grep -qiE "offloaded .*/.* layers to GPU|CUDA[0-9]* buffer" "${JOURNAL}"; then
  grep -iE "offloaded .*layers to GPU" "${JOURNAL}" | tail -1 | sed 's/^/    /'
else
  echo "    ⚠ AUCUNE trace de délestage GPU dans les journaux — le modèle tourne"
  echo "      probablement sur CPU. Vérifier la compilation (-DGGML_CUDA=ON)."
fi

# Un 401 sans jeton est une BONNE nouvelle : le serveur est protégé.
CODE_NU="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:${PORT}/v1/models" || echo 000)"
echo "    /v1/models sans jeton  -> HTTP ${CODE_NU} (401 attendu)"
[ "${CODE_NU}" = "401" ] || echo "    ⚠ L'inférence répond SANS jeton — vérifier --api-key."

DEBUT_GEN="${SECONDS}"
REPONSE="$(curl -fsS --max-time 120 "http://127.0.0.1:${PORT}/v1/chat/completions" \
  -H "Authorization: Bearer ${INFERENCE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"opencacao-8b","max_tokens":64,"temperature":0.2,
       "messages":[{"role":"user","content":"En une phrase : quand tailler un cacaoyer ?"}]}' \
  || echo '')"
GEN="$((SECONDS - DEBUT_GEN))"

if [ -z "${REPONSE}" ]; then
  echo "    ⚠ La génération de test a échoué — voir ${JOURNAL}"
else
  echo "${REPONSE}" | head -c 400 | sed 's/^/    /'
  echo
fi

echo
echo "OK → chargement ${CHARGEMENT} s, génération de test ${GEN} s"
echo "   À reporter au runbook §6 (mitigation M3 : « pod froid -> premier token »)."
echo
echo "Il reste DEUX choses, dans cet ordre :"
echo "  1. le tunnel privé (Tailscale/WireGuard) — l'inférence ne doit JAMAIS être"
echo "     joignable autrement. Ne pas exposer le port ${PORT} par le proxy RunPod."
echo "  2. depuis le poste d'exploitation, une fois l'adresse du tunnel connue :"
echo "        kubectl -n opencacao scale deploy sentinelle --replicas=0   # sinon elle"
echo "            replierait pendant le chargement du modèle"
echo "        deploy/scripts/profil.sh runpod http://<adresse_tunnel>:${PORT}"
echo "        kubectl -n opencacao scale deploy sentinelle --replicas=1   # réarmer"
