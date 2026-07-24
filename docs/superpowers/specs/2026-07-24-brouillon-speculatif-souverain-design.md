# Brouillon spéculatif souverain — accélérer l'inférence sans rien emprunter

**Date** : 2026-07-24
**Statut** : design (chiffrage validé oralement), à instruire avant lancement RunPod

## Objectif

Réduire la latence de **décodage** de l'inférence (goulot du conversationnel : ~15 tok/s,
borné bande passante mémoire sur la VM CPU sans GPU) **sans dégrader la qualité** du modèle
souverain et **sans aucune dépendance externe**. Levier : le **décodage spéculatif** de
llama.cpp (déjà supporté par le binaire de prod), avec un **brouillon auto-distillé depuis
notre propre OpenCacao-8B**.

## Pourquoi c'est la voie souveraine

- **Aucun maître tiers** : le brouillon apprend à imiter *notre* 8B (pas Qwen, pas d'API).
  Le teacher EST le modèle souverain. (Distinct de F2, qui utilise Qwen2.5-72B auto-hébergé
  pour améliorer le corpus — ici on ne cherche pas à *dépasser* le 8B mais à le *prédire*.)
- **Données 100 % locales** : corpus existant (10k) + textes cacao générés par le 8B lui-même.
- **Chaîne locale** : entraînement sur GPU loué (calcul, pas une API — conforme CLAUDE §1.3/§13),
  service `-md` local, aucun appel sortant.
- **Qualité préservée par construction** : en spéculatif, le 8B **vérifie chaque token** ; le
  brouillon ne fait que *proposer*. La sortie est **bit-à-bit celle du 8B** (à température 0),
  quelle que soit la qualité du brouillon. Le brouillon n'affecte QUE la vitesse.

## Contrainte dure établie (mesurée)

Le tokenizer est **Tekken (~131k tokens)** — vérifié en prod (id de token jusqu'à 80120 sur
3 mots). Un brouillon doit partager **exactement** ce vocabulaire pour que les id coïncident.
Il n'existe pas de petit modèle Tekken **ouvert** prêt à l'emploi (Ministral-3B est API-only).
D'où : on **fabrique** le brouillon à partir du 8B, ce qui garantit le vocab ET une acceptation
élevée (le brouillon « pense » comme le 8B).

> Leçon du test raté : TinyLlama-1.1B (vocab Llama 32k) a chargé comme brouillon mais donné
> **+0 %** — les vocabs ne coïncident pas (32k ≠ 131k), acceptation ~0. Le spéculatif n'est
> donc ni prouvé ni infirmé sur cette infra ; il attend le bon brouillon.

## Méthode retenue : élagage en profondeur + distillation (self-distillation)

1. **Élagage (depth pruning)** — partir de `Ministral-3-8B-Instruct-2512-BF16` (~36 couches
   transformer) et **retirer ~la moitié des couches** les moins utiles. Sélection par métrique
   d'importance (distance angulaire / cosinus des états cachés entre entrée et sortie de chaque
   bloc, sur un échantillon du corpus). Résultat : un **~4B** de même vocab Tekken, même
   architecture `mistral3`.
2. **Cicatrisation par distillation (self-distillation)** — ré-entraîner le brouillon élagué
   pour **imiter le 8B** :
   - Données : les 10k paires + ~50-100k tokens de sorties cacao **générées par le 8B**
     (réutiliser `pod_generate.sh` / la logique F2, mais teacher = notre 8B, pas Qwen).
   - Perte : distillation (KL sur les logits du 8B) + CE sur les tokens. LoRA 4-bit possible
     (comme `train_lora.py`) ou fine-tune complet du petit modèle si le GPU tient.
   - 1-3 epochs. Objectif : **maximiser l'accord token-à-token avec le 8B**, pas la qualité
     absolue.
3. **Export GGUF** du brouillon (Q4_K_M) via `pod_gguf.sh` → `draft-opencacao-4b-q4.gguf`.

**Alternative (repli)** : transplant de tokenizer sur un 1B efficace (remplacer embeddings+tête
par le vocab Tekken 131k puis distiller). Plus léger à servir mais plus lourd à entraîner
(table 131k dominante sur un 1B) et acceptation moins garantie. À n'envisager que si le 4B
élagué est jugé trop lourd comme brouillon.

## Taille du brouillon — décision ouverte

| Taille brouillon | Décodage brouillon | Acceptation attendue | Gain spéculatif net |
|---|---|---|---|
| **~4B (élagage ½)** — recommandé pour démarrer | ~2× le 8B | **haute (~70-85 %)** | **~1,6-1,9×** |
| ~2-3B (élagage plus agressif) | ~2,5-3× le 8B | moyenne | incertain (peut être < 4B) |

Démarrer à **~4B** (acceptation sûre), mesurer, puis tenter plus petit si l'acceptation est
haute. Le gain net dépend du produit (coût brouillon × nombre de tokens acceptés d'affilée).

## Intégration (zéro changement applicatif)

Sur le déploiement `inference` (`deploy/k8s/inference.yaml`) :
```
-md /models/draft-opencacao-4b-q4.gguf --spec-draft-n-max 16 --spec-draft-n-min 1
```
- Déposer le GGUF brouillon sur le nœud (`/opt/opencacao/models/`, +~2,5 Go ; 15 Go libres).
- Remonter la limite mémoire du pod `inference` (12Gi → ~14Gi) pour le brouillon + ses buffers.
- Le brouillon partage le tokenizer et le KV : rien à changer dans `api/`.
- Réglages latence : `--spec-draft-n-max` (nb de tokens spéculés, défaut 3 → 8-16 sur CPU),
  éventuellement épingler quelques threads au brouillon (`--threads-draft`).

## Validation (avant bascule prod)

1. Banc fantôme (pod `inference-bench` sur port dédié, GGUF partagé en mmap) — comme le test
   du 24/07.
2. Mesurer sur prompts cacao FR : **taux d'acceptation** + **tok/s bout-en-bout** vs 8B nu.
3. Vérifier l'**identité des sorties** à température 0 (le spéculatif ne doit rien changer).
4. Balayer `--spec-draft-n-max` (4 / 8 / 16) pour le point optimal.
5. Ne basculer la prod que si gain net ≥ ~1,4× sans régression de sortie.

## Chiffrage

| Poste | Estimation |
|---|---|
| Génération des données de distillation (8B, self) | ~2-4 GPU-h (ou CPU sur le pod, lent) |
| Élagage + distillation du ~4B (24 Go, LoRA 4-bit, 1-3 epochs) | ~5-15 GPU-h |
| Export GGUF + validation banc | ~1-2 h |
| **Coût GPU RunPod** (~0,5-0,8 $/h) | **~10-40 $** |
| **Effort ingénierie** (recette, itérations) | **~1-3 jours** |

## Gain attendu (mesurable, non garanti)

Acceptation ~70 % → décodage **15 → ~25-28 tok/s (1,7-1,9×)**, **qualité 8B strictement
inchangée**. Réponse 200 tokens : ~13 s → ~7 s. Clarification 40 tokens : ~5 s → ~3 s.
Bénéficie à **toutes** les générations (conversationnel ET réponses), sans GPU en prod.

## Risques et mitigations

| Risque | Mitigation |
|---|---|
| Acceptation trop basse (français, brouillon petit) → gain faible | Démarrer à 4B (proche du 8B) ; distiller sur données on-domain ; mesurer avant bascule. |
| Brouillon consomme RAM + CPU (partage les cœurs) | +2,5 Go RAM (15 libres) ; régler `--threads-draft` ; le brouillon ne tourne que pendant la génération. |
| Élagage dégrade trop l'accord | Sélection de couches par importance + cicatrisation par distillation KL ; repli sur élagage moins agressif. |
| Charge concurrente fait chuter le débit | Orthogonal au spéculatif ; déjà connu (n_parallel). Le spéculatif garde son ratio. |

## Hors scope

- Pas de maître tiers ni d'API (souveraineté).
- Pas de changement de l'architecture applicative (`api/`) — intégration au seul niveau du
  serveur d'inférence.
- Pas de remplacement du 8B : le spéculatif le **conserve** comme modèle de vérité.

## Leviers écartés (mesurés le 24/07, pour mémoire)

- Threads `-t 16` vs `-t 12` : **−8 %** (bande passante saturée avant 12 cœurs).
- KV cache q4_0 vs q8_0 : **−1 %** (KV négligeable face aux poids).
- NUMA : 1 seul nœud (VM). AVX-512/VNNI : absents (AVX2 + REPACK seulement).
- Alternative bon marché à ce plan (compromis qualité) : **requant IQ4_XS/Q3** du 8B
  (~+15-40 % décodage), à requantifier depuis le f16 fusionné — souverain aussi, mais dégrade
  la qualité, contrairement au brouillon spéculatif.
