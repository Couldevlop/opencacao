# Déploiement OpenCacao sur K3s (Hetzner, CPU/GGUF)

Déploie l'application complète sur ton cluster K3s : interface + API publiques
(via Ingress TLS), inférence (llama.cpp + GGUF, CPU) et Redis **internes**.

```
Internet ──HTTPS──▶ Ingress Traefik ┬─ /v1 ─▶ api (FastAPI, garde-fous)
                    (cert-manager)   └─ /   ─▶ web (interface nginx)
                                                  api ──▶ inference (llama.cpp + GGUF)   [interne]
                                                  api ──▶ redis (cache/rate-limit)        [interne]
```

> **Rappel** : Hetzner = CPU. L'inférence d'un 8B en GGUF est **lente**
> (~3-8 mots/s, ~1 min/réponse). Suffisant pour une démo, pas pour de la charge.

## 0. Prérequis : produire le modèle GGUF (à dé-risquer EN PREMIER)
Sur le pod GPU (modèle fusionné présent) :
```sh
bash training/scripts/pod_gguf.sh          # -> models/opencacao-8b-Q4_K_M.gguf (~5 Go)
runpodctl send models/opencacao-8b-Q4_K_M.gguf
```
Récupère-le sur ton PC (`runpodctl receive <code>`). **Teste-le** rapidement avant
de déployer (ex. `docker run ... ghcr.io/ggml-org/llama.cpp:server -m … && curl /v1/models`) :
si la conversion ou le service mistral3 échoue, le chemin CPU est bloqué — bascule
alors sur l'option « inférence GPU » (hybride).

## 1. Construire et pousser les images (Docker Hub)
Depuis la racine du dépôt :
```sh
export U=DOCKERHUB_USER            # ton compte Docker Hub
docker build -t docker.io/$U/opencacao-api:0.1.0 ./api
docker build -f web/Dockerfile -t docker.io/$U/opencacao-web:0.1.0 .
docker push docker.io/$U/opencacao-api:0.1.0
docker push docker.io/$U/opencacao-web:0.1.0
```

## 2. Personnaliser les manifests
- `kustomization.yaml` : remplacer `DOCKERHUB_USER` (les 2 images).
- `api.yaml` et `ingress.yaml` : remplacer `opencacao.example.ci` par ton domaine.
- `ingress.yaml` : ton `ClusterIssuer` cert-manager (sinon installer cert-manager).
- `inference.yaml` : `storageClassName` (ici `hcloud-volumes`, CSI Hetzner).

## 3. Charger le GGUF sur le volume
Crée d'abord le namespace + la PVC, puis copie le fichier via un pod temporaire :
```sh
kubectl apply -f namespace.yaml
kubectl apply -f inference.yaml          # crée la PVC modele-gguf (la pull d'image peut attendre)
kubectl -n opencacao run gguf-loader --image=busybox --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"l","image":"busybox","command":["sleep","3600"],
  "volumeMounts":[{"name":"m","mountPath":"/models"}]}],"volumes":[{"name":"m",
  "persistentVolumeClaim":{"claimName":"modele-gguf"}}]}}'
kubectl -n opencacao cp opencacao-8b-Q4_K_M.gguf gguf-loader:/models/opencacao-8b-Q4_K_M.gguf
kubectl -n opencacao delete pod gguf-loader
```
*(Alternative : initContainer qui télécharge le GGUF depuis l'Object Storage S3 Hetzner.)*

## 4. Déployer
```sh
kubectl apply -k .
kubectl -n opencacao get pods -w
```
Attends que `inference` soit `Ready` (1er chargement du GGUF = quelques minutes).

## 5. DNS + TLS
- Pointe un enregistrement **A** de ton domaine vers l'IP d'entrée du cluster
  (LoadBalancer Hetzner, ou IP du nœud Traefik).
- cert-manager émet le certificat Let's Encrypt automatiquement (annotation Ingress).

## 6. Accès
Ouvre `https://<ton-domaine>` → l'interface OpenCacao s'affiche et **répond avec
le modèle** (lentement, CPU). Les garde-fous (refus des dosages, ANADER) et le
disclaimer s'appliquent côté API.

## Sécurité (rappels)
- `inference` et `redis` en ClusterIP + NetworkPolicies (trafic API uniquement).
- `ENABLE_DOCS=false`, `TRUST_FORWARDED_FOR=true` (derrière Traefik), TLS partout.
- Pas de secret côté client ; pas de modèle exposé directement.
