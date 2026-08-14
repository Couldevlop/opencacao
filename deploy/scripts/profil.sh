#!/usr/bin/env bash
# Bascule du profil matériel — GPU <-> CPU, un seul verbe, chronométré.
#
# Pourquoi ce script existe. La spec V3 §4.5 promet un retour au CPU « en moins de deux
# minutes », et §9.6 exige que la bascule soit « exécutée au moins deux fois,
# chronométrée, documentée ». Tenu à la main, c'est une suite de cinq commandes tapées
# sous pression devant une salle. Tenu ici, c'est un mot.
#
# Usage :
#   KUBECONFIG=kubeconfig-hetzner.yaml deploy/scripts/profil.sh cpu
#   KUBECONFIG=kubeconfig-hetzner.yaml deploy/scripts/profil.sh gpu
#   KUBECONFIG=kubeconfig-hetzner.yaml deploy/scripts/profil.sh runpod http://100.x.y.z:8000
#   deploy/scripts/profil.sh etat        # ne change rien, dit où on en est
#
# TROIS profils, et `gpu` n'est PAS `runpod` :
#
#   cpu     llama.cpp dans le cluster, le GGUF sur le nœud. Le défaut, et le repli.
#   gpu     vLLM DANS le cluster, sur un nœud porteur d'une carte. Bascule par
#           `kubectl scale` : les deux déploiements partagent le Service.
#   runpod  vLLM HORS du cluster, sur un GPU loué (décision matérielle de la spec
#           §4.3, le Hetzner GEX44 étant indisponible). Il n'y a alors aucun pod à
#           mettre à l'échelle côté GPU : ce qui bascule est `INFERENCE_URL`. Le
#           déploiement CPU n'est éteint qu'APRÈS vérification du service public —
#           on ne coupe pas ce qui répond encore pour un point de terminaison qu'on
#           n'a pas vu fonctionner.
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
# Adresse du Service interne : ce à quoi `INFERENCE_URL` doit revenir hors RunPod.
URL_INTERNE="${URL_INTERNE:-http://inference:8000}"
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
  k patch configmap api-config --type merge \
    -p "{\"data\":{\"INFERENCE_URL\":\"${URL_INTERNE}\"}}"
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
  # `INFERENCE_URL` est remise à l'adresse interne : c'est ce qui ramène aussi depuis
  # le profil runpod, où elle pointait hors du cluster. L'oublier laisserait l'API
  # parler à un pod loué déjà détruit — panne muette et coûteuse à diagnostiquer.
  k patch configmap api-config --type merge \
    -p "{\"data\":{\"PROFIL_MATERIEL\":\"cpu\",\"INFERENCE_BACKEND\":\"llama-cpp\",\"VISION_ENABLED\":\"false\",\"INFERENCE_URL\":\"${URL_INTERNE}\"}}"
  k rollout restart "deploy/${DEPL_API:-api}"
}

vers_runpod() {
  local cible="$1"
  case "${cible}" in
    http://* | https://*) ;;
    *)
      echo "✗ URL invalide : « ${cible} ». Attendu http(s)://hôte:port" >&2
      exit 2
      ;;
  esac

  # D1 : l'inférence n'est JAMAIS exposée publiquement. Un point de terminaison proxy
  # RunPod l'est par défaut — la spec §4.5 impose un tunnel privé (Tailscale ou
  # WireGuard) précisément pour cela. On refuse par défaut, et l'échappatoire est
  # explicite : personne ne doit pouvoir exposer le modèle par distraction.
  case "${cible}" in
    *proxy.runpod.net* | *.ngrok.* )
      if [ "${AUTORISER_ENDPOINT_PUBLIC:-0}" != "1" ]; then
        echo "✗ « ${cible} » est un point de terminaison PUBLIC." >&2
        echo "  D1 interdit d'exposer l'inférence ; la spec §4.5 impose un tunnel privé." >&2
        echo "  Passer par l'adresse du tunnel, ou forcer avec AUTORISER_ENDPOINT_PUBLIC=1." >&2
        exit 3
      fi
      echo "  ⚠ Point de terminaison public FORCÉ — hors doctrine, à ne pas laisser ainsi."
      ;;
  esac

  echo "→ Bascule vers un GPU loué : ${cible}"
  # On interroge le point de terminaison AVANT de toucher à quoi que ce soit. Un 401
  # est une bonne nouvelle : il prouve que vLLM répond ET qu'il est protégé par
  # `--api-key`. C'est un 000 (injoignable) qui doit arrêter la bascule.
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 \
    ${INFERENCE_API_KEY:+-H "Authorization: Bearer ${INFERENCE_API_KEY}"} \
    "${cible}/v1/models" || echo 000)"
  echo "  ${cible}/v1/models -> HTTP ${code}"
  case "${code}" in
    200) ;;
    401 | 403) echo "  ℹ Protégé par jeton : le Secret opencacao-inference doit porter le même." ;;
    *)
      echo "✗ Point de terminaison injoignable (HTTP ${code}) — bascule annulée." >&2
      echo "  Le profil CPU n'a pas été touché, le service continue de répondre." >&2
      exit 4
      ;;
  esac

  k patch configmap api-config --type merge \
    -p "{\"data\":{\"PROFIL_MATERIEL\":\"gpu\",\"INFERENCE_BACKEND\":\"vllm\",\"INFERENCE_URL\":\"${cible}\"}}"
  k rollout restart "deploy/${DEPL_API:-api}"
  verifier
  # Le CPU n'est éteint qu'ICI, une fois le service public vérifié. Si `verifier` a
  # signalé une anomalie, on garde les deux : de la RAM immobilisée coûte moins cher
  # qu'une salle devant une page blanche.
  echo "  Le déploiement CPU reste allumé jusqu'à votre confirmation :"
  echo "    kubectl -n ${NS} scale deploy ${DEPL_CPU} --replicas=0"
  echo "  Retour arrière, à tout instant : deploy/scripts/profil.sh cpu"
}

CIBLE="${1:-}"
case "${CIBLE}" in
  etat)
    etat
    exit 0
    ;;
  runpod)
    [ -n "${2:-}" ] || {
      echo "Usage : profil.sh runpod <URL du tunnel, ex. http://100.x.y.z:8000>" >&2
      exit 2
    }
    ;;
  gpu | cpu) ;;
  *)
    echo "Usage : profil.sh cpu|gpu|runpod <URL>|etat" >&2
    exit 2
    ;;
esac

DEPART="${SECONDS}"
echo "→ Profil de départ : $(profil_actuel)"
case "${CIBLE}" in
  gpu) vers_gpu ;;
  cpu) vers_cpu ;;
  runpod) vers_runpod "$2" ;;
esac
# `vers_runpod` a déjà vérifié : le refaire ne dirait rien de plus.
[ "${CIBLE}" = "runpod" ] || verifier
DUREE="$((SECONDS - DEPART))"

echo
echo "OK → profil ${CIBLE} en ${DUREE} s"
# Un chiffre non écrit est un chiffre perdu (runbook §6) : on le rend prêt à coller.
echo "   À reporter dans docs/demo/runbook.md §6 : bascule ${CIBLE} = ${DUREE} s"
etat
