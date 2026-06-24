# Déploiement continu d'OpenCacao (GitOps — ArgoCD)

Calque du dispositif éprouvé du site web : **push sur `main` → pipeline → image GHCR
→ ArgoCD déploie**. Aucun secret de registre (GHCR via `GITHUB_TOKEN`), aucun accès
SSH entrant au cluster.

```
push/merge main ─▶ .github/workflows/release.yml
                     ├─ gate   : ruff + pytest + corpus  (bloquant)
                     ├─ tag    : auto-bump vX.Y.Z + push du tag
                     └─ publish: build+push ghcr.io/couldevlop/opencacao-{api,web}:X.Y.Z
                                 (+ Trivy report-only + Cosign keyless)
                                          │
                       ArgoCD Image Updater (semver) ─▶ sync du namespace opencacao
```

## Flux

1. **`gate`** rejoue exactement `ci.yml` (lint, format, tests API ≥ 90 %, tests
   training, validation corpus). Rouge → pas de tag, pas d'image, pas de déploiement.
2. **`tag`** incrémente le patch depuis le dernier `vX.Y.Z` (ou version manuelle via
   `workflow_dispatch`) et pousse le tag.
3. **`publish`** construit et pousse les **deux** images sur GHCR (`api` depuis
   `./api`, cible `runner` ; `web` depuis `./web/Dockerfile`, contexte racine),
   tags `X.Y.Z` + `sha-<court>`, scan Trivy (report-only) et signature Cosign keyless.
4. **ArgoCD Image Updater** (annotations dans `application.yaml`, stratégie semver)
   détecte la plus haute version `X.Y.Z` et écrit le tag → sync automatique.

## Activation (une seule fois)

> ⚠️ **Ordre important** : les images `X.Y.Z` doivent EXISTER sur GHCR avant
> d'appliquer l'Application (sinon `ImagePullBackOff`).

```sh
export KUBECONFIG=kubeconfig-hetzner.yaml

# 1) Déclencher la 1re release : merger sur main → release.yml construit
#    ghcr.io/couldevlop/opencacao-{api,web}:0.6.3. Vérifier dans l'onglet Actions.

# 2) Rendre les paquets GHCR accessibles au cluster :
#    - soit les passer en "public" (Packages → Visibility → Public),
#    - soit créer un imagePullSecret ghcr et le référencer (cf. doc GHCR).

# 3) Appliquer l'Application ArgoCD (prend la main sur le namespace opencacao) :
kubectl apply -f deploy/argocd/application.yaml
kubectl -n argocd get application opencacao -w
```

## Bug ArgoCD / K8s 1.35 et fallback

Le cluster est en **K3s v1.35.x**. ArgoCD **v2.13.x** plante au diff client
(`.status.terminatingReplicas: field not declared in schema`) → les syncs échouent
et l'Image Updater n'a aucun effet. Mitigations, dans l'ordre :

1. `ServerSideDiff=true` (déjà posé sur l'Application) — délègue le diff au cluster.
2. **Fix durable** : **upgrader ArgoCD** (≥ v2.14 / v3.x).
3. **En attendant**, déployer un tag à la main :

```sh
KUBECONFIG=kubeconfig-hetzner.yaml deploy/scripts/roll-image.sh 0.6.3
```

## Migration depuis l'ancien déploiement

La prod tournait `docker.io/thomcoul/opencacao-*` posées hors-pipeline. La
`kustomization.yaml` pointe désormais vers **GHCR**. La première bascule se fait soit
par `roll-image.sh 0.6.3`, soit par l'Application ArgoCD une fois les images publiées.
