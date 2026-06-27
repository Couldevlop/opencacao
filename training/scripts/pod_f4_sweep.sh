#!/usr/bin/env bash
# F4 — Recette LoRA PILOTÉE PAR L'ÉVAL, sur un seul pod GPU (24 Go).
#
# Pourquoi : jusqu'ici un seul jeu d'hyperparamètres (r=16, 1 epoch, lr=2e-4) était
# entraîné « à l'aveugle ». F4 balaie une petite grille — epochs × rang LoRA × lr ×
# longueur de séquence — entraîne un adaptateur par combinaison, ÉVALUE chacun avec
# le jeu d'éval étendu (garde-fous + qualité + juge GLM-5.2 + latence), puis ne
# retient que le MEILLEUR point de contrôle. On ne fusionne/exporte en GGUF que le
# vainqueur (souverain, économe en disque).
#
# Chaîne (tout sur le pod, séquentiel pour tenir sur 1 GPU) :
#   1) assemble le corpus  2) entraîne un adaptateur LoRA par combinaison (léger,
#   ~90 Mo chacun)  3) sert la base BF16 + tous les adaptateurs via vLLM (LoRA, sans
#   fusion)  4) évalue chaque combinaison  5) arrête vLLM  6) sélectionne le meilleur
#   d'après l'éval  7) fusionne UNIQUEMENT le vainqueur + export GGUF Q4_K_M.
#
# Portail non négociable (CLAUDE §13) : une combinaison n'est éligible que si
# garde-fous = 100 % ET 0 fuite de dosage. La qualité (juge en priorité) ne
# départage que des combinaisons déjà sûres ; la latence p95 départage à égalité.
#
# GPU : 24 Go suffisent (base BF16 ~16 Go + adaptateurs LoRA). Volume >= 80 Go.
#
# Pré-requis pod : template Python+PyTorch+CUDA>=12.1, dépôt cloné, corpus présent
#   (corpus/corpus_cacao_rag.jsonl via pod_generate.sh OU corpus distillé F2).
#   - HF_TOKEN : requis (modèle de base Ministral, gated).
#   - ZAI_API_KEY : recommandé (juge GLM-5.2 pour un classement qualité fiable).
#     Sans elle, le classement retombe sur le taux de qualité déterministe.
#
# Grille (surchargeable par variables d'env, défaut = 4 combinaisons) :
#   SWEEP_EPOCHS="1 2"  SWEEP_RANGS="16 32"  SWEEP_LRS="2e-4"  SWEEP_SEQ="1024"
#   # Grille complète de la roadmap :
#   SWEEP_EPOCHS="1 2 3" SWEEP_RANGS="16 32 64" SWEEP_LRS="2e-4 1e-4" SWEEP_SEQ="1024 1536"
#
# Usage :
#   export HF_TOKEN=hf_xxx ; export ZAI_API_KEY=...        # juge (recommandé)
#   bash training/scripts/pod_f4_sweep.sh
set -euo pipefail

SWEEP_EPOCHS="${SWEEP_EPOCHS:-1 2}"
SWEEP_RANGS="${SWEEP_RANGS:-16 32}"
SWEEP_LRS="${SWEEP_LRS:-2e-4}"
SWEEP_SEQ="${SWEEP_SEQ:-1024}"
PORT="${PORT:-8000}"
GPU_UTIL="${GPU_UTIL:-0.90}"
BASE_MODEL="mistralai/Ministral-3-8B-Instruct-2512-BF16"
SWEEP_DIR="models/sweep"
MERGED_DIR="models/opencacao-8b"
CORPUS_ENTRAINEMENT="corpus/corpus_entrainement.jsonl"

: "${HF_TOKEN:?Exporte HF_TOKEN (modèle de base Ministral, gated)}"
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"
CACHE_ROOT="${CACHE_ROOT:-/workspace}"; [ -d "$CACHE_ROOT" ] || CACHE_ROOT="$HOME"
export HF_HOME="$CACHE_ROOT/.hf" TMPDIR="$CACHE_ROOT/tmp"
mkdir -p "$HF_HOME" "$TMPDIR" "$SWEEP_DIR"

JUGE_OPT=""
if [[ -n "${ZAI_API_KEY:-}" ]]; then
  JUGE_OPT="--juge"
  echo "==> Juge GLM-5.2 actif (classement qualité fiable)."
else
  echo "==> ZAI_API_KEY absente : classement sur le taux de qualité déterministe."
fi

echo "==================================================================="
echo " F4 sweep — epochs={${SWEEP_EPOCHS}} rangs={${SWEEP_RANGS}} lr={${SWEEP_LRS}} seq={${SWEEP_SEQ}}"
echo "==================================================================="

echo "==> 1/7  Dépendances d'entraînement (PyTorch du pod conservé)"
pip install --upgrade pip >/dev/null
grep -v '^torch' training/requirements.txt > "${TMPDIR}/req-train.txt"
pip install -r "${TMPDIR}/req-train.txt" >/dev/null
pip install -U "git+https://github.com/huggingface/transformers" >/dev/null
pip install -U "numpy>=2" >/dev/null

echo "==> 2/7  Assemblage du corpus d'entraînement"
CORPUS=()
for f in corpus/corpus_cacao_teacher.jsonl corpus/corpus_cacao_rag.jsonl \
         corpus/corpus_cacao_demarrage.jsonl corpus/corpus_refus.jsonl \
         corpus/corpus_cure.jsonl; do
  [[ -f "$f" ]] && CORPUS+=("$f")
done
if [[ ${#CORPUS[@]} -eq 0 ]]; then
  echo "ERREUR : aucun corpus trouvé (génère corpus/corpus_cacao_rag.jsonl)." >&2
  exit 1
fi
echo "    sources : ${CORPUS[*]}"
python training/scripts/assemble_corpus.py --sources "${CORPUS[@]}" --out "${CORPUS_ENTRAINEMENT}"

# Grille calculée par sweep_lora.py (source de vérité unique).
mapfile -t GRILLE < <(python training/scripts/sweep_lora.py grille \
  --epochs ${SWEEP_EPOCHS} --rangs ${SWEEP_RANGS} \
  --learning-rates ${SWEEP_LRS} --max-seq-lens ${SWEEP_SEQ})
echo "==> ${#GRILLE[@]} combinaison(s) à balayer."

echo "==> 3/7  Entraînement d'un adaptateur LoRA par combinaison"
MAX_RANK=0
LORA_MODULES=()
for ligne in "${GRILLE[@]}"; do
  read -r ID EPOCHS RANG ALPHA LR SEQ <<< "${ligne}"
  echo "    [${ID}] epochs=${EPOCHS} r=${RANG} alpha=${ALPHA} lr=${LR} seq=${SEQ}"
  python training/scripts/train_lora.py \
    --corpus "${CORPUS_ENTRAINEMENT}" --output "${SWEEP_DIR}/${ID}" \
    --epochs "${EPOCHS}" --lora-r "${RANG}" --lora-alpha "${ALPHA}" \
    --learning-rate "${LR}" --max-seq-len "${SEQ}"
  LORA_MODULES+=("${ID}=${SWEEP_DIR}/${ID}")
  (( RANG > MAX_RANK )) && MAX_RANK="${RANG}"
done

echo "==> 4/7  Service vLLM : base BF16 + ${#LORA_MODULES[@]} adaptateurs (LoRA, sans fusion)"
pip install --no-cache-dir vllm >/dev/null
CLE="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
python -m vllm.entrypoints.openai.api_server \
  --model "${BASE_MODEL}" --api-key "${CLE}" \
  --enable-lora --max-lora-rank "${MAX_RANK}" \
  --max-loras 1 --max-cpu-loras "${#LORA_MODULES[@]}" \
  --lora-modules "${LORA_MODULES[@]}" \
  --port "${PORT}" --max-model-len 4096 --gpu-memory-utilization "${GPU_UTIL}" \
  > "${TMPDIR}/vllm.log" 2>&1 &
VLLM_PID=$!
trap 'kill "${VLLM_PID}" 2>/dev/null || true' EXIT
for i in $(seq 1 360); do
  if curl -fsS -H "Authorization: Bearer ${CLE}" "http://localhost:${PORT}/v1/models" >/dev/null 2>&1; then
    echo "    service prêt après ~$((i * 5))s"; break
  fi
  if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
    echo "ERREUR : vLLM s'est arrêté. Voir ${TMPDIR}/vllm.log :"; tail -30 "${TMPDIR}/vllm.log"; exit 1
  fi
  sleep 5
done

echo "==> 5/7  Évaluation de chaque combinaison (jeu d'éval étendu)"
for ligne in "${GRILLE[@]}"; do
  read -r ID _ <<< "${ligne}"
  echo "    éval [${ID}]"
  # On n'impose pas les seuils ici (||true) : la sélection applique le portail
  # garde-fous globalement ; on veut le rapport de TOUTES les combinaisons.
  ZAI_API_KEY="${ZAI_API_KEY:-}" python training/scripts/evaluate.py \
    --endpoint "http://localhost:${PORT}" --model "${ID}" ${JUGE_OPT} \
    --rapport "${SWEEP_DIR}/${ID}.json" || true
done

echo "==> 6/7  Arrêt de vLLM puis sélection du meilleur point de contrôle"
kill "${VLLM_PID}" 2>/dev/null || true
trap - EXIT
sleep 5

set +e
MEILLEUR="$(python training/scripts/sweep_lora.py selectionner \
  --rapports-dir "${SWEEP_DIR}" --sortie "${SWEEP_DIR}/rapport_sweep.json" | tail -1)"
SEL_RC=$?
set -e
if [[ ${SEL_RC} -ne 0 || -z "${MEILLEUR}" ]]; then
  echo "ÉCHEC : aucune combinaison ne franchit le portail garde-fous (100 % ET 0 "
  echo "fuite de dosage). Rien à déployer. Voir ${SWEEP_DIR}/rapport_sweep.json."
  exit 1
fi
echo "    vainqueur : ${MEILLEUR}"

echo "==> 7/7  Fusion du vainqueur + export GGUF Q4_K_M"
python training/scripts/merge_and_export.py \
  --base "${BASE_MODEL}" --adapter "${SWEEP_DIR}/${MEILLEUR}" --output "${MERGED_DIR}"
bash training/scripts/pod_gguf.sh "${MERGED_DIR}"

echo ""
echo "================================ TERMINÉ ============================"
echo " Meilleure recette : ${MEILLEUR}"
echo " Rapport comparatif : ${SWEEP_DIR}/rapport_sweep.json"
echo " GGUF prêt : models/opencacao-8b-Q4_K_M.gguf (~5 Go)"
echo " Suite (cf. docs/F4_sweep_runpod.md) : re-vérifier l'éval du vainqueur, puis"
echo " rapatrier + déployer sur le CX53 si garde-fous=100 % ET qualité>=baseline."
echo "===================================================================="
