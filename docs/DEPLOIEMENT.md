# Déploiement d'une nouvelle image API — OpenCacao-8B

Procédure pour livrer une nouvelle version de l'**API** (et de la **console de
curation**, servie par la même image) sur le cluster K3s (Hetzner). Le modèle
GGUF se déploie séparément (`docs/REENTRAINEMENT.md`).

> Version livrée ici : image API **0.1.12** (`__version__` applicatif **0.2.1**) —
> pipeline depuis la console, pré-chauffage du cache FAQ, anti-brute-force du login,
> cache testé avant le rate-limit, `model_version` dans la clé de cache, `max_tokens`
> configurable.
>
> Note : `model_version` fait désormais partie de la clé de cache. Au déploiement,
> le cache accumulé devient orphelin (expire seul) ; le pré-chauffage régénère les
> FAQ en ~8 min. Sans impact fonctionnel.

## 1. Construire et pousser l'image API

```bash
TAG=0.1.12
docker build -t docker.io/thomcoul/opencacao-api:$TAG ./api
docker push docker.io/thomcoul/opencacao-api:$TAG
```

Le tag est déjà fixé dans `deploy/k8s/kustomization.yaml` (`opencacao-api` →
`0.1.11`). Pour une version suivante, bumper ce tag **et** `api/app/__init__.py`.

## 2. Appliquer le cluster

```bash
export KUBECONFIG=...
kubectl apply -k deploy/k8s/
```

Cela met à jour les déploiements `api` et `curation` (même image) et crée le
**RBAC de la console** (`curation-rbac.yaml` : ServiceAccount + Role `get/patch`
sur le seul Deployment `api`, nécessaire au bouton « Reconstruire le RAG »).

```bash
kubectl -n opencacao rollout status deploy/api
kubectl -n opencacao rollout status deploy/curation
```

## 3. Aucune nouvelle variable d'environnement requise

Les valeurs par défaut conviennent en prod :

| Variable | Défaut | Effet |
| --- | --- | --- |
| `PREWARM_ENABLED` | `true` | Pré-chauffage du cache FAQ au démarrage (tâche de fond). |
| `EMBEDDINGS_URL` | `http://embeddings:8001` | Service d'embeddings (reindex RAG console). |
| `API_DEPLOYMENT` | `api` | Déploiement redémarré après reindex. |
| `CURATION_LOGIN_MAX_ECHECS` | `5` | Seuil de blocage du login (par IP). |
| `CURATION_LOGIN_FENETRE_S` | `300` | Fenêtre/blocage anti-brute-force (s). |
| `INFERENCE_MAX_TOKENS` | `512` | Plafond de génération (abaisser réduit la latence). |

Durcissement recommandé : décommenter `whitelist-source-range` dans
`deploy/k8s/curation-ingress.yaml` avec les CIDR de l'équipe.

## 4. Vérifications post-déploiement

```bash
# Santé API
curl -s https://opencacao.openlabconsulting.com/v1/health        # {"status":"ok"}
# Version exposée
curl -s https://opencacao.openlabconsulting.com/v1/version        # api_version 0.2.1
# Console : page de connexion (200) et anti-brute-force
curl -s -o /dev/null -w "%{http_code}\n" https://curation.opencacao.openlabconsulting.com/
```

Le **pré-chauffage** tourne en fond après le démarrage (~8 min) : les questions
FAQ basculent à ~0,2 s au fil de l'eau. Vérifier dans les logs de l'API :
`kubectl -n opencacao logs deploy/api | grep prewarm`.

> Note : le pré-chauffage est idempotent — après un simple redémarrage d'API
> (cache Redis conservé), il ne régénère rien. Il ne recalcule qu'après une purge
> de cache (redéploiement de modèle).
