#!/usr/bin/env bash
# Bascule du profil matériel — GPU <-> CPU, un seul verbe, chronométré.
#
# Pourquoi ce script existe. La spec V3 §4.5 promet un retour au CPU « en moins de deux
# minutes », et §9.6 exige que la bascule soit « exécutée au moins deux fois,
# chronométrée, documentée ». Tenu à la main, c'est une suite de cinq commandes tapées
# sous pression devant une salle. Tenu ici, c'est un mot.
#
# Usage :
#   KUBECONFIG=kubeconfig-hetzner.yaml deploy/scripts/profil.sh gpu
#   KUBECONFIG=kubeconfig-hetzner.yaml deploy/scripts/profil.sh cpu
#   deploy/scripts/profil.sh etat        # ne change rien, dit où on en est
#
# Idempotent : rejouer `profil.sh cpu` alors qu'on est déjà en CPU ne casse rien.
#
# CE QU'IL NE FAIT PAS, ET C'EST VOULU :
#   - il ne déploie aucune image applicative (c'est `roll-image.sh`) ;
#   - il n'allume PAS la vision en passant sur GPU. `VISION_ENABLED` est un drapeau de
#     fonctionnalité, pas une capacité matérielle : l'allumer doit rester une décision
#     prise en connaissance du budget de latence (runbook §3). En revanche il l'ÉTEINT
#     en repassant sur CPU, parce que laisser un drapeau qui promet une capacité absente
#     est la seule des deux erreurs qui se voie en scène.
set -euo pipefail

NS="${NS:-opencacao}"
DEPL_CPU="${DEPL_CPU:-inference}"
DEPL_GPU="${DEPL_GPU:-inference-gpu}"
RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MANIFESTE_GPU="${RACINE}/deploy/k8s/inference-gpu.yaml"
URL_PUBLIQUE="${URL_PUBLIQUE:-https://opencacao.openlabconsulting.com}"
# Le premier chargement d'un modèle est long (compilation des noyaux vLLM, lecture des
# poids). Le RETOUR, lui, doit tenir la promesse des deux minutes.
ATTENTE_GPU="${ATTENTE_GPU:-900s}"
ATTENTE_CPU="${ATTENTE_CPU:-300s}"

k() { kubectl -n "${NS}" "$@"; }

repliques() {
  # Répliques demandées d'un déploiement, 0 s'il n'existe pas encore.
  k get deploy "$1" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 0
}

profil_actuel() {
  k get configmap api-config -o jsonpath='{.data.PROFIL_MATERIEL}' 2>/dev/null || echo "?"
}

etat() {
  echo "→ Namespace ${NS}"
  echo "  PROFIL_MATERIEL déclaré : $(profil_actuel)"
  echo "  INFERENCE_BACKEND       : $(k get configmap api-config -o jsonpath='{.data.INFERENCE_BACKEND}' 2>/dev/null || echo '?')"
  echo "  VISION_ENABLED          : $(k get configmap api-config -o jsonpath='{.data.VISION_ENABLED}' 2>/dev/null || echo '?')"
  echo "  Répliques ${DEPL_CPU} (CPU) : $(repliques "${DEPL_CPU}")"
  echo "  Répliques ${DEPL_GPU} (GPU) : $(repliques "${DEPL_GPU}")"
  echo "  Pods servant le Service « inference » :"
  k get pods -l role=inference -L profil --no-headers 2>/dev/null | sed 's/^/    /' || true
  # Un Service sans endpoint est la panne la plus silencieuse du lot : l'API répond
  # 503 et rien dans les journaux du cluster ne dit pourquoi.
  local points
  points="$(k get endpoints inference -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  if [ -z "${points}" ]; then
    echo "  ⚠ AUCUN endpoint sur le Service « inference » — l'API répondra 503."
  else
    echo "  Endpoints : ${points}"
  fi
}

verifier() {
  # On ne déclare pas une bascule réussie sur la foi d'un rollout : ce qui compte est
  # que le service public réponde.
  echo "→ Vérification"
  k rollout status "deploy/${DEPL_API:-api}" --timeout=300s
  local pret
  pret="$(curl -fsS --max-time 20 "${URL_PUBLIQUE}/v1/ready" 2>/dev/null || echo 'INJOIGNABLE')"
  echo "  /v1/ready   : ${pret}"
  echo "  /v1/version : $(curl -fsS --max-time 20 "${URL_PUBLIQUE}/v1/version" 2>/dev/null || echo 'INJOIGNABLE')"
  case "${pret}" in
    *INJOIGNABLE*) echo "  ⚠ Le service public ne répond pas — NE PAS entrer en scène là-dessus." ;;
  esac
}

vers_gpu() {
  echo "→ Bascule vers le profil GPU"
  # Créé seulement s'il manque : un `apply` inconditionnel remettrait les répliques à 0
  # (valeur du manifeste) juste avant qu'on les monte à 1, ce qui tuerait le pod en
  # cours de chargement à chaque rejeu du script.
  if ! k get deploy "${DEPL_GPU}" >/dev/null 2>&1; then
    echo "  Création du déploiement ${DEPL_GPU} (absent)"
    k apply -f "${MANIFESTE_GPU}"
  fi
  k scale deploy "${DEPL_GPU}" --replicas=1
  echo "  Attente du chargement du modèle (jusqu'à ${ATTENTE_GPU})…"
  # Le GPU monte AVANT que le CPU descende : pendant le recouvrement, le Service a des
  # endpoints des deux côtés et sert indifféremment — les deux exposent la même API et
  # le même modèle. Descendre d'abord ouvrirait un trou de plusieurs minutes.
  k rollout status "deploy/${DEPL_GPU}" --timeout="${ATTENTE_GPU}"
  k scale deploy "${DEPL_CPU}" --replicas=0
  k patch configmap api-config --type merge \
    -p '{"data":{"PROFIL_MATERIEL":"gpu","INFERENCE_BACKEND":"vllm"}}'
  k rollout restart "deploy/${DEPL_API:-api}"
  echo "  ℹ La vision reste éteinte. Pour l'allumer, en connaissance du budget de"
  echo "    latence (runbook §3) : k patch configmap api-config --type merge \\"
  echo "      -p '{\"data\":{\"VISION_ENABLED\":\"true\"}}' puis rollout restart deploy/api"
}

vers_cpu() {
  echo "→ Retour au profil CPU"
  # Le CPU remonte AVANT que le GPU descende, pour la même raison qu'à l'aller. C'est
  # le chemin qu'on empruntera si le GPU lâche en scène : il ne doit pas commencer par
  # éteindre la seule chose qui répond encore.
  k scale deploy "${DEPL_CPU}" --replicas=1
  echo "  Attente du chargement du GGUF (jusqu'à ${ATTENTE_CPU})…"
  k rollout status "deploy/${DEPL_CPU}" --timeout="${ATTENTE_CPU}"
  if k get deploy "${DEPL_GPU}" >/dev/null 2>&1; then
    k scale deploy "${DEPL_GPU}" --replicas=0
  fi
  k patch configmap api-config --type merge \
    -p '{"data":{"PROFIL_MATERIEL":"cpu","INFERENCE_BACKEND":"llama-cpp","VISION_ENABLED":"false"}}'
  k rollout restart "deploy/${DEPL_API:-api}"
}

CIBLE="${1:-}"
case "${CIBLE}" in
  etat)
    etat
    exit 0
    ;;
  gpu | cpu) ;;
  *)
    echo "Usage : profil.sh gpu|cpu|etat" >&2
    exit 2
    ;;
esac

DEPART="${SECONDS}"
echo "→ Profil de départ : $(profil_actuel)"
if [ "${CIBLE}" = "gpu" ]; then vers_gpu; else vers_cpu; fi
verifier
DUREE="$((SECONDS - DEPART))"

echo
echo "OK → profil ${CIBLE} en ${DUREE} s"
# Un chiffre non écrit est un chiffre perdu (runbook §6) : on le rend prêt à coller.
echo "   À reporter dans docs/demo/runbook.md §6 : bascule ${CIBLE} = ${DUREE} s"
etat
