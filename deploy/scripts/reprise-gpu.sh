#!/usr/bin/env bash
# Reprise du GPU en moins de cinq minutes — côté cluster.
#
# CE QUI REND CETTE PROMESSE TENABLE. Le pod loué ne détient rien d'irremplaçable :
# tout ce qui compte vit sur le VOLUME RÉSEAU RunPod (`iyqkq9jicv`, monté sur
# /workspace), qui survit à la destruction du pod. Il porte le GGUF de production, le
# modèle de vision, llama.cpp déjà compilé, le script de démarrage automatique — et
# l'état Tailscale, donc la MÊME adresse de tunnel d'un pod à l'autre.
#
# On peut donc DÉTRUIRE le pod entre deux usages, pas seulement l'arrêter : le coût du
# pod tombe à zéro et rien n'est perdu. Seul le volume reste facturé.
#
# ─────────────────────────────────────────────────────────────────────────────
# CE QUI SE FAIT AILLEURS, ET QUI N'EST PAS DANS CE SCRIPT
#
#   1. Créer le pod dans la console RunPod, avec DEUX réglages non négociables :
#        · Network Volume : `iyqkq9jicv` — sinon le pod démarre vide, et il faudra
#          repousser 12 Go. Le volume est lié au centre de données EU-RO-1 : le pod
#          DOIT y être créé, sans quoi il ne peut pas s'y attacher.
#        · Container Start Command :
#            bash -c 'if [ -f /workspace/pre_start.sh ]; then cp -f /workspace/pre_start.sh /pre_start.sh; fi; exec /start.sh'
#          C'est elle qui fait renaître le point d'accroche effacé à chaque arrêt, et
#          donc qui relance Tailscale et llama-server tout seuls.
#
#   2. Attendre ~1 min que le pod démarre. `/workspace/demarrage.log` raconte ce qui
#      s'est passé ; `/workspace/llama-server.log` le chargement du modèle.
#
# Ensuite seulement, ce script. Usage :
#   KUBECONFIG=kubeconfig-hetzner.yaml deploy/scripts/reprise-gpu.sh [URL du tunnel]
#
# Sans argument, il reprend l'adresse mémorisée (INFERENCE_URL_GPU) — ce qui est le cas
# nominal, l'identité Tailscale étant sur le volume.
# Le nœud a bien bash 5.2 ; c'est /bin/sh qui y est dash. Ce script exige bash — son
# shebang le dit, et il faut l'invoquer par `bash`, jamais par `sh`.
#
# ⚠ Transféré depuis Windows, purger les retours chariot : `bash` lirait l'option
# « pipefail » et refuserait la première ligne. `tr -d '' < script | bash`.
set -euo pipefail

NS="${NS:-opencacao}"
CONFIGMAP="${CONFIGMAP:-api-config}"
DEPL_API="${DEPL_API:-api}"
URL_PUBLIQUE="${URL_PUBLIQUE:-https://opencacao.openlabconsulting.com}"
ATTENTE_MAX_S="${ATTENTE_MAX_S:-180}"

k() { kubectl -n "${NS}" "$@"; }

DEPART="${SECONDS}"

CIBLE="${1:-$(k get configmap "${CONFIGMAP}" -o jsonpath='{.data.INFERENCE_URL_GPU}' 2>/dev/null || true)}"
if [ -z "${CIBLE}" ]; then
  echo "✗ Aucune adresse de tunnel : ni en argument, ni mémorisée dans INFERENCE_URL_GPU." >&2
  echo "  Sur le pod : tailscale ip -4     puis rejouer avec http://<adresse>:8000" >&2
  exit 2
fi

# D1 : l'inférence n'est jamais exposée publiquement. Le même refus que profil.sh et que
# le réveil automatique — un garde-fou que l'outil de reprise contournerait n'en est plus un.
case "${CIBLE}" in
  *proxy.runpod.net* | *.ngrok.*)
    echo "✗ « ${CIBLE} » est un point de terminaison PUBLIC. Passer par le tunnel privé." >&2
    exit 3
    ;;
esac

echo "→ 1/4  Le pod répond-il ?  ${CIBLE}"
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "${CIBLE}/v1/models" || echo 000)"
echo "        /v1/models -> HTTP ${CODE}"
case "${CODE}" in
  # 401 est une BONNE nouvelle : le serveur répond ET il est protégé par --api-key.
  200 | 401 | 403) ;;
  *)
    echo "✗ Injoignable (HTTP ${CODE}). Le cluster n'est pas touché, le service continue sur CPU." >&2
    echo "  Vérifier sur le pod : pgrep -a llama-server ; tailscale ip -4 ; tail /workspace/demarrage.log" >&2
    exit 4
    ;;
esac

echo "→ 2/4  Bascule de la configuration"
# Les DEUX clés. `INFERENCE_URL` fait basculer maintenant ; `INFERENCE_URL_GPU` est ce
# que la veille du matin relira demain — l'oublier ferait échouer la reprise automatique
# du lendemain, sans que rien ne le signale aujourd'hui.
k patch configmap "${CONFIGMAP}" --type merge -p "{\"data\":{
  \"PROFIL_MATERIEL\":\"gpu\",
  \"INFERENCE_URL\":\"${CIBLE}\",
  \"INFERENCE_URL_GPU\":\"${CIBLE}\",
  \"REPLI_CPU\":\"false\"
}}" >/dev/null
echo "        profil gpu, tunnel mémorisé"

echo "→ 3/4  Redémarrage de l'API"
k rollout restart "deploy/${DEPL_API}" >/dev/null
k rollout status "deploy/${DEPL_API}" --timeout="${ATTENTE_MAX_S}s"

echo "→ 4/4  Vérification du service"
# On ne déclare pas une reprise réussie sur la foi d'un rollout : ce qui compte est que
# le service réponde. On interroge donc l'API, mais PAS forcément par son adresse
# publique : exécuté sur le nœud — ce qui est le cas nominal, le port 6443 étant filtré
# depuis un poste de travail — Cloudflare répond 403 à l'origine qui s'appelle
# elle-même. On sonde alors DEPUIS le cluster, ce qui teste d'ailleurs le vrai chemin.
sonder() {
  local corps
  corps="$(curl -fsS --max-time 15 "${URL_PUBLIQUE}/v1/ready" 2>/dev/null || true)"
  case "${corps}" in *'"inference":true'*) echo "${corps}"; return 0 ;; esac
  # Repli interne : depuis un pod, avec l'en-tête Host qu'exige TrustedHostMiddleware.
  k exec "deploy/${DEPL_API}" -- python -c "
import httpx, sys
try:
    r = httpx.get('http://127.0.0.1:8080/v1/ready', headers={'Host': '$(echo "${URL_PUBLIQUE}" | sed 's|https\?://||')'}, timeout=10)
    sys.stdout.write(r.text)
except Exception:
    pass
" 2>/dev/null || true
}

PRET=""
for _ in $(seq 1 20); do
  PRET="$(sonder)"
  case "${PRET}" in *'"inference":true'*) break ;; esac
  sleep 5
done
echo "        /v1/ready : ${PRET:-INJOIGNABLE}"

DUREE="$((SECONDS - DEPART))"
case "${PRET}" in
  *'"inference":true'*)
    echo
    echo "OK → GPU repris en ${DUREE} s (côté cluster)."
    ;;
  *)
    echo
    echo "⚠ Configuration basculée mais le service ne confirme pas encore (${DUREE} s)." >&2
    echo "  Retour immédiat au CPU si besoin : deploy/scripts/profil.sh cpu" >&2
    exit 5
    ;;
esac
