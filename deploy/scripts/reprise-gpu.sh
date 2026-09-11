#!/usr/bin/env bash
# Reprise du GPU en moins de cinq minutes — côté cluster.
#
# CE QUI REND CETTE PROMESSE TENABLE. Le pod loué ne détient rien d'irremplaçable :
# tout ce qui compte vit sur le VOLUME RÉSEAU RunPod (`naq4wvxooz`, monté sur
# /workspace), qui survit à la destruction du pod. Il porte le GGUF de production,
# llama.cpp déjà compilé, le script de démarrage automatique — et l'état Tailscale,
# donc la MÊME adresse de tunnel d'un pod à l'autre.
#
# On peut donc DÉTRUIRE le pod entre deux usages, pas seulement l'arrêter : le coût du
# pod tombe à zéro et rien n'est perdu. Seul le volume reste facturé.
#
# ⚠ Le volume `iyqkq9jicv` (jusqu'au 20/08) a disparu avec son pod : un pod détruit ne
# coûte rien, un volume détruit coûte une reconstruction complète (~40 min, dont la
# compilation de llama.cpp et 4,9 Go à repousser depuis le nœud). Garder le volume est
# le geste qui tient la promesse ci-dessus.
#
# ─────────────────────────────────────────────────────────────────────────────
# CE QUI SE FAIT AILLEURS, ET QUI N'EST PAS DANS CE SCRIPT
#
#   1. Créer le pod dans la console RunPod, avec DEUX réglages non négociables :
#        · Network Volume : `naq4wvxooz` — sinon le pod démarre vide, RunPod en crée un
#          neuf EN SILENCE (vécu le 11/09), et il faut tout reconstruire. Le volume est
#          lié au centre de données EU-RO-1 : le pod DOIT y être créé, sans quoi il ne
#          peut pas s'y attacher.
#        · Container Start Command :
#            bash -c 'if [ -f /workspace/pre_start.sh ]; then cp -f /workspace/pre_start.sh /pre_start.sh; fi; exec /start.sh'
#          C'est elle qui fait renaître le point d'accroche effacé à chaque arrêt, et
#          donc qui relance Tailscale et llama-server tout seuls. Le fichier de
#          référence est versionné : `deploy/scripts/pod/pre_start.sh`.
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
# shebang le dit, et il faut l'invoquer par `bash`, jamais par `sh` : la substitution de
# processus de l'étape 2 en dépend.
#
# ⚠ Transféré depuis Windows, purger les retours chariot, sans quoi bash lit l'option
# « pipefail » et refuse la première ligne :   tr -d '\r' < script | ssh … 'cat > …'
set -euo pipefail

NS="${NS:-opencacao}"
CONFIGMAP="${CONFIGMAP:-api-config}"
DEPL_API="${DEPL_API:-api}"
SECRET_JETON="${SECRET_JETON:-opencacao-inference}"
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

echo "→ 1/5  Le pod répond-il ?  ${CIBLE}"
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

echo "→ 2/5  Le pod accepte-t-il le jeton DU CLUSTER ?"
# LA LEÇON DE LA NUIT DU 11/09. L'étape 1 se contentait d'un 401 — « le serveur est
# protégé », ce qui est vrai et rassurant, mais ne dit RIEN de la validité du jeton que
# l'API présentera. Le jeton du pod différait de quatre octets ; la bascule a été
# déclarée « OK en 14 s » et la production a rendu 503 jusqu'à ce que la sentinelle
# replie d'elle-même. On essaie donc une vraie génération ICI, AVANT de toucher au
# ConfigMap : un échec à cette étape ne coûte rien, le CPU continue de servir.
JETON="$(k get secret "${SECRET_JETON}" -o jsonpath='{.data.INFERENCE_API_KEY}' 2>/dev/null | base64 -d || true)"
if [ -z "${JETON}" ]; then
  echo "✗ Secret « ${SECRET_JETON} » introuvable ou vide : impossible de prouver l'accès." >&2
  exit 6
fi
# Le jeton passe par un fichier de configuration éphémère, JAMAIS par argv : `ps` est
# lisible par tous sur le nœud. C'est le reste de sécurité relevé au 19/08 côté pod,
# qu'on ne reproduit pas côté cluster.
ESSAI="$(curl -s --max-time 60 \
  --config <(printf 'header = "Authorization: Bearer %s"\n' "${JETON}") \
  -H 'Content-Type: application/json' \
  -d '{"model":"opencacao-8b","max_tokens":8,"messages":[{"role":"user","content":"Bonjour"}]}' \
  "${CIBLE}/v1/chat/completions" 2>/dev/null || true)"
case "${ESSAI}" in
  *'"choices"'*)
    echo "        génération d'essai acceptée"
    ;;
  *)
    echo "✗ Le pod REFUSE le jeton du cluster — l'API prendrait un 401 et rendrait 503." >&2
    echo "  Le cluster n'est PAS touché : le service continue sur CPU." >&2
    echo "  Empreinte du jeton attendu : $(printf '%s' "${JETON}" | sha256sum | cut -c1-12)" >&2
    echo "  Comparer sur le pod :  sha256sum /workspace/.opencacao_api_key | cut -c1-12" >&2
    echo "  Et  wc -c  doit rendre 64 : un « echo » au lieu d'un « printf » ajoute un octet." >&2
    exit 7
    ;;
esac

echo "→ 2 bis/5  Chaque conversation a-t-elle assez de contexte ?"
# llama.cpp DIVISE `-c` entre les emplacements parallèles. `-c 8192 -np 4` n'offre que
# 2048 tokens par conversation : trois tours avec un extrait RAG dépassent, le serveur
# rend 400 « exceeds the available context size » et l'interface annonce un service
# indisponible. Vécu le 11/09, et INVISIBLE pour une génération d'essai courte — d'où
# cette lecture directe de ce que le serveur offre vraiment, plutôt qu'un prompt témoin
# qu'il faudrait calibrer.
CTX_MIN="${CTX_MIN:-8192}"
PROPS="$(curl -s --max-time 15 \
  --config <(printf 'header = "Authorization: Bearer %s"\n' "${JETON}") \
  "${CIBLE}/props" 2>/dev/null || true)"
# `/props` expose plusieurs `n_ctx` (le total du serveur et celui d'un emplacement).
# On retient le PLUS PETIT : c'est celui dont dispose réellement une conversation, et
# se tromper dans l'autre sens revaliderait précisément le défaut qu'on traque.
CTX="$(printf '%s' "${PROPS}" | tr ',' '\n' | sed -n 's/.*"n_ctx"[[:space:]]*:[[:space:]]*\([0-9]\{1,\}\).*/\1/p' | sort -n | head -1)"
if [ -z "${CTX}" ]; then
  echo "        ⚠ contexte par conversation inconnu (/props illisible) — on continue"
elif [ "${CTX}" -lt "${CTX_MIN}" ]; then
  echo "✗ ${CTX} tokens par conversation, il en faut ${CTX_MIN}." >&2
  echo "  Le cluster n'est PAS touché. Sur le pod, relancer avec un TOTAL suffisant :" >&2
  echo "      pkill -f llama-server" >&2
  echo "      CONTEXTE=32768 bash /workspace/pod_serve.sh   # 32768 / 4 slots = 8192" >&2
  exit 8
else
  echo "        ${CTX} tokens par conversation"
fi

echo "→ 3/5  Bascule de la configuration"
# Les DEUX clés. `INFERENCE_URL` fait basculer maintenant ; `INFERENCE_URL_GPU` est ce
# que la veille du matin relira demain — l'oublier ferait échouer la reprise automatique
# du lendemain, sans que rien ne le signale aujourd'hui.
# Les fonctions délestées la nuit sont RESTAURÉES ici. Le réveil automatique le fait
# (`exploitation/fenetre.py`), la reprise manuelle ne le faisait pas : le 11/09, le
# service est revenu sur GPU avec « Ma parcelle » et « l'Atelier » encore annoncés
# « bientôt ». Deux chemins pour le même état doivent produire le même état.
# VISION_ENABLED reste À PART, et volontairement : le modèle de vision n'est pas sur
# tous les pods (il a disparu avec le volume du 20/08). Le rallumer d'office donnerait
# une fonction qui échoue à chaque photo. Il s'allume quand le service de vision répond.
k patch configmap "${CONFIGMAP}" --type merge -p "{\"data\":{
  \"PROFIL_MATERIEL\":\"gpu\",
  \"INFERENCE_URL\":\"${CIBLE}\",
  \"INFERENCE_URL_GPU\":\"${CIBLE}\",
  \"REPLI_CPU\":\"false\",
  \"RAPPORTS_ENABLED\":\"true\",
  \"PARCELLES_ENABLED\":\"true\"
}}" >/dev/null
echo "        profil gpu, tunnel mémorisé, atelier et parcelles restaurés"

echo "→ 4/5  Redémarrage de l'API"
k rollout restart "deploy/${DEPL_API}" >/dev/null
k rollout status "deploy/${DEPL_API}" --timeout="${ATTENTE_MAX_S}s"

echo "→ 5/5  Vérification du service, sur une GÉNÉRATION RÉELLE"
# On ne déclare pas une reprise réussie sur la foi d'un rollout, ni d'un `/v1/ready` :
# ce dernier n'interroge que la sonde de santé de llama.cpp, qui n'exige aucun jeton.
# Il a répondu `inference:true` pendant que la production rendait 503. Le seul contrôle
# qui prouve qu'un utilisateur est servi est une réponse complète obtenue par le vrai
# chemin — depuis un pod du cluster, avec l'en-tête Host qu'exige TrustedHostMiddleware
# (le nœud reçoit 403 de Cloudflare sur sa propre URL publique).
HOTE="$(echo "${URL_PUBLIQUE}" | sed 's|https\?://||')"
REPONSE=""
for _ in $(seq 1 12); do
  REPONSE="$(k exec "deploy/${DEPL_API}" -- python -c "
import httpx, sys
try:
    r = httpx.post('http://127.0.0.1:8080/v1/chat',
                   headers={'Host': '${HOTE}'},
                   json={'question': 'Quand tailler un cacaoyer ?'},
                   timeout=120)
    sys.stdout.write('%d %s' % (r.status_code, r.text[:120]))
except Exception as exc:
    sys.stdout.write('000 %s' % type(exc).__name__)
" 2>/dev/null || true)"
  case "${REPONSE}" in 200*) break ;; esac
  sleep 5
done
echo "        /v1/chat : ${REPONSE:-INJOIGNABLE}"

DUREE="$((SECONDS - DEPART))"
case "${REPONSE}" in
  200*)
    echo
    echo "OK → GPU repris en ${DUREE} s, prouvé par une génération complète."
    ;;
  *)
    echo
    echo "⚠ Configuration basculée mais AUCUNE génération n'a abouti (${DUREE} s)." >&2
    echo "  Retour immédiat au CPU : deploy/scripts/profil.sh cpu" >&2
    echo "  Journal du pod : tail -30 /workspace/llama-server.log" >&2
    exit 5
    ;;
esac
