# V3 opérationnelle — parcelle, vision, livrables traçables

> **Statut** : design validé par Waopron Coulibaly le 28/07/2026.
> **Échéance** : présentation publique devant ~800 personnes, dans 2 à 4 semaines.
> **Portée** : spec maîtresse. Chaque chantier (C1 à C5) reçoit ensuite son propre plan
> d'implémentation TDD dans `docs/superpowers/plans/`.
> **Exécution** : l'implémentation sera conduite dans des sessions distinctes. Cette spec est
> donc rédigée pour une exécution autonome — chemins de fichiers explicites, critères
> d'acceptation vérifiables, aucune décision laissée à l'interprétation.

---

## 1. Pourquoi cette V3

La V3 livrée à ce jour est une **plateforme agentique conversationnelle** : sept agents, un
orchestrateur, un routeur déterministe, du RAG ancré, des garde-fous centralisés. Elle répond
à des questions. C'est solide, et c'est insuffisant pour ce qui est demandé.

Trois exigences nouvelles la refondent :

1. **Aller au-delà du chat.** Le système doit produire des **livrables** — études de filière,
   dossiers de parcelle, bulletins régionaux — en Word, PowerPoint et Excel.
2. **Voir.** Le producteur doit pouvoir scanner sa plantation : photos, vidéo, parcours GPS du
   contour, ou les deux à la fois.
3. **Tracer.** Chaque affirmation d'un rapport doit porter sa provenance, et le rapport
   lui-même doit être rejouable.

La bascule tient en une phrase : **OpenCacao cesse d'être un assistant qui répond et devient un
instrument qui constate, documente et produit.** L'objet central n'est plus la question, c'est
la **parcelle**.

### Ce que la présentation exige, publics confondus

L'assemblée réunit producteurs et coopératives, institutions et bailleurs, techniciens ANADER
et chercheurs, entreprises et exportateurs. Chaque brique de cette V3 doit parler à au moins
deux de ces publics, sinon elle sort du périmètre.

| Public | Ce qui le convainc |
|---|---|
| Producteurs, coopératives | scanner sa parcelle depuis un téléphone, obtenir un constat compréhensible |
| Institutions, bailleurs | une étude de filière sourcée, rejouable, en Word et PPTX |
| Techniciens, chercheurs | l'abstention du modèle, la boucle de revue humaine, le rappel par classe publié |
| Entreprises, exportateurs | le dossier de traçabilité EUDR, géolocalisation et constat satellite daté |

---

## 2. Doctrine — non négociable

Ces cinq règles précèdent toute décision technique. Une implémentation qui les contredit est
fausse, même si elle passe les tests.

**D1 — Souveraineté.** Aucun service externe d'IA en production. Tous les modèles, y compris le
modèle de vision, sont à poids ouverts et servis localement. Les outils appellent des *sources
de données* (Open-Meteo, Global Forest Watch, Conseil du Café-Cacao), jamais un LLM tiers,
toujours derrière un port mockable.

**D2 — Cacao uniquement.** Périmètre inchangé (décision Waopron, juin 2026). Le vivrier et
l'anacarde sont redirigés vers l'ANADER. Une parcelle qui n'est pas une cacaoyère n'entre pas
dans le système.

**D3 — Pas de diagnostic autonome.** *Reformulation du garde-fou d'origine, arbitrée par
Waopron le 28/07/2026.* L'interdit passe de « pas d'analyse d'image » à « pas de diagnostic
autonome ». Le système **constate et signale** : il décrit ce qu'il observe, affiche sa
confiance, ne nomme jamais un produit ni un dosage, et transmet un constat horodaté à
l'ANADER pour confirmation humaine. Le pré-diagnostic étiologique — nommer la maladie probable
— ne s'active pas à une date mais au franchissement d'un **seuil de rappel par classe** mesuré
sur un jeu de test de terrain (§7.5). À reporter dans `CLAUDE_OpenCacao.md` et dans
`CLAUDE.md`.

**D4 — Chaque chiffre porte sa source, ou il n'apparaît pas.** Une section de rapport sans
source disponible dégrade en **constat de lacune explicite** (« cette donnée n'est pas
disponible dans les sources indexées »), jamais en estimation, jamais en ordre de grandeur
plausible. Cette règle s'applique au moteur de rédaction comme aux agents.

**D5 — Jamais une déclaration de conformité.** Les livrables EUDR sont des **dossiers
préparatoires à la diligence raisonnée**, que l'opérateur assume et signe. Le système ne
certifie rien, ne conclut pas à la conformité, et l'écrit en tête de chaque dossier. Règle déjà
en vigueur pour l'agent Satellite (A8), étendue à tous les livrables.

---

## 3. Architecture des modèles

Six rôles, deux profils matériels. Aucun rôle n'en assume deux.

Ce tableau décrit l'architecture **cible**, une fois tous les chantiers livrés. Ce qui est
effectivement servi le jour de la présentation est plus restreint : voir §7.2, où les étages 2
et 3 de la cascade sont explicitement reportés après l'événement.

| Rôle | Modèle | Taille | Profil GPU | Profil CPU |
|---|---|---|---|---|
| Conseil & rédaction | `Ministral-3-8B-Instruct-2512` + LoRA cacao | 8 Md | vLLM, BF16 ou AWQ 4 bits | llama.cpp GGUF Q4/Q5 |
| Brouillon spéculatif | auto-distillé, ou `Luciole-1B-Instruct` après C5 | 1 Md | inutile | **essentiel** (1,7-1,9×) |
| Embeddings RAG & cache sémantique | `Qwen3-Embedding-0.6B` *(en place)* | 0,6 Md | actif | actif |
| Description visuelle | `Qwen3-VL` (poids ouverts) | 4-8 Md | actif | **éteint** |
| Recevabilité d'image (étage 0) | aucun modèle — métriques navigateur + en-têtes serveur (§6.2) | — | actif | actif |
| Organe, lésions, étiologie (étages 1-3) | ViT/DINOv2 affiné + détecteur de lésions | 20-90 M | actif | **actif**, ~100-300 ms/image |

### 3.1 La conséquence architecturale à retenir

La dernière ligne est le cœur du dispositif. Une cascade de petits modèles spécialisés — 20 à
90 millions de paramètres — **tient sur le CX53 en CPU**, à côté du 8B, pour un coût mémoire
négligeable. En profil CPU, seul le VLM descriptif s'éteint. Le diagnostic survit à la
dégradation. C'est ce qui rend le retour au CPU acceptable après l'événement, et c'est la
raison pour laquelle on refuse de confier l'étiologie à un VLM généraliste (voir §7 : les VLM
sont de bons descripteurs et de mauvais diagnosticiens).

### 3.2 Migration Luciole — décision et justification

Question posée : remplacer Mistral par « Lucie-8B/7B/16B » ?

**Faits établis le 28/07/2026.** La famille visée s'appelle **Luciole** (Lucie-7B est la
génération antérieure ; il n'existe pas de 16B, l'étage supérieur est un 23B).
`Luciole-8B-Instruct-1.1` : 8 Md de paramètres, **architecture Mamba hybride**, licence
Apache 2.0, contexte 16 384 tokens en instruct (131 k sur la base), GGUF officiels compatibles
llama.cpp et vLLM, produit par LINAGORA et le consortium OpenLLM-France sur financement BPI
France / France 2030, **jeu de données d'entraînement publié**.

`Ministral-3-8B-Instruct-2512` est **également Apache 2.0**, avec 256 k de contexte. Il n'y a
donc aucun problème de licence à résoudre : l'arbitrage se joue ailleurs.

**Deux arguments pour la migration.**

*La reproductibilité.* Les deux modèles sont français et sous la même licence ; seul Luciole
publie ses données d'entraînement. « Je peux vous montrer sur quoi mon modèle a appris » sert
directement l'une des trois valeurs affichées du projet.

*Le brouillon spéculatif gratuit.* Luciole existe en 1B. Un 1B et un 8B de la même famille
partagent le vocabulaire : c'est une **paire de décodage spéculatif officielle**. La spec
`2026-07-24-brouillon-speculatif-souverain-design.md` visait 1,7 à 1,9× en auto-distillant un
brouillon depuis le 8B — un chantier entier qui deviendrait inutile. C'est précisément le levier
nécessaire au retour sur CPU.

**L'argument décisif contre, à cette échéance.** La LoRA cacao est entraînée sur Ministral.
Changer de socle impose de réentraîner l'adaptateur, revalider les ~592 tests, revérifier
chaque garde-fou et refaire la recette agronomique — en parallèle de quatre autres chantiers,
à trois semaines d'une présentation publique. Le contexte tomberait par ailleurs de 256 k à
16 k, ce qui n'est pas neutre avec les prompts RAG actuels.

**Décision : chantier C5, après l'événement, en migration mesurée** (§10). À la tribune,
Luciole est présentée comme la trajectoire, sans engagement technique.

---

## 4. Profils matériels et stratégie de coût

### 4.1 Le drapeau `profil_materiel`

Nouveau réglage dans `api/app/core/config.py` : `profil_materiel: Literal["gpu", "cpu"] = "cpu"`.
Il ne choisit pas un backend — `inference_backend` le fait déjà — il **déclare quelles
capacités sont disponibles**. Toute fonctionnalité coûteuse l'interroge, et le mode CPU est le
défaut : une erreur de configuration dégrade, elle ne casse pas.

| Capacité | `gpu` | `cpu` |
|---|---|---|
| Chat interactif | ~8-10 s pour 400 tokens | ~15-38 s, brouillon spéculatif actif |
| Vision descriptive (VLM) | active | **inactive** — message explicite, pas d'erreur |
| Cascade de vision (étages 0-3) | active | active |
| Études de filière | à la demande, quelques minutes | **file nocturne par cron** |
| Dossier de parcelle | à la demande | à la demande (peu de générations) |
| Composition multi-agents | active | active, bornée à `MAX_CONTRIBUTEURS` |

### 4.2 Le GPU ne sert qu'à ce qui est regardé en direct

**L'atelier de livrables n'a pas besoin de GPU.** Personne n'attend devant un rapport. Une
étude de quinze pages à 15 tok/s représente 30 à 45 minutes de décodage : parfaitement
acceptable pour un livrable asynchrone produit la nuit par cron. Le GPU ne bénéficie qu'au chat
de scène et au VLM descriptif.

Conséquence budgétaire : l'exigence GPU porte sur **une fenêtre d'événement**, pas sur le
produit en régime permanent.

### 4.3 Recommandation matérielle

| Option | Matériel | Coût | Verdict |
|---|---|---|---|
| **Hetzner GEX44** | RTX 4000 SFF Ada 20 Go, i5-13500, 64 Go, 2×1,92 To NVMe | **184 €/mois + 79 € d'installation**, HT | **retenu** — même fournisseur que le cluster ; 20 Go tiennent le 8B en AWQ (~6 Go) + Qwen3-VL-4B + embeddings, avec marge KV |
| Hetzner GEX131 | RTX PRO 6000 Blackwell Max-Q 96 Go | 889 €/mois | surdimensionné |
| Hetzner GEX130 | RTX 6000 Ada 48 Go | 838 €/mois | apparemment retiré du catalogue |
| Pod horaire (RunPod) | L4 / A5000 | quelques € la journée | insuffisant : pas de persistance ni d'IP stable pour un service |

**Retenu : un mois de GEX44.** Un pod horaire couvrirait le seul jour J ; le mois achète les
répétitions, la marge de panne en direct, et la fenêtre nécessaire pour constituer le jeu de
photos ivoirien via la boucle de revue (§7.6). Retour au CX53 en profil CPU ensuite, sur
**mesure d'affluence** — pas sur principe. Prolonger d'un mois si la fréquentation
post-présentation le justifie reste une décision légitime, prise sur chiffres.

*Tarifs relevés le 28/07/2026, hors TVA ; à revérifier avant commande.* **À vérifier également
avant de s'engager** : le GEX44 est un serveur **dédié**, avec frais d'installation et
possiblement une durée minimale ou un préavis de résiliation. « Un mois » n'est pas
nécessairement aussi résiliable qu'une instance cloud.

### 4.4 Contraintes concrètes du GEX44

C'est un **serveur dédié root** : accès complet, dépôt des GGUF et lancement de vLLM ou
llama.cpp en Docker comme aujourd'hui. 64 Go de RAM et 2×1,92 To de NVMe : aucune contrainte de
ce côté.

**Le budget VRAM est la vraie limite : 20 Go.** Le 8B en BF16 occupe ~16 Go, ce qui ne laisse
rien au modèle de vision. Il faut donc **quantifier le modèle fusionné en AWQ ou GPTQ 4 bits**
(~5-6 Go), libérant ~13 Go pour Qwen3-VL-4B et le cache KV. Cette quantification est une étape
de préparation à part entière, à prévoir avant le jour J.

**Débit attendu, sans optimisme.** La RTX 4000 SFF Ada offre ~280 Go/s de bande passante
mémoire ; un 8B en 4 bits plafonne donc autour de **40-50 tok/s**. Une réponse de 400 tokens
prend **8 à 10 secondes**, pas 2 ou 3. C'est une transformation face aux ~38 s du CPU, et le
streaming la rend confortable — mais aucune communication publique ne doit annoncer 3 secondes.

**Le gain décisif n'est pas la latence, c'est le débit agrégé.** llama.cpp sur CPU traite **une
requête à la fois** ; vLLM les **regroupe**, et vingt utilisateurs simultanés partagent la même
lecture des poids. Pour la phase d'ouverture au public après la présentation, cela compte
davantage que la latence d'un utilisateur isolé.

**Rattachement au cluster.** Le GEX44 est une machine distincte du nœud CX53. Deux options : la
**joindre au cluster K3s comme second nœud** (les Deployments `inference` et `vision` reçoivent
alors un `nodeSelector`), ce qui préserve intact le pipeline GitOps ; ou la laisser autonome et
faire pointer `inference_url` vers elle par le réseau privé. **La première option est retenue** :
elle évite de créer un chemin de déploiement parallèle à maintenir dans l'urgence.

---

## 5. L'objet central : la parcelle

### 5.1 Modèle de domaine

Nouveau module `api/app/models/parcelle.py`, types purs sans dépendance framework :

```
Parcelle
  identifiant           str        opaque, stable
  compte                str        rattachement au compte magic-link (D2 existant)
  nom                   str        libellé donné par le producteur
  localite              str        rattachée au module partagé services/localites.py
  direction_regionale   str        l'une des 10 DR (services/contacts.py)
  geometrie             Geometrie | None
  cree_le, maj_le       datetime

Geometrie
  type                  Literal["point", "polygone"]
  points                tuple[Coordonnee, ...]
  superficie_ha         float | None     calculée, jamais saisie
  source                Literal["parcours_gps", "saisie_manuelle"]

Coordonnee
  latitude, longitude   float
  precision_m           float | None     telle que rendue par le navigateur
  horodatage            datetime | None

Capture
  identifiant, parcelle, compte
  modalite              Literal["photos", "video", "parcours", "parcours_video"]
  images                tuple[Image, ...]
  trace                 tuple[Coordonnee, ...]
  cree_le               datetime

Image
  empreinte_sha256      str        identité et déduplication
  largeur, hauteur      int
  coordonnee            Coordonnee | None
  recevabilite          Recevabilite

Recevabilite
  recevable             bool
  motif                 Literal["ok", "flou", "sous_expose", "sur_expose", "trop_petite"]
  conseil               str        message d'aide à la reprise, en français simple
  score_nettete         float
```

Toutes les structures sont `dataclass(frozen=True)`, conformément au contrat d'agent existant.

### 5.2 Persistance

`api/app/core/parcelles_store.py`, **sur le moule exact de `api/app/core/sessions.py`** :
`sqlite3` de la bibliothèque standard, fichier sur le volume `/data`, migrations par
`PRAGMA user_version`, accès asynchrone par `asyncio.to_thread`, écritures sérialisées par
verrou applicatif, mode WAL, **initialisation tolérante aux pannes** — si le fichier ne peut
être ouvert, l'API démarre quand même et les parcelles sont marquées indisponibles.

Les images ne vont pas en base : elles sont écrites sur `/data/captures/<empreinte>.jpg`, la
base ne stockant que l'empreinte et les métadonnées. Une purge par cron supprime les captures
non rattachées à un constat au-delà d'une rétention configurable.

### 5.3 Validation géographique

Une géométrie est rejetée si : un point sort des bornes de la Côte d'Ivoire (réutiliser les
bornes déjà présentes dans `api/app/services/geo.py` et `localites.py`) ; un polygone compte
moins de quatre points ; la superficie calculée sort de l'intervalle 0,1 à 50 ha ; l'anneau
s'auto-intersecte. La superficie se calcule sur coordonnées projetées localement, jamais en
degrés bruts.

---

## 6. Chantier C1 — socle parcelle & capture

**Prérequis de tout le reste.** Rien dans C2, ni le dossier de parcelle de C3, ne peut commencer
avant.

### 6.1 Quatre modalités, deux contrats serveur

L'apport clé : les quatre modalités demandées ne coûtent pas quatre implémentations. Le serveur
n'expose que **deux contrats** — un jeu d'images géoréférencées, une trace de points — et le
navigateur fait le reste.

| Modalité | Rôle du navigateur | Reçu par l'API |
|---|---|---|
| Photos | capture ou sélection | N images géoréférencées, horodatées |
| Vidéo | **échantillonne les images sur l'appareil** via `canvas`, 1 image / 2 s, plafond 12 | le contrat « images », identique |
| Parcours GPS | `watchPosition` pendant le tour de parcelle | le contrat « trace » |
| Parcours + vidéo | les deux simultanément, chaque image étiquetée par son point GPS | les deux contrats, corrélés |

L'échantillonnage côté navigateur n'est pas une optimisation, c'est une **contrainte de
terrain** : téléverser une vidéo de 100 Mo sur un réseau mobile ivoirien échouera. Douze images
suffisent au constat, et elles empruntent le canal base64 déjà utilisé par la console de
curation — ce qui évite la dépendance `python-multipart`, conformément au choix documenté dans
`api/app/curation/models.py`.

### 6.2 Étage 0 — recevabilité, sans modèle

**Aucun modèle d'apprentissage.** Variance du laplacien pour la netteté, luminance moyenne pour
l'exposition, seuil de résolution minimale.

*Correction du 28/07/2026, au moment d'écrire le plan C1 :* cet étage ne peut pas être
entièrement serveur. `numpy` ne décode pas le JPEG, et il n'y aura pas de dépendance Pillow.
La responsabilité se répartit donc ainsi — ce qui se révèle meilleur que la version initiale,
puisqu'une mauvaise image n'est même plus téléversée :

| Responsabilité | Où | Pourquoi |
|---|---|---|
| Netteté et exposition | **navigateur** | il possède déjà les pixels décodés dans `canvas` ; refuse avant téléversement, ce qui économise la bande passante sur réseau faible |
| Format, dimensions, taille | **serveur** (`api/app/services/vision/recevabilite.py`) | contrôle de sécurité sur un fichier écrit sur disque ; lecture d'en-tête PNG/JPEG en Python pur, sans dépendance |
| Plausibilité des métriques déclarées | **serveur** | le client peut mentir : on borne, on ne fait pas confiance aveuglément |

C'est le composant le moins spectaculaire et le plus rentable de la chaîne : sans lui, tout
l'aval analyse du bruit. Une image refusée renvoie un **conseil de reprise en français simple**
(« approchez-vous de la cabosse », « tournez-vous dos au soleil »), jamais un code d'erreur.

Deux règles de sécurité, puisqu'on accepte des fichiers d'utilisateurs : le nom de fichier
dérive de l'**empreinte SHA-256 du contenu**, jamais d'une donnée fournie par le client — aucune
traversée de chemin n'est possible, et la déduplication est gratuite ; un fichier dont l'en-tête
n'est pas reconnu n'est **jamais écrit sur disque**, tout en étant consigné en métadonnées avec
son motif de refus, pour que le producteur voie ce qui a été rejeté.

### 6.3 API

Nouveau routeur `api/app/routers/parcelles.py` :

```
POST   /v1/parcelles                     créer une parcelle
GET    /v1/parcelles                     lister celles du compte
GET    /v1/parcelles/{id}                détail, géométrie, historique
PUT    /v1/parcelles/{id}/geometrie      enregistrer une trace de parcours
POST   /v1/parcelles/{id}/captures       déposer des images (base64) et/ou une trace
GET    /v1/parcelles/{id}/captures/{cid} état d'une capture et sa recevabilité
```

**Aucune logique métier dans le routeur.** Tout passe par un nouveau service
`api/app/services/parcelles.py`. Validation Pydantic v2 en entrée **et** en sortie. Rate-limit
spécifique au dépôt d'images, plus strict que les 20 req/min du chat.

### 6.4 Interface

Dans `web/` : un écran « Ma parcelle » — création, carte du contour, bouton de parcours,
appareil photo, galerie des captures avec verdict de recevabilité. L'échantillonnage vidéo et
`watchPosition` vivent ici. Dégradation explicite si le navigateur refuse la géolocalisation :
saisie manuelle de la localité, jamais un blocage.

### 6.5 Critères d'acceptation C1

- Les quatre modalités aboutissent à une capture persistée, vérifié en prod sur téléphone réel.
- Une géométrie hors Côte d'Ivoire, auto-intersectée, ou de superficie absurde est refusée avec
  un motif lisible.
- Une image floue ou en contre-jour est refusée avec un conseil de reprise.
- Coupure de `/data` : l'API démarre, les parcelles sont indisponibles, le chat fonctionne.
- Couverture ≥ 97 % sur les modules nouveaux (seuil `--cov-fail-under=97` du `pyproject.toml`).

---

## 7. Chantier C2 — analyse visuelle en cascade

**Le chantier à risque.** Non par sa taille, mais parce qu'il dépend d'un modèle de vision
étranger au terrain ivoirien. Son chemin critique de démonstration est donc le **constat
descriptif**, ce qu'un VLM fait bien — et rien d'étiologique.

### 7.1 Pourquoi une cascade et pas un classifieur

La littérature sur les maladies du cacao procède presque toujours par « une photo, un CNN, un
nom de maladie ». C'est exactement ce qui échoue au champ : les jeux de données sont des
cabosses isolées sur fond neutre, et le modèle apprend le fond autant que la lésion. Une
cascade sépare les décisions, permet l'abstention à chaque étage, et rend chaque étage
testable seul.

### 7.2 Les étages

| Étage | Fonction | Moyen | Livré pour l'événement |
|---|---|---|---|
| 0 | recevabilité de l'image | heuristiques `numpy` | **oui** (C1) |
| 1 | tri de l'organe : cabosse / feuille / tronc / vue d'ensemble | VLM à l'événement, ViT affiné ensuite | **oui**, par VLM |
| 2 | localisation des lésions, sévérité en % de surface | détecteur affiné | non — post-événement |
| 3 | étiologie avec classe `indéterminé`, probabilités calibrées | classifieur affiné | non — sous verrou §7.5 |
| 4 | fusion contextuelle | orchestrateur + outils existants | **oui**, sur l'étage 1 |
| 5 | rédaction du constat | modèle 8B + LoRA | **oui** |
| 6 | boucle de revue humaine | console de curation | **oui** |

### 7.3 Étage 2 — pourquoi localiser plutôt que classer

Entourer les lésions apporte deux gains qu'une classification globale ne donne pas. Le
producteur **voit où le système regarde** — c'est ce qui crée la confiance et ce qui permet à un
agent ANADER de réviser en un coup d'œil. Et la **sévérité** devient quantifiable en pourcentage
de surface atteinte, qui est ce qui commande réellement la décision agronomique.

### 7.4 Étage 4 — la fusion contextuelle, l'avantage structurel

L'hypothèse visuelle est croisée avec ce que la plateforme sait déjà : pluviométrie des
dernières semaines via l'outil Open-Meteo existant, saison, localité, historique de la parcelle,
alertes de déforestation régionales. Une pourriture brune annoncée après trois semaines sèches
est dégradée d'office.

**Aucun classifieur publié sur le cacao ne fait cela** — parce qu'aucun n'a d'agents météo et
de RAG derrière lui. C'est le seul point où la plateforme agentique produit une capacité qu'un
modèle de vision seul ne peut pas atteindre, et c'est donc l'argument technique central de la
présentation sur ce volet.

### 7.5 Le verrou du pré-diagnostic

Conformément à D3, l'étage 3 ne s'active pas à une date. Il s'active quand, sur un jeu de test
**de terrain** (photos de téléphones réels, arrière-plans réels, contre-jour réel) :

- le **rappel par classe** dépasse un seuil convenu avec l'ANADER pour la pourriture brune et
  le swollen shoot — les deux dont un manqué coûte une récolte. **Valeur proposée par défaut,
  à confirmer ou relever par l'ANADER : 0,90 de rappel sur ces deux classes.** Tant qu'aucun
  seuil n'est arrêté, c'est cette valeur qui s'applique, et l'étage 3 reste inactif ;
- l'**abstention** est calibrée de façon à préférer la fausse alerte au manqué ;
- la **matrice de confusion** est publiée, avec ses intervalles de confiance.

On publie le rappel par classe, pas une exactitude globale — qui ne veut rien dire sur des
classes déséquilibrées. Tant que le verrou n'est pas franchi, le système décrit et signale sans
nommer.

### 7.6 Étage 6 — la boucle humaine, et le jeu de données ivoirien

Chaque constat part dans une file de revue. Un agent ANADER confirme ou corrige ; l'étiquette
corrigée alimente le jeu d'entraînement. Le système s'améliore **parce qu'il est utilisé**, et
la précision annoncée devient mesurable puis publiable.

La console `api/app/curation/` fournit le moule : file de travail, revue, `store.py`,
`jobs.py`. On l'étend plutôt que de créer une seconde console.

Les jeux de données publics (collections Mendeley et Kaggle sur la pourriture brune) servent
d'amorce, pas de vérité : ils sont biaisés studio. Le jeu ivoirien se construit par cette
boucle — ce qui est aussi la raison pour laquelle la fenêtre GPU d'un mois (§4.3) a une valeur
au-delà du jour J.

### 7.7 Service de vision

Nouveau conteneur `vision/`, **jamais exposé publiquement**, consommé en interne par l'API comme
l'est déjà `inference/`. Il sert le VLM en profil GPU, et les petits spécialistes dans les deux
profils. Port mockable `api/app/services/vision/port.py` : aucun appel réseau en test.

En profil CPU, le VLM est absent et l'API le dit — message explicite, pas une erreur, pas une
description inventée. Le pattern « contexte vide → fabrication » a déjà été corrigé une fois
sur les agents (v0.6.48) ; il ne doit pas revenir par la vision.

### 7.8 Critères d'acceptation C2

- Une photo de cabosse produit un constat descriptif, sans nom de maladie, sans produit, sans
  dosage — vérifié par test sur les termes interdits.
- En profil CPU, une demande de description renvoie une indisponibilité explicite.
- Une contradiction météo dégrade la confiance du constat (test de l'étage 4).
- Chaque constat apparaît dans la file de revue et une correction est persistée.
- Aucun test n'appelle le réseau ; aucun test ne contient de dosage phytosanitaire.

---

## 8. Chantier C3 — atelier de livrables

### 8.1 Le moteur

`api/app/application/redaction.py` — orchestration pure, testable sans réseau, sur le modèle des
modules `application/` existants. Trois temps :

1. **Planifier** — le gabarit fournit un plan de sections ; chaque section déclare ses sources
   (requête RAG, outil prix, outil météo, alertes GFW, parcelle, baromètre des préoccupations).
2. **Rédiger** — section par section, chacune avec son contexte propre. Une section dont les
   sources sont vides produit un **constat de lacune** (D4), jamais une estimation.
3. **Assembler** — sections, tableaux, figures, bibliographie et manifeste de génération dans un
   objet `Document` unique.

### 8.2 Ce que le corpus permet — et ce qu'il ne permet pas

*Analyse du corpus menée le 28/07/2026, sur question de Waopron.* `corpus/corpus_cacao_rag.jsonl`
compte 10 000 paires. Réponses : **médiane 583 caractères, maximum 1 201**. Marqueurs de
structure : **0,0 % de titres markdown, 0,0 % de puces, 0,0 % de listes numérotées, 0,0 % de
tableaux** — zéro sur les quatre.

**Conséquence favorable, et elle valide l'architecture.** La LoRA sait produire un paragraphe de
prose française de 600 à 800 caractères. C'est exactement la granularité d'une section. La
structure — titres, tableaux, numérotation, annexe de provenance — est produite par le **gabarit
YAML et les adaptateurs de rendu** ; le modèle n'émet jamais un titre. Un 8B qui n'a jamais lu de
document de 30 000 caractères ne peut pas en écrire un d'un seul jet ; il peut écrire quarante
paragraphes de 700 caractères, ce qui est le même document. Le découpage par section n'est donc
pas seulement une parade au time-out Cloudflare : **c'est ce qui rend l'étude possible.**

**Manque réel, à traiter.** Le corpus est intégralement en registre *conseil au producteur*
(« rendez-vous auprès de l'agent ANADER de votre zone »). Sollicitée sur une section d'étude, la
LoRA s'adressera au producteur et renverra vers l'ANADER — ce qui est faux dans un document
destiné à un bailleur. Traitement en deux temps :

1. **Pour l'événement — une consigne de registre.** Le mécanisme existe : `prompts.py::build_messages(consigne=...)`,
   construit pour le dialogue naturel. Consigne « rédaction analytique, troisième personne, pas
   d'adresse au lecteur, pas de renvoi ANADER ». La leçon acquise en juillet s'applique : *le
   levier est la consigne, pas le plafond*.
2. **Après l'événement — 200 à 400 exemples de prose analytique** ajoutés au corpus, puis un
   rafraîchissement de LoRA (une à deux heures sur le GEX44 déjà loué). **Sans changement de
   socle** : c'est un enrichissement, pas la migration C5, et il ne remet rien en cause.

### 8.3 Gabarits déclaratifs

`api/app/data/gabarits/*.yaml`, sur le modèle de `sources_agro.yaml` et
`sources_officielles.yaml` déjà en place. Trois gabarits :

| Gabarit | Entrée | Sortie | Public visé |
|---|---|---|---|
| `etude_filiere` | un sujet borné cacao | 5-15 pages : plan, sections sourcées, tableaux de données réelles, bibliographie, manifeste | institutions, bailleurs, chercheurs |
| `dossier_parcelle` | une parcelle de C1 | géométrie et superficie, constat satellite daté, chaîne de traçabilité, constats visuels, pièces | coopératives, exportateurs |
| `bulletin_regional` | une DR, périodique | 1 page : météo, prix, alertes de la zone | producteurs, ANADER |

Ajouter un gabarit doit être un fichier YAML, pas du code. C'est la même discipline
d'extensibilité que « ajouter un agent = un adaptateur ».

### 8.4 Traçabilité — au centre, pas en annexe

Deux traçabilités distinctes, et la seconde est celle que personne d'autre ne produit.

**La chaîne d'approvisionnement.** parcelle (polygone GPS) → producteur → coopérative et
section → lot → livraison → exportateur, avec le constat satellite daté de non-déforestation
postérieure au 31/12/2020. C'est le cœur de l'exigence EUDR. Tout maillon absent est **déclaré
absent** dans le dossier, avec ce qu'il faudrait fournir pour le compléter.

**Le manifeste de génération.** Joint à chaque livrable : modèle et version, version applicative,
documents RAG mobilisés avec leur empreinte, outils appelés avec leur horodatage, profil
matériel, compte demandeur. Le rapport devient **rejouable**. C'est ce qui le rend défendable
devant un auditeur, et c'est la souveraineté rendue vérifiable : *ce document dit d'où vient
chacun de ses chiffres, et vous pouvez le refaire.*

Nouveau module `api/app/application/provenance.py`. Chaque `Affirmation` porte source, date,
méthode et confiance. Un tableau de provenance figure en annexe de tout livrable, et en feuille
dédiée dans l'export Excel.

Rappel D5 : le dossier de parcelle porte en tête, de façon non contournable, la mention qu'il
s'agit d'un **dossier préparatoire** et non d'une déclaration de conformité.

### 8.5 Quatre formats, un seul moteur

Le moteur produit un `Document` ; les formats sont des **adaptateurs** dans
`api/app/services/rendu/` qui ne remontent jamais dans le moteur.

| Format | Bibliothèque | Usage |
|---|---|---|
| Markdown | aucune | affichage web, streaming en direct |
| Word | `python-docx` | dossier de parcelle, étude de filière |
| Excel | `openpyxl` | annexes de données, tableau de provenance |
| PowerPoint | `python-pptx` | restitution institutionnelle |

Toutes sont pures Python, sans dépendance système. `CLAUDE.md` exige la validation de Waopron
pour toute dépendance hors spec §2.1 : **accordée le 28/07/2026 par la demande explicite des
formats Word, PPTX et Excel** — à reporter dans `CLAUDE_OpenCacao.md` §2.1. Précédent : `pypdf`
et `maxminddb` ont été ajoutés selon la même règle, avec justification en commentaire dans
`api/pyproject.toml`. Faire de même.

Le rendu Word réutilise les conventions typographiques déjà écrites dans
`scripts/build_doc_agentique.py` plutôt que d'en inventer d'autres.

### 8.6 Jobs asynchrones

Une étude représente 10 à 30 générations : le synchrone est exclu, et le time-out edge
Cloudflare (~100 s) l'interdit de toute façon — la leçon des 524 de juin est acquise.

`api/app/core/rapports_store.py` (SQLite, moule `sessions.py`) et un routeur
`api/app/routers/rapports.py` :

```
POST   /v1/rapports              créer un job, renvoyer son identifiant
GET    /v1/rapports              lister les jobs du compte
GET    /v1/rapports/{id}         état, progression par section, contenu partiel
GET    /v1/rapports/{id}/stream  SSE — la rédaction section par section, en direct
GET    /v1/rapports/{id}/export  ?format=docx|xlsx|pptx|md
```

Le flux SSE émet un premier octet immédiatement, puis un événement par section. Contrainte
directement héritée de l'incident 524 : **une réponse longue sur CPU doit streamer un premier
octet vite.** Le front ignore les types d'événements inconnus.

En profil CPU, les études basculent en file nocturne par cron, avec notification par email
via `api/app/services/notifier.py` (ZeptoMail, expéditeur `waopron@` — `noreply@` renvoie 403).

### 8.7 Le moment de scène

Faire générer par le système, en direct, le **PPTX d'une étude de filière** — la présentation
que l'assemblée est en train de regarder. Le flux SSE écrit les sections à l'écran, puis le
fichier se télécharge. Ce moment doit figurer dans le scénario répété (§9.4) et être testé en
prod avant le jour J, pas improvisé.

### 8.8 Critères d'acceptation C3

- Une étude en Word, Excel et PPTX, chacune contenant son manifeste de génération.
- Une section privée de sources rend un constat de lacune ; aucun chiffre sans provenance —
  vérifié par un test qui échoue si une affirmation sort sans source.
- Un dossier de parcelle porte la mention « dossier préparatoire » et ne conclut à aucune
  conformité.
- Le flux SSE émet son premier octet en moins d'une seconde, mesuré en prod.
- Un job survit à un redémarrage de l'API (état persisté, reprise ou échec propre).

---

## 9. Chantier C4 — durcissement et jour J

Transverse, exécuté en dernier, **préparé dès le début**.

### 9.1 Bascule GPU

Manifeste `deploy/k8s/inference-gpu.yaml` servant le 8B sous vLLM, et
`deploy/k8s/vision.yaml` pour le VLM. Bascule par `profil_materiel` et `inference_url` en
ConfigMap, déployée par `deploy/scripts/roll-image.sh` — le chemin fiable connu, ArgoCD
présentant un défaut de synchronisation sur K8s 1.35. **Repli CPU testé et chronométré avant
le jour J**, pas découvert le jour même.

### 9.2 File d'attente à position visible

Le pire scénario n'est pas la lenteur, c'est la lenteur muette. Quand la charge dépasse la
capacité, l'utilisateur reçoit sa **position dans la file** et une estimation, servies
immédiatement sur le flux SSE. Une attente annoncée est tolérée ; une page blanche de quarante
secondes est un échec public.

### 9.3 Pré-chauffage du cache

`scripts/prewarm_cache.py` existe. L'étendre au scénario répété : chaque question de la démo
est en cache avant l'entrée en scène. Le cache exact et le cache sémantique sont tous deux
alimentés. Attention au piège déjà rencontré : la clé de cache inclut `app_version`, donc un
déploiement postérieur au pré-chauffage **invalide tout** — le pré-chauffage est la dernière
opération avant la présentation.

### 9.4 Scénario de démonstration répété

Document `docs/demo/scenario.md` : le déroulé minute par minute, les questions exactes, les
réponses attendues, l'ordre des agents montrés, la parcelle de démonstration préparée avec ses
photos, et le point de bascule vers le plan de secours. Répété **en production**, pas en local.

### 9.5 Runbook jour J

Document `docs/demo/runbook.md` : bascule GPU et retour, seuils d'alerte, surveillance en
direct, qui fait quoi en cas de panne, procédure de retour arrière, et le plan de secours
hors-ligne — captures et enregistrements préparés, à dégainer sans hésitation. Le watchdog et
les alertes email existants (`cron-watchdog.yaml`, ZeptoMail) sont réglés sur des seuils
d'événement.

### 9.6 Critères d'acceptation C4

- Bascule GPU puis retour CPU exécutés au moins deux fois, chronométrés, documentés.
- Le scénario complet joué en production sans intervention, deux fois de suite.
- Sous charge simulée, la file annonce une position et aucune requête ne meurt en silence.
- Le plan de secours est utilisable par quelqu'un d'autre que vous.

---

## 10. Chantier C5 — migration Luciole, après l'événement

Migration **mesurée**, jamais sur la foi d'un argumentaire.

1. **Banc d'essai à prompts identiques** — les questions de la recette V2 existante, sur les
   deux socles : prefill, décodage, tok/s, RAM, qualité agronomique évaluée sur le jeu de test
   en place.
2. **Vérifier le cache de prompt.** Une architecture Mamba hybride ne réutilise pas son état
   comme un transformeur pur. `cache_prompt` est l'un des trois leviers de latence acquis en
   juillet : si llama.cpp ne le sert pas de la même façon sur un hybride, le gain se retourne.
   **À mesurer avant toute décision** — c'est le risque principal de C5.
3. **Le pari inverse** — les couches récurrentes ont un état constant par token au lieu d'un
   cache KV croissant. Sur des prompts RAG longs et en CPU, l'hybride peut être *plus rapide*.
   Hypothèse à tester, pas à supposer.
4. **Réentraîner la LoRA cacao** sur le nouveau socle, revalider les garde-fous, refaire la
   recette.
5. **Brancher `Luciole-1B` en brouillon spéculatif** et mesurer le gain réel contre les 1,7-1,9×
   attendus.
6. Décision sur chiffres. `docs/superpowers/specs/2026-07-24-brouillon-speculatif-souverain-design.md`
   devient caduque si le gain est atteint par la paire officielle.

---

## 11. Garde-fous — ce qui change, ce qui ne change pas

**Ne change pas.** Périmètre cacao uniquement ; refus des dosages phytosanitaires ; refus du
médical et du vétérinaire ; redirection ANADER systématique ; disclaimer porté par l'entité
`Conseil` ; garde-fous **dans l'orchestrateur**, jamais par agent ; aucun service externe d'IA ;
aucun dosage dans les tests, même en exemple.

**Change, par arbitrage daté du 28/07/2026.** La catégorie de refus `DIAGNOSTIC_IMAGE`
(`api/app/models/domain.py`) ne refuse plus toute analyse d'image : elle refuse le **diagnostic
autonome**. Une image reçoit un constat descriptif, une confiance, une orientation ANADER —
jamais un nom de maladie tant que le verrou §7.5 n'est pas franchi, jamais un produit, jamais un
dosage. Le test par règle de refus reste obligatoire, et un test supplémentaire vérifie qu'un
constat visuel ne contient ni nom de produit ni posologie.

**S'ajoute.** D4 (chaque chiffre porte sa source) et D5 (jamais une déclaration de conformité)
deviennent des garde-fous testés, appliqués au moteur de rédaction comme aux agents.

À reporter dans `CLAUDE.md` et dans `CLAUDE_OpenCacao.md`.

---

## 12. Hors périmètre de cette V3

Écarté délibérément, pour tenir l'échéance :

- **SMS et USSD.** Atteindre les producteurs sans smartphone suppose une passerelle SMS —
  service externe, coût récurrent, tension avec D1. Vrai sujet, autre chantier.
- **Voix en langues locales.** Baoulé, dioula, bété : pas de modèle souverain hors ligne
  disponible à ce jour. À rouvrir quand l'écosystème mûrit.
- **Alertes proactives poussées** (fenêtre sans pluie, variation de prix, alerte de
  déforestation à proximité). Le socle parcelle de C1 les rend possibles ; elles n'ont aucune
  valeur de démonstration dans un créneau de vingt minutes. Backlog, juste après l'événement.
- **Observatoire public des préoccupations.** Réduit à une **source de données** pour les études,
  avec volume affiché honnêtement. Un tableau de bord désert devant 800 personnes ferait plus de
  mal que de bien.
- **Agents A7 maladie autonome, A11 ERP, A12 AgroSense.** Restent au backlog documenté.

---

## 13. Livrables documentaires

| Livrable | Destination |
|---|---|
| Cette spec maîtresse | `docs/superpowers/specs/2026-07-28-v3-operationnelle-design.md` |
| Plans d'implémentation C1 à C4 | `docs/superpowers/plans/2026-07-28-c{1..4}-*.md` |
| Dossier de référence V3, Word et PDF | `docs/OpenCacao_V3_Dossier.docx`, régénérable par `scripts/build_doc_v3.py` |
| Scénario de démonstration | `docs/demo/scenario.md` |
| Runbook jour J | `docs/demo/runbook.md` |
| Mise à jour du cours agentique | `docs/agents_v3.md` — sections parcelle, vision, livrables |

Le dossier de référence suit le moule de `scripts/build_doc_agentique.py` : généré par script,
jamais édité à la main, régénérable après chaque évolution.

---

## 14. Ordonnancement et évaluation du risque

```
C1 socle parcelle & capture   ████████░░░░░░░░░░░░  prérequis, aucun risque externe
C2 analyse visuelle           ░░░░████████░░░░░░░░  RISQUE — dépend d'un VLM hors terrain
C3 atelier de livrables       ░░░░██████████░░░░░░  tient, dépendances pures Python
C4 durcissement & jour J      ░░░░░░░░░░░░████████  transverse, préparé tôt
C5 migration Luciole          ░░░░░░░░░░░░░░░░░░░░  après l'événement
```

**Évaluation franche du calendrier.** C1, C3 et C4 tiennent en 2 à 4 semaines parce que le socle
agentique, le RAG, les outils météo, prix et satellite, la notification email et l'outillage
DOCX existent déjà. **C2 est le seul chantier à risque calendaire** : son chemin critique de
démonstration est le constat descriptif, et si le VLM se révèle inutilisable sur photos
ivoiriennes, on montre C1, C3 et C4 sans lui — la présentation tient debout. Cette porte de
sortie doit rester ouverte jusqu'au bout, et la décision de l'emprunter être prise **une semaine
avant**, pas la veille.

Il n'y a aucune marge pour un cinquième chantier. Toute demande nouvelle déplace quelque chose.
