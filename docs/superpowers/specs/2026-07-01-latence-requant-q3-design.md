# Latence — re-quantifier le modèle affiné en Q3 (+ leviers mineurs)

**Date** : 2026-07-01
**Auteur** : session OpenCacao
**Statut** : conçu, à implémenter (sprint ops/empirique, gate A/B)

## Problème

Après le sprint « tokens d'entrée » (v0.6.46, `cache_prompt` + prompt condensé + cap
RAG), la latence des réponses générées est ~**15 s** une fois le KV chaud. Le tuning
RAG (top_k/cap) est en **rendement décroissant** : à (2,320) la latence est identique
à (3,480). **Le plancher ~15 s est celui du modèle sur CPU** (Ministral-8B Q4_K_M,
~8 tok/s, borné par la bande passante mémoire). Il n'y a plus de bouton de config.

Le seul levier restant **sans nouveau modèle, sans fine-tune, sans GPU** est de
**re-quantifier plus fort NOTRE modèle affiné** : sur CPU, la vitesse de génération
est ~inversement proportionnelle à la taille du modèle en octets. Passer de Q4_K_M
(4,9 Go) à Q3_K_M (~3,7 Go) ≈ −24 % d'octets → ~15-20 % plus rapide.

## Contrainte d'identité (non négociable)

La thèse du livre blanc (§1.1 de la spec) est qu'une équipe ivoirienne **entraîne**
son modèle souverain. Le modèle affiné est la démonstration, pas un détail. Ce sprint
**préserve le pivot** : on re-quantifie le MÊME Ministral-8B affiné par LoRA — c'est
toujours notre modèle entraîné, juste plus compressé. **Aucun fine-tune, aucun modèle
tiers.** (Écarté : migration MoE et re-fine-tune — infra/coût, décidés hors sprint.)

## Objectif

~10-20 % de latence en plus, modèle affiné conservé, **validé A/B** (qualité ≈ Q4).
Aucun déploiement sans que le gate qualité passe. Q4 conservé comme rollback instantané.

## Leviers (3, indépendants)

### Levier 1 — Re-quant Q4_K_M → Q3_K_M (headline, gaté qualité)
Produire `opencacao-8b-Q3_K_M.gguf` et le servir à la place du Q4.

- **Production, voie rapide** : Job K8s ponctuel avec l'image `ghcr.io/ggml-org/llama.cpp:full`
  (contient `llama-quantize`), montant le hostPath `/opt/opencacao/models`, exécutant
  `llama-quantize /models/opencacao-8b-Q4_K_M.gguf /models/opencacao-8b-Q3_K_M.gguf Q3_K_M`.
  Sortie ~3,7 Go (tient dans les ~9 Go libres pendant le job ; le Q4 source reste).
- **Voie propre (escalade, SI la qualité rapide échoue)** : re-merger la LoRA adapter
  (89 Mo, conservée) + base Ministral-8B en f16 sur une box scratch, puis quantifier
  f16 → Q3_K_M (bien moins lossy que Q4→Q3). On ne paie l'escalade que si nécessaire.

### Levier 2 — KV cache `q8_0 → q4_0` (`deploy/k8s/inference.yaml`)
`--cache-type-k q4_0 --cache-type-v q4_0` : cache KV plus petit → moins de bande
passante mémoire → ~5 %. Effet qualité léger (validé dans l'A/B avec le Q3).

### Levier 3 — RAG cap `480 → 400` (ConfigMap `RAG_PASSAGE_MAX_CHARS`)
Petit gain de préremplissage (~2-3 %), réglable à chaud, risque recall faible.

## A/B validation (le gate bloquant)

Sur un **pod de test isolé** (pas la prod), servir le Q3 (avec KV q4) en parallèle du
Q4 (KV q8) et comparer sur le MÊME jeu de ~8 questions cacao couvrant : agronomie/RAG,
prix (doit dire 1200), météo (localité), contact ANADER, multi-tours, hors-filière
(refus), taille, ombrage.

Pour chaque question, relever :
- **Latence** : tok/s (`llama-bench` pour prefill+génération) et/ou temps E2E ;
- **Qualité** : faits corrects (pas de dérive vs Q4), ton producteur, garde-fous
  respectés (refus hors-filière, prix officiel 1200, pas de dosage), disclaimer.

**Gate de déploiement** : on ne bascule la prod sur le Q3 QUE si — (a) qualité **≈ Q4**
(aucune régression factuelle ni de garde-fou sur le jeu de test) ET (b) latence **≥
~10 %** meilleure. Sinon : escalade f16→Q3, ou abandon du levier 1 (on garde Q4 + les
leviers 2-3 seuls s'ils aident).

## Déploiement & rollback

Si le gate passe :
1. `deploy/k8s/inference.yaml` : pointer `-m` sur `/models/opencacao-8b-Q3_K_M.gguf`,
   appliquer les args KV q4.
2. Bump `MODEL_VERSION` (ConfigMap) — le Q3 est un artefact modèle distinct, tracé.
3. Appliquer le manifeste inférence (`kubectl apply -f deploy/k8s/inference.yaml`) +
   `roll-image.sh <tag>` pour l'API (invalide le cache), `RAG_PASSAGE_MAX_CHARS=400`.
4. Vérifier prod (santé, prix 1200, latence).

**Rollback instantané** : le `opencacao-8b-Q4_K_M.gguf` **reste sur le nœud** →
repointer `inference.yaml` sur le Q4 + réappliquer. (Idem KV q8, cap 480.)

## Nature du sprint & tests

Sprint **ops + empirique** : le juge est l'A/B qualité/latence, pas des tests unitaires.
Le code touché est minimal (manifeste inférence, ConfigMap) — pas de logique métier
nouvelle. Aucune régression de la suite `pytest` attendue (rien dans `api/app/` ne
change) ; on la relance quand même (couverture ≥ 97 %) par sécurité.

## Hors périmètre

- Migration MoE 30B / GPU (infra/coût, décision Waopron).
- Re-fine-tune du modèle (GPU, épopée séparée).
- i-quants (IQ3) — à considérer seulement si Q3_K_M déçoit sur le compromis.
