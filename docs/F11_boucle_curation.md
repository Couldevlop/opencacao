# F11 — Boucle d'amélioration continue (journal → corpus curé → ré-entraînement)

La V1/V2 journalise déjà, de façon **anonymisée**, chaque interaction et chaque retour
👍/👎 (`api/app/core/journal.py` → `interactions.jsonl`, `feedback.jsonl` sur le PVC
`/data`). F11 ferme la boucle : un **maître GLM-5.2** (hors production) relit ce journal,
**réécrit** les réponses insatisfaisantes (ou les transforme en refus conformes), et
alimente le **corpus curé** (`corpus/corpus_cure.jsonl`) déjà consommé par
l'entraînement (`assemble_corpus.py` → `train_lora.py`).

> Souveraineté (CLAUDE §1.3, §13) : le maître n'intervient qu'**offline**, comme
> l'enrichissement du corpus et l'évaluation F1 — **jamais** dans le service. Le journal
> est anonymisé (aucune IP, aucune donnée personnelle).

## Ce que fait `curate_journal.py`
1. Joint `feedback.jsonl` (👍/👎, dernier vote retenu) à `interactions.jsonl`.
2. Déduplique par question (un 👎 prime sur un 👍).
3. Pour chaque cas, le maître renvoie `{action, instruction, output}` :
   - **corriger** : meilleure réponse (question légitime) ;
   - **refus** : redirection ANADER (dosage, médical, image, hors-filière) ;
   - **garder** : 👍 déjà correct et conforme ;
   - **rejeter** : inexploitable.
4. **Valide** chaque paire (mêmes règles que le corpus : champs, longueurs, **aucun
   dosage chiffré**, **source citée**) et l'**ajoute sans doublon** à
   `corpus/corpus_cure.jsonl`. Aucun rejet n'est silencieux (statistiques en sortie).

Règle métier intégrée au prompt : pour les questions de **zones de culture**, le maître
nomme les vraies régions cacaoyères (sud forestier), ne présente **jamais** une localité
de savane (ex. Katiola) comme propice, **demande la ville** du producteur et **propose le
contact ANADER** le plus proche (les coordonnées exactes sont rattachées par le service).

## Étape 1 — Rapatrier le journal (depuis la prod)
```sh
export KUBECONFIG=kubeconfig-hetzner.yaml
kubectl -n opencacao cp <pod-api>:/data/interactions.jsonl ./journal/interactions.jsonl
kubectl -n opencacao cp <pod-api>:/data/feedback.jsonl     ./journal/feedback.jsonl
```

## Étape 2 — Curer (maître GLM-5.2, offline)
```sh
export ZAI_API_KEY=...            # ou CORPUS_LLM_API_KEY (endpoint OpenAI-compatible)
python training/scripts/curate_journal.py --journal ./journal \
    --sortie corpus/corpus_cure.jsonl
# n'autocorriger que les 👎 : --votes down ; plafonner : --max 200
```
La sortie résume : paires ajoutées, rejets, invalides, doublons, et les motifs.

## Étape 3 — Ré-entraîner (périodique, sur le pod GPU)
Le corpus curé enrichi est repris tel quel par la chaîne d'entraînement (il fait déjà
partie des sources de `pod_train.sh` / `pod_f2_distillation.sh` / `pod_f4_sweep.sh`) :
```sh
# sur le pod : assemble + LoRA + fusion + GGUF (cf. F2/F4)
bash training/scripts/pod_f4_sweep.sh          # recette pilotée par l'éval
```
Ne déployer la nouvelle version que si l'**éval F1** est verte : garde-fous = 100 %,
0 fuite de dosage, qualité ≥ baseline.

## Périodicité
Cadence recommandée : **mensuelle** (ou dès qu'un volume suffisant de 👎 s'accumule).
La curation (étape 2) est légère (CPU, pas de GPU) et peut être planifiée ; seul le
ré-entraînement (étape 3) requiert le pod GPU.

## Dépannage
- « Aucun cas à curer » : vérifier que `feedback.jsonl` contient des votes `up`/`down`
  et que les `id` correspondent à des interactions.
- Beaucoup d'« invalides » (sans source / trop court) : le maître ne respecte pas le
  format — réessayer ou ajuster le modèle (`--juge-model`).
- Sécurité : une paire contenant un dosage chiffré est **toujours** écartée.
