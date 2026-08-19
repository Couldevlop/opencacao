#!/usr/bin/env bash
# Jour J — ouvrir la démonstration sur GPU, puis TOUT refermer proprement.
#
# Présentation publique du 19/08/2026, bascule prévue le 17/08. Ce script ne remplace
# pas `profil.sh` : il l'appelle. La différence est de niveau.
#
#   profil.sh   bascule le MATÉRIEL. Il ne connaît que le backend et le profil.
#   jour-j.sh   bascule l'ÉVÉNEMENT : le matériel, PLUS les fonctionnalités qu'on
#               ouvre au public, PLUS ce qu'il faut refermer après.
#
# Pourquoi les séparer. Un drapeau de fonctionnalité n'est pas une capacité matérielle,
# et les mélanger dans une seule commande, c'est se retrouver à ouvrir l'atelier au
# public parce qu'on voulait un GPU. On peut donc basculer le matériel sans rien ouvrir
# — c'est ce qu'on fait en répétition.
#
# Usage :
#   KUBECONFIG=kubeconfig-hetzner.yaml deploy/scripts/jour-j.sh ouvrir            # GPU du cluster
#   KUBECONFIG=kubeconfig-hetzner.yaml deploy/scripts/jour-j.sh ouvrir http://100.x.y.z:8000
#   KUBECONFIG=kubeconfig-hetzner.yaml deploy/scripts/jour-j.sh fermer
#   deploy/scripts/jour-j.sh etat
#
# `ouvrir` accepte l'URL d'un tunnel : on part alors sur un GPU loué (spec §4.5) au
# lieu d'une carte dans le cluster.
set -euo pipefail

NS="${NS:-opencacao}"
ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFIL="${ICI}/profil.sh"
RACINE="$(cd "${ICI}/../.." && pwd)"
URL_PUBLIQUE="${URL_PUBLIQUE:-https://opencacao.openlabconsulting.com}"

k() { kubectl -n "${NS}" "$@"; }

drapeaux() {
  # Un seul patch : deux patchs successifs, ce sont deux redémarrages et une fenêtre
  # pendant laquelle l'API annonce un état qu'elle n'a plus.
  k patch configmap api-config --type merge -p "$1"
  k rollout restart deploy/api
  k rollout status deploy/api --timeout=300s
}

capacites() {
  echo "→ Ce que l'API déclare ouvrir (c'est ce que l'interface proposera) :"
  curl -fsS --max-time 20 "${URL_PUBLIQUE}/v1/version" 2>/dev/null || echo "  INJOIGNABLE"
  echo
}

ouvrir() {
  local tunnel="${1:-}"

  echo "═══ 1/4  Bascule matérielle ═══"
  if [ -n "${tunnel}" ]; then
    "${PROFIL}" runpod "${tunnel}"
  else
    "${PROFIL}" gpu
  fi

  echo
  echo "═══ 2/4  Service de vision ═══"
  # Le VLM ne tient que sur GPU. On le déploie MAINTENANT, avant de lever son drapeau :
  # l'ordre inverse laisserait l'API annoncer une capacité dont le service n'existe pas.
  k apply -f "${RACINE}/deploy/k8s/vision.yaml"
  k apply -f "${RACINE}/deploy/k8s/networkpolicy.yaml"
  if ! k rollout status deploy/vision --timeout=900s; then
    echo "✗ La vision n'est pas montée. On N'OUVRE PAS le drapeau correspondant." >&2
    echo "  Le reste de la démonstration tient sans elle (scénario §3 bis)." >&2
    VISION="false"
  else
    VISION="true"
  fi

  echo
  echo "═══ 3/4  Ouverture des fonctionnalités ═══"
  # REPLI_CPU redescend ici : si la sentinelle avait ramené le service au CPU d'elle-même
  # (cf. deploy/k8s/sentinelle.yaml), l'interface affiche encore l'avis « service de
  # secours ». Rouvrir sans l'effacer laisserait un bandeau qui ment.
  drapeaux "{\"data\":{
    \"PARCELLES_ENABLED\":\"true\",
    \"RAPPORTS_ENABLED\":\"true\",
    \"REPLI_CPU\":\"false\",
    \"VISION_ENABLED\":\"${VISION}\"
  }}"

  echo
  echo "═══ 4/4  Vérification ═══"
  capacites
  echo "→ Il reste UNE chose à faire, et elle vient EN DERNIER :"
  echo "     python ${RACINE}/scripts/prewarm_cache.py"
  echo "   Le pré-chauffage est invalidé par tout déploiement postérieur (APP_VERSION"
  echo "   entre dans la clé de cache). Aucun roll-image.sh après ce point."
}

fermer() {
  echo "═══ 1/4  Fermeture des fonctionnalités ═══"
  # On referme AVANT d'éteindre le matériel. L'ordre inverse laisserait l'API annoncer
  # la vision et l'atelier alors que le GPU n'est déjà plus là : des requêtes parties
  # pour rien, et des erreurs à la place d'un refus propre.
  #
  # RAPPORTS_ENABLED redescend, et ce n'est pas de la prudence excessive : sur CPU une
  # étude coûte plusieurs minutes et l'inférence ne sert QU'UNE requête à la fois. Un
  # visiteur lançant un document bloquerait le chat pour tout le monde. La spec §4.1
  # prévoit la file nocturne par cron pour cet usage ; elle n'existe pas encore.
  #
  # PARCELLES_ENABLED reste levé : la cartographie ne mobilise aucun modèle.
  # PARCELLES_ENABLED est REMIS à true, pas seulement laissé : un repli automatique
  # l'aura peut-être baissé pendant l'événement, et fermer proprement veut dire revenir
  # à l'état de service normal, pas hériter d'un état d'incident. REPLI_CPU redescend
  # pour la même raison : la fin de l'événement est une décision, pas une panne.
  drapeaux '{"data":{
    "PARCELLES_ENABLED":"true",
    "RAPPORTS_ENABLED":"false",
    "REPLI_CPU":"false",
    "VISION_ENABLED":"false"
  }}'

  echo
  echo "═══ 2/4  Retour au CPU ═══"
  "${PROFIL}" cpu

  echo
  echo "═══ 3/4  Extinction du service de vision ═══"
  # Mis à zéro, jamais supprimé : le redéployer coûterait le rechargement des poids.
  if k get deploy vision >/dev/null 2>&1; then
    k scale deploy vision --replicas=0
    echo "  vision : 0 réplique"
  else
    echo "  vision : absent, rien à éteindre"
  fi

  echo
  echo "═══ 4/4  Vérification ═══"
  capacites
  cat <<'RAPPEL'
⚠ CE QUE CE SCRIPT NE PEUT PAS FAIRE, ET QUI COÛTE DE L'ARGENT

  Si la démonstration a tourné sur un GPU LOUÉ, le pod est facturé À L'HEURE et il
  tourne encore. Ce script n'a aucun accès au fournisseur : personne d'autre que vous
  ne l'arrêtera.

      -> Détruire l'instance dans la console RunPod. Maintenant, pas demain.

  Le repère de la spec §4.3 : ~30 h d'utilisation coûtent 15 à 25 €, un mois oublié
  en coûte 300 à 500.
RAPPEL
}

case "${1:-}" in
  ouvrir) ouvrir "${2:-}" ;;
  fermer) fermer ;;
  etat)
    "${PROFIL}" etat
    capacites
    ;;
  *)
    echo "Usage : jour-j.sh ouvrir [URL du tunnel] | fermer | etat" >&2
    exit 2
    ;;
esac
