# Runbook jour J — OpenCacao V3

> **Critère d'acceptation de ce document (spec §9.6) : il doit être utilisable par
> quelqu'un d'autre que Waopron.** Si une étape suppose un savoir qui n'est écrit
> nulle part, c'est un défaut du runbook, pas du lecteur.

Chaque commande est donnée telle qu'elle se tape. Les valeurs à remplacer sont en
`MAJUSCULES`.

---

## 0. Avant de commencer — les cinq choses à savoir

| | |
|---|---|
| **Cluster** | `nexusrh-preprod`, K3s v1.35.x, nœud unique `62.238.11.20` |
| **Accès** | `export KUBECONFIG=kubeconfig-hetzner.yaml` (racine du dépôt), namespace `opencacao` |
| **Déploiement** | `deploy/scripts/roll-image.sh` **et rien d'autre** — voir §1 |
| **Domaine** | `opencacao.openlabconsulting.com`, derrière Cloudflare |
| **Console** | `curation.opencacao.openlabconsulting.com` — publique, protégée par mot de passe |

**Les trois pièges qui ont déjà coûté du temps.** Les trois ont été **vérifiés sur le
cluster le 30/07/2026** — ce ne sont pas des souvenirs.

1. **ArgoCD ne déploie pas OpenCacao.** Une seule Application existe sur le cluster, et
   c'est `openlab-website` :

   ```
   $ kubectl get applications -A
   NAMESPACE   NAME              SYNC STATUS   HEALTH STATUS
   argocd      openlab-website   Synced        Healthy
   ```

   L'Application `opencacao` a été retirée après l'incident du 06/07/2026.
   `roll-image.sh` est l'**unique** chemin. N'attendez pas une synchronisation : elle ne
   viendra pas.

2. **`roll-image.sh` ne synchronise pas la ConfigMap.** Le script change l'image et
   `APP_VERSION`, rien d'autre. Les clés de la V3 ont été ajoutées à la main le
   14/08/2026, en même temps que le déploiement de `0.6.77`.

   > ⚠ **NE JAMAIS FAIRE `kubectl apply -f deploy/k8s/api.yaml`.** Ce fichier contient
   > le Deployment *et* la ConfigMap, et son image y vaut `opencacao-api` — un nom nu,
   > sans registre ni tag, destiné à être réécrit par `kustomization.yaml`. L'appliquer
   > tel quel fait résoudre `docker.io/library/opencacao-api:latest`, qui n'existe pas :
   > **`ImagePullBackOff`, production à terre.** Cette recommandation figurait ici et
   > était fausse ; elle n'a jamais été exécutée.
   >
   > Le fichier ferait de surcroît **régresser cinq valeurs réglées à la main**, relevées
   > le 14/08 : `AUTH_EMAIL_FROM` repasserait de `waopron@` à `noreply@` — que ZeptoMail
   > **refuse** (SM_147), ce qui casse la connexion par lien magique — et `RAG_TOP_K`,
   > `RAG_MIN_SIMILARITE`, `RAG_CANDIDATS`, `APP_VERSION` changeraient aussi.

   Pour ajouter une clé sans rien écraser, on la patche, et on ne touche qu'à elle :

   ```bash
   kubectl -n opencacao patch configmap api-config --type merge \
     -p '{"data":{"MA_CLE":"valeur"}}'
   kubectl -n opencacao rollout restart deploy/api
   ```

   Avant tout patch large, comparer d'abord — le vivant a raison sur le fichier :

   ```bash
   kubectl -n opencacao get cm api-config -o jsonpath='{.data}'
   ```

3. **Cloudflare coupe une réponse d'origine vers 100 secondes** (erreur 524). Toute
   réponse longue doit émettre un premier octet vite. C'est déjà vrai du chat et des
   rapports ; ne l'oubliez pas en changeant un timeout.

**État relevé le 14/08/2026, après déploiement**, pour comparaison :

| | |
|---|---|
| Image servie | `ghcr.io/couldevlop/opencacao-api:0.6.77` (api, curation, web) |
| `APP_VERSION` | `0.6.77` |
| Drapeaux V3 | `PARCELLES_ENABLED` et `RAPPORTS_ENABLED` à **`true`**, `VISION_ENABLED` à `false` |
| Interface | fenêtre unique, trois destinations dans la barre latérale |
| Accès au cluster | **le port 6443 est filtré** depuis l'extérieur ; on passe par `ssh root@62.238.11.20`, où `kubectl` est configuré. Le nœud n'a **pas** de vrai `bash` (`set -o pipefail` échoue) : y exécuter les scripts du dépôt ne marche pas, on déroule les commandes |

**État relevé le 30/07/2026**, pour mémoire :

| | |
|---|---|
| Image servie | `ghcr.io/couldevlop/opencacao-api:0.6.74` |
| `APP_VERSION` | `0.6.74` (la release `0.6.75` existe sur GHCR, non déployée) |
| Nœud | `nexusrh-preprod`, K3s `v1.35.4+k3s1` |
| Mémoire de l'inférence | **4,9 Gi** consommés sur 12 Gi de limite |
| Drapeaux actifs | `AGENTS_ENABLED`, `SEMANTIC_CACHE_ENABLED`, `DIALOGUE_NATUREL_ENABLED` |

---

## 1. Déployer une version

```bash
export KUBECONFIG=kubeconfig-hetzner.yaml
deploy/scripts/roll-image.sh X.Y.Z          # ex. 0.6.75
```

Le script patche `APP_VERSION` dans la ConfigMap, bascule les images de `api`,
`curation` et `web`, purge le cache de réponses Redis (`cache:chat:*`) et, si
`CF_API_TOKEN` et `CF_ZONE_ID` sont exportés, purge le cache Cloudflare.

**Vérifier ce qui tourne réellement** — le tag Git le plus récent ne dit rien :

```bash
kubectl -n opencacao get deploy api \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl -n opencacao get rs -l app=api --sort-by=.metadata.creationTimestamp
curl -s https://opencacao.openlabconsulting.com/v1/health
curl -s https://opencacao.openlabconsulting.com/v1/ready
```

> **Précédent, 06/07/2026 :** trois minutes après une release, quelqu'un a lancé
> `roll-image.sh` avec un **ancien** tag, faisant régresser la production de quatre
> versions pendant 24 h. La signature était visible dans `APP_VERSION`. **Après chaque
> déploiement, relisez le tag effectivement servi.**

### Retour arrière

```bash
deploy/scripts/roll-image.sh TAG_PRECEDENT
```

C'est la même commande. Le retour arrière n'est pas une procédure spéciale, ce qui est
voulu : sous pression, on ne veut pas apprendre une seconde commande.

---

## 2. Bascule GPU

**À répéter et chronométrer au moins deux fois avant le jour J** (spec §9.6), aller
**et** retour. Une bascule découverte le jour même est une bascule ratée.

### Les deux commandes, et laquelle choisir

```bash
deploy/scripts/jour-j.sh ouvrir            # l'ÉVÉNEMENT : matériel + fonctionnalités
deploy/scripts/profil.sh gpu               # le MATÉRIEL seul
```

`profil.sh` bascule le matériel et rien d'autre. `jour-j.sh` bascule l'événement : il
appelle `profil.sh`, monte le service de vision, **puis** ouvre au public les
fonctionnalités que le GPU débloque — et sait tout refermer.

La séparation est volontaire. Un drapeau de fonctionnalité n'est pas une capacité
matérielle ; les confondre, c'est ouvrir l'atelier au public parce qu'on voulait un
GPU. **En répétition, on utilise `profil.sh`** : on éprouve la bascule sans rien ouvrir.

```bash
deploy/scripts/jour-j.sh ouvrir http://100.x.y.z:8000   # sur un GPU loué (§2.3 bis)
deploy/scripts/jour-j.sh fermer                         # après la démonstration
deploy/scripts/jour-j.sh etat                           # ne change rien
```

**`fermer` ferme d'abord, éteint ensuite** — l'ordre inverse laisserait l'API annoncer
la vision et l'atelier alors que le GPU n'est déjà plus là. Il baisse `RAPPORTS_ENABLED`
et `VISION_ENABLED`, revient au CPU, met la vision à zéro réplique, et affiche ce qu'il
ne peut pas faire : **arrêter un pod loué, facturé à l'heure**.

`PARCELLES_ENABLED` reste levé : la cartographie ne mobilise aucun modèle. `RAPPORTS`
redescend parce qu'une étude coûte plusieurs minutes de CPU et que l'inférence ne sert
**qu'une requête à la fois** — un visiteur lançant un document bloquerait le chat pour
tout le monde. La spec §4.1 prévoit la file nocturne par cron pour cet usage ; elle
n'existe pas encore.

> Depuis le 14/08, l'interface **suit** ces drapeaux : `/v1/version` déclare ses
> capacités et la barre latérale masque les destinations fermées. Baisser un drapeau
> ne laisse donc plus une porte qui ne mène nulle part.

### 2.0 Migration à faire UNE FOIS, hors répétition

Jusqu'au 14/08/2026, `inference.yaml` et `inference-gpu.yaml` portaient **le même nom
d'objet** : appliquer l'un supprimait l'autre. Le retour au CPU imposait donc de
recharger le GGUF — plusieurs minutes — là où la spec §4.5 promet « moins de deux
minutes » par un simple `kubectl scale`. C'est corrigé : deux Deployments distincts
(`inference` en CPU, `inference-gpu`), un seul Service qui suit le label `role:
inference`, porté par les deux.

Cette bascule de labels demande **une** application, qui **redémarre l'inférence CPU**
(le gabarit de pod change, donc le pod est recréé — comptez le rechargement du GGUF).
À faire à un moment calme, jamais pendant une répétition. L'ordre des deux `apply` est
indifférent : chaque profil est cloisonné par sa propre NetworkPolicy, sur un label que
les pods portent avant comme après.

```bash
export KUBECONFIG=kubeconfig-hetzner.yaml
kubectl -n opencacao apply -f deploy/k8s/inference.yaml
kubectl -n opencacao apply -f deploy/k8s/networkpolicy.yaml
kubectl -n opencacao rollout status deploy/inference --timeout=300s
deploy/scripts/profil.sh etat        # doit montrer des endpoints et profil=cpu
```

### 2.1 Prérequis, à vérifier la veille

```bash
# Le nœud expose-t-il un GPU ?
kubectl get nodes -o json | grep -c "nvidia.com/gpu"

# Les poids sont-ils en place sur le nœud ? (modèle fusionné, PAS le GGUF)
ls -la /opt/opencacao/models/opencacao-8b/
# et, pour la vision :
ls -la /opt/opencacao/models/qwen3-vl-8b-Q4_K_M.gguf \
       /opt/opencacao/models/qwen3-vl-8b-mmproj-f16.gguf
```

Renseigner le `nodeSelector` et les `tolerations` de `deploy/k8s/inference-gpu.yaml`
selon les labels réels du nœud — commentés par défaut, car ils dépendent du
fournisseur. **Sans eux, le pod peut rester `Pending` sans explication visible.**

### 2.2 Bascule (chronométrer à partir d'ici)

```bash
export KUBECONFIG=kubeconfig-hetzner.yaml
deploy/scripts/profil.sh gpu
```

**C'est tout, et c'est voulu** : sous pression, on ne veut pas exécuter cinq commandes
dans le bon ordre. Le script monte le pod GPU, attend qu'il soit prêt, **puis seulement**
descend le CPU à zéro réplique (jamais l'inverse : descendre d'abord ouvrirait un trou de
plusieurs minutes), patche `PROFIL_MATERIEL` et `INFERENCE_BACKEND`, redémarre l'API,
vérifie `/v1/ready` et **affiche la durée à reporter au §6**.

`deploy/scripts/profil.sh etat` ne change rien et dit où on en est — y compris si le
Service n'a aucun endpoint, la panne la plus silencieuse du lot.

**La vision n'est pas allumée par la bascule**, délibérément : c'est un drapeau de
fonctionnalité, pas une capacité matérielle, et son budget de latence se décide en
connaissance de cause (§3). Une fois le GPU en place :

```bash
kubectl -n opencacao apply -f deploy/k8s/vision.yaml
kubectl -n opencacao rollout status deploy/vision --timeout=15m
kubectl -n opencacao patch configmap api-config --type merge \
  -p '{"data":{"VISION_ENABLED":"true"}}'
kubectl -n opencacao rollout restart deploy/api
```

### 2.3 Vérifier

```bash
curl -s https://opencacao.openlabconsulting.com/v1/ready
curl -s https://opencacao.openlabconsulting.com/v1/version
# Une vraie question, chronométrée :
time curl -s -X POST https://opencacao.openlabconsulting.com/v1/chat \
  -H 'Content-Type: application/json' -H 'X-Device-Id: recette-jour-j' \
  -d '{"question":"Quand récolter le cacao ?","langue":"fr"}' | head -c 300
```

**Noter les temps mesurés dans le tableau du §6.** Un chiffre non écrit est un chiffre
perdu.

### 2.3 bis Bascule vers un GPU LOUÉ (RunPod) — le chemin réellement retenu

`profil.sh gpu` suppose une carte **dans** le cluster. Or la décision matérielle de la
spec §4.3 est **RunPod**, le Hetzner GEX44 étant indisponible : l'inférence part alors
**hors** du cluster et il n'y a aucun pod GPU à mettre à l'échelle. Ce qui bascule est
`INFERENCE_URL`.

```bash
export KUBECONFIG=kubeconfig-hetzner.yaml
export INFERENCE_API_KEY=<le même jeton que le --api-key de vLLM>   # pour la vérification
deploy/scripts/profil.sh runpod http://100.x.y.z:8000              # adresse du TUNNEL
```

Le script interroge le point de terminaison **avant de toucher à quoi que ce soit**. Un
`401` est une bonne nouvelle — vLLM répond et il est protégé. Un endpoint injoignable
annule la bascule sans rien modifier : le CPU continue de servir.

Il **refuse** une adresse `*.proxy.runpod.net` : D1 interdit d'exposer l'inférence, et
§4.5 impose un tunnel privé. L'échappatoire existe (`AUTORISER_ENDPOINT_PUBLIC=1`) mais
elle est hors doctrine.

**Le CPU n'est pas éteint automatiquement** dans ce mode. Le script vous rend la main
avec la commande à lancer quand vous aurez vu le service répondre — de la RAM
immobilisée coûte moins cher qu'une salle devant une page blanche.

**Les quatre prérequis, dans l'ordre où ils bloquent** (§4.5) :

| | État au 14/08/2026 |
|---|---|
| Modèle fusionné quantifié AWQ, poussé sur un dépôt privé | **à faire** — aucun script AWQ dans le dépôt |
| Image vLLM téléchargeant le modèle au démarrage | **à faire** — le manifeste actuel monte un `hostPath` |
| Tunnel privé Tailscale/WireGuard entre le CX53 et le pod | **à faire** — rien dans `deploy/` |
| `--api-key` côté vLLM + Secret `opencacao-inference` côté API | **fait** (14/08) — le client porte le jeton |

### 2.4 Retour au CPU

```bash
deploy/scripts/profil.sh cpu
```

**La même commande, dans l'autre sens** — c'est le seul geste à retenir des deux. Le
script remonte le CPU **avant** d'éteindre le GPU (s'il lâche en scène, on ne commence
pas par éteindre ce qui répond encore), remet `PROFIL_MATERIEL`, `INFERENCE_BACKEND` et
`VISION_ENABLED` à leurs valeurs CPU, et redémarre l'API.

Le déploiement GPU n'est pas supprimé, seulement mis à zéro réplique : repartir sur GPU
ne repaiera pas la création de l'objet.

> **Le repli CPU est le plan de secours du plan de secours.** Il doit être chronométré
> lui aussi : c'est ce qu'on exécutera sous pression si le GPU lâche en scène. Le script
> affiche la durée ; elle va au §6.

---

## 2.5 Le repli AUTOMATIQUE — la sentinelle

`profil.sh cpu` suppose **quelqu'un pour le taper**. Pendant la présentation, ce
quelqu'un est sur scène. La sentinelle (`deploy/k8s/sentinelle.yaml`) comble ce trou :
c'est un pod qui sonde l'API et rentre au CPU tout seul.

**Ce qu'elle fait.** Toutes les **15 s**, elle interroge `/v1/health` puis `/v1/ready`
sur le Service interne. Après **3 échecs consécutifs de l'inférence** (~45 s) — et
seulement si `PROFIL_MATERIEL` vaut `gpu` — elle :

1. remonte `inference` (CPU) à 1 réplique ;
2. patche la ConfigMap : `PROFIL_MATERIEL=cpu`, `INFERENCE_BACKEND=llama-cpp`,
   `INFERENCE_URL=http://inference:8000` ;
3. **déleste** : `RAPPORTS_ENABLED=false`, `PARCELLES_ENABLED=false`,
   `VISION_ENABLED=false` ;
4. lève `REPLI_CPU=true` — ce qui fait afficher le bandeau « service de secours » ;
5. redémarre l'API, puis envoie un email.

**Pourquoi le délestage.** Sur CPU, l'inférence ne sert **qu'une requête à la fois**.
Une étude lancée pendant un repli monopoliserait le moteur plusieurs minutes et la
conversation — le produit — mourrait avec. On garde ce qui est regardé en direct.
*Noter la divergence avec `jour-j.sh fermer`, qui laisse les parcelles ouvertes : une
fermeture est délibérée et calme, un repli est subi et se produit devant une salle.*

**Ce qu'elle ne fait JAMAIS**, et c'est ce qui la rend sûre :

| Jamais | Pourquoi |
|---|---|
| Aller vers le GPU | Ça coûte de l'argent et engage un fournisseur : décision humaine |
| Agir en profil `cpu` | Sinon une maintenance délibérée devient une lutte contre une boucle |
| Éteindre `inference-gpu` | Un pod lent n'est pas un pod mort. `inference-gpu` n'est même pas dans son RBAC |
| Replier sur une panne d'API | Si `/v1/health` ne répond pas, ce n'est pas l'inférence : elle alerte, elle n'agit pas |
| Parler au modèle | Elle n'interroge que la santé de l'API — D1 réserve l'inférence à l'API |

**Ce que la salle voit.** Un bandeau ambre sous l'en-tête : *« Service de secours — nous
servons actuellement OpenCacao sur nos serveurs de repli. La conversation fonctionne
normalement. L'atelier de documents et le suivi de parcelle sont mis en pause… »* Les
deux destinations restent visibles, marquées **« en pause »** et non « bientôt » — dire
« bientôt » à quelqu'un qui s'en servait une minute plus tôt serait faux.

**Vérifier qu'elle veille :**

```bash
kubectl -n opencacao get deploy sentinelle
kubectl -n opencacao logs deploy/sentinelle --tail=20   # une ligne `sentinelle_cycle` toutes les 15 s
```

**La désarmer** (répétition d'une bascule à la main, sans qu'elle contrarie) :

```bash
kubectl -n opencacao scale deploy sentinelle --replicas=0
# … puis la réarmer :
kubectl -n opencacao scale deploy sentinelle --replicas=1
```

> **À faire avant de répéter la bascule (§2.3) : la désarmer.** Sinon, pendant les
> minutes de chargement du modèle sur le GPU, elle constatera trois échecs et rentrera
> au CPU au milieu de votre répétition — en faisant exactement son travail.

> **À FAIRE APRÈS L'ÉVÉNEMENT — l'écran d'exploitation.** La spec (mitigation M6)
> exige que le repli soit « exécutable par quelqu'un d'autre que Waopron ». Aujourd'hui
> il ne l'est qu'en ligne de commande, par quelqu'un qui a le kubeconfig et sait s'en
> servir. Le bon aboutissement est un écran dans la console de curation : état du profil
> en clair, bouton « rentrer sur CPU », bouton « reprendre le service normal ».
> Conception arrêtée le 19/08/2026, construction reportée faute de temps avant la
> présentation — et parce qu'un point de terminaison qui reconfigure la production, sur
> une console exposée sur Internet, ne s'ajoute pas à 3 h du matin.
>
> Périmètre retenu, quand il se fera : réutiliser `app.exploitation.sentinelle.replier`
> (le bouton doit faire EXACTEMENT ce que fait la sentinelle, pas une seconde
> implémentation) ; session existante de la console ; SameSite=Lax + en-tête
> personnalisé contre le CSRF ; limitation de débit stricte ; journal d'audit ; droits
> RBAC à étendre à `configmaps/api-config` et `deployments/inference` pour le compte
> `curation`. **Le passage VERS le GPU reste en ligne de commande** : il demande une URL
> de tunnel, et un champ d'URL libre qui reconfigure la production est précisément la
> surface qu'on refuse d'ouvrir sur une console publique.

> **À FAIRE APRÈS L'ÉVÉNEMENT — OCR des documents scannés (`baidu/Unlimited-OCR`).**
> Demande de Waopron le 19/08/2026. Constat : la console de curation ingère les PDF
> avec `pypdf`, qui n'extrait **rien** d'un document scanné. Les pièces FIRCA du dépôt
> (livre d'or des 20 ans, plaquette SARA 2025, politique LCB-FT) n'ont jamais pu
> entrer dans le RAG pour cette seule raison.
>
> `baidu/Unlimited-OCR` (licence MIT, dérivé des travaux DeepSeek-OCR) est le bon
> outil et sa licence convient à la thèse souveraine. Trois contraintes à respecter :
>
> 1. **C'est un outil de corpus HORS LIGNE**, comme l'enrichissement — il vit dans
>    `training/` ou `scripts/`, jamais dans l'API. `torch` et `transformers` ne sont
>    pas dans les dépendances épinglées de la spec §2.1 ; les faire entrer dans
>    l'image de production serait une régression d'architecture. Seul le TEXTE produit
>    revient au cluster.
> 2. **Il est documenté pour GPU** (CUDA 12.9/13.0, bfloat16). Sur CPU il tournerait
>    en float32, à des heures par document — et sur le CX53 il affamerait l'inférence
>    qui sert le chat. À exécuter sur un pod GPU, ou en traitement de nuit.
> 3. **Réindexer le RAG est l'opération à risque** : la régression de rappel de
>    juillet (0,75 -> 0,27) est partie de là. Mesurer le rappel AVANT et APRÈS, sur le
>    jeu de questions de `docs/demo/questions.txt`, et garder l'index précédent.

**Effacer le bandeau après un repli** : c'est `jour-j.sh ouvrir` (on repart) ou
`jour-j.sh fermer` (on clôt l'événement) — les deux remettent `REPLI_CPU=false`.
`profil.sh cpu` ne l'efface pas volontairement : il bascule le matériel, il ne décide
pas de ce que le service promet.

---

## 3. Les drapeaux de la V3

Trois fonctionnalités sont livrées mais **coupées**, chacune par un drapeau. Les
activer se fait à chaud, sans redéploiement d'image.

| Drapeau | État | Ce qu'il ouvre | À savoir avant d'activer |
|---|---|---|---|
| `PARCELLES_ENABLED` | `false` | Parcelles et captures terrain (C1) | Crée `parcelles.db` sur `/data` |
| `VISION_ENABLED` | `false` | Analyse visuelle des captures (C2) | **Inerte sans GPU** : `PROFIL_MATERIEL` doit valoir `gpu`, sinon l'API répond 503 |
| `RAPPORTS_ENABLED` | `false` | Atelier de livrables (C3) | Une étude enchaîne **une génération par section** : mesurer le budget CPU total avant |
| `REPLI_CPU` | `false` | *(n'ouvre rien)* Affiche l'avis « service de secours » et ferme les capacités lourdes | **Levé par la sentinelle, pas à la main** (§2.5). Remis à `false` par `jour-j.sh ouvrir\|fermer` |

```bash
kubectl -n opencacao patch configmap api-config --type merge \
  -p '{"data":{"PARCELLES_ENABLED":"true"}}'
kubectl -n opencacao rollout restart deploy/api
```

**Budget de latence — le point à trancher avant d'activer la vision.** Le constat visuel
est **synchrone** : il enchaîne `VISION_TIMEOUT_S` puis `REQUEST_TIMEOUT_S`. Valeurs
réellement en ConfigMap aujourd'hui :

```
REQUEST_TIMEOUT_S = 300      <-- relevé sur le cluster, et non 120 (défaut du code)
VISION_TIMEOUT_S  =  30      <-- défaut du code, la clé est absente de la ConfigMap
```

Le cumul brut atteindrait **330 s** quand Cloudflare coupe vers 100 : le client verrait
un 524 et la génération continuerait côté serveur pour rien.

**C'est réglé, mais par une borne explicite, pas par ces deux valeurs.** Le service du
constat s'accorde un budget total de **75 s** (`BUDGET_CONSTAT_S` dans
`api/app/services/constats.py`) : au-delà, il abandonne et rend la consigne qui oriente
vers l'ANADER — le repli déjà prévu par la cascade — plutôt que de laisser l'edge
couper. `REQUEST_TIMEOUT_S` reste à 300 volontairement : il est aligné sur le
`proxy-read-timeout` de l'ingress et sert le flux du chat, où une composition
multi-agents prend jusqu'à trois minutes. **Le baisser casserait ce que le correctif du
524 de juillet avait réparé.**

Ce qui reste à surveiller après la bascule GPU : si le budget de 75 s se révèle trop
court pour une analyse d'image sur GPU, l'ajuster **là** — pas en touchant au timeout
global.

---

## 4. Surveillance en direct

```bash
# Journaux de l'API, filtrés sur ce qui compte
kubectl -n opencacao logs -f deploy/api | grep -E "error|warning|refus|echoue"

# Mémoire de l'inférence — la cause de l'OOM-kill du 28/06/2026
kubectl -n opencacao top pod -l app=inference

# Redémarrages : un compteur qui bouge est le premier signe d'un OOM
kubectl -n opencacao get pods -w
```

**Seuils d'alerte.** Le pod d'inférence est plafonné à 12 Gi et a déjà été tué pour
dépassement. Si `top pod` approche cette limite, ne pas attendre : vérifier que
`--cache-ram` est bien à 1024 dans le manifeste servi.

**Alertes automatiques.** Un CronJob `watchdog-enrichissement` tourne chaque jour à
04:30 UTC et alerte par email si l'enrichissement du corpus n'a pas réussi depuis plus
de 26 h. L'email part par ZeptoMail.

> **L'expéditeur DOIT être `waopron@openlabconsulting.com`.** `noreply@` est refusé par
> ZeptoMail (erreur `SM_147`). Si les alertes cessent, vérifier d'abord cela.

---

## 5. Plan de secours — à dégainer sans hésiter

L'ordre compte : chaque palier est plus sûr et plus rapide que le précédent.

| Palier | Quand | Comment |
|---|---|---|
| **1. Couper la fonctionnalité fautive** | Une seule brique déraille | Passer son drapeau à `false` + `rollout restart deploy/api` (~1 min) |
| **2. Repli CPU AUTOMATIQUE** | Le GPU lâche pendant la présentation | **Rien à faire** : la sentinelle rentre au CPU en ~45 s et le dit à l'écran (§2.5) |
| **2 bis. Repli CPU manuel** | On veut devancer la sentinelle, ou elle est désarmée | §2.4 (chronométré à l'avance) |
| **3. Retour arrière d'image** | La version déployée est en cause | `roll-image.sh TAG_PRECEDENT` |
| **4. Hors-ligne** | Le service est inaccessible | Captures d'écran et enregistrements préparés — voir ci-dessous |

**Le palier 4 doit être prêt AVANT le jour J**, pas improvisé : enregistrement vidéo du
scénario complet joué en production, et captures des écrans clés. Sans lui, une panne
réseau dans la salle suffit à interrompre la démonstration.

- [ ] Enregistrement du scénario complet — **à produire**
- [ ] Captures des écrans clés — **à produire**
- [ ] Fichiers de livrables (`.docx`, `.xlsx`, `.pptx`) déjà générés, sur la machine de
      présentation — **à produire**

---

## 6. Mesures — à remplir pendant les répétitions

### Où en sont les critères d'acceptation (spec §9.6)

| Critère | État |
|---|---|
| Sous charge simulée, la file annonce une position et aucune requête ne meurt en silence | **Acquis.** Vérifié en continu par `api/tests/test_charge_file_attente.py` : douze demandes concurrentes pour une place, chacune repart avec une issue nette — servie ou refusée lisiblement. |
| Bascule GPU puis retour CPU, deux fois, chronométrés | **En attente du GPU.** Rien ne peut être mesuré avant. |
| Scénario complet joué en production, deux fois de suite | **En attente** des questions de Waopron et de la bascule. |
| Plan de secours utilisable par quelqu'un d'autre | **À éprouver** — le seul juge est quelqu'un qui n'a pas écrit ce document. |

Le premier critère est le seul qui ne dépend ni du matériel ni d'une répétition : il est
donc verrouillé par un test plutôt que par une observation ponctuelle. Ce test a d'ailleurs
trouvé une incohérence à l'écrit : une demande s'entendait annoncer « position 9 » alors
que le plafond d'attente est à 8, puis se faisait refuser dans la foulée. On ne promet plus
une place qui n'existe pas.

### Chronométrage des répétitions

Un tableau vide le jour J signifie que les répétitions n'ont pas eu lieu.

| Répétition | Date | Bascule GPU | Retour CPU | Latence 1ʳᵉ question | Incident |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |

---

## 7. Qui fait quoi

À remplir avant le jour J : un runbook sans noms laisse chacun supposer que l'autre
s'en occupe.

| Rôle | Personne | Joignable à |
|---|---|---|
| Pilote de la démonstration | | |
| Surveillance technique (journaux, mémoire) | | |
| Décision de repli | | |
