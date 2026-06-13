# Construction du corpus Q/R par RAG

Ce guide décrit la constitution du corpus d'instruction-tuning d'OpenCacao à
partir des **documents officiels** de la filière cacao ivoirienne, via un
pipeline RAG entièrement local (CLAUDE §5, §13).

## Principe

```
documents officiels (PDF)  →  extraction  →  passages (chunks)
        →  index sémantique local (sentence-transformers)
        →  génération Q/R ancrée (LLM local OpenAI-compatible)
        →  validation (longueurs, source citée, anti-dosage phyto)
        →  corpus/corpus_cacao_rag.jsonl
```

Souveraineté : embeddings et génération tournent en local. Le seul appel réseau
sortant est le **téléchargement initial** des documents publics, rassemblés
dans le dossier `train/` (gitignoré, reconstructible depuis le manifeste).
Aucun service propriétaire (OpenAI, Anthropic…) n'intervient — conforme au
principe de souveraineté (§1.3).

Les fiches techniques du CNRA servies par un gestionnaire de téléchargement
WordPress (`/download/<slug>/`) sont résolues automatiquement vers leur PDF
réel (`?wpdmdl=`) par le téléchargeur — aucune action manuelle requise.

## Sources

Le fichier `corpus/sources/sources_officielles.yaml` liste les documents. Le
champ `source` de chaque entrée doit appartenir à l'ensemble reconnu par le
validateur (`CNRA`, `ANADER`, `Conseil du Café-Cacao`, `FAO`) afin que les
réponses citent une source valide.

## Pré-requis

```bash
pip install -r training/requirements-corpus.txt
```

Un serveur LLM **local** OpenAI-compatible doit être disponible (vLLM servant
Mistral-7B ou le modèle OpenCacao). Variables d'environnement :

| Variable | Rôle | Défaut |
|---|---|---|
| `CORPUS_LLM_BASE_URL` | URL du serveur LLM | `http://localhost:8000` |
| `CORPUS_LLM_MODEL` | Nom du modèle servi | `opencacao-7b` |
| `CORPUS_LLM_API_KEY` | Jeton (optionnel) | — |
| `CORPUS_EMBED_MODEL` | Modèle d'embeddings | `paraphrase-multilingual-MiniLM-L12-v2` |

## Utilisation

```bash
# 1. Télécharger + découper uniquement (vérifier les sources, sans LLM)
make corpus-rag-collect

# 2. Générer le corpus (LLM local requis)
make corpus-rag TARGET=5000

# 3. Valider le corpus produit
python training/scripts/enrich_corpus.py --check corpus/corpus_cacao_rag.jsonl
```

Le pipeline est **idempotent et reprenable** : il relit le fichier de sortie,
ignore les questions déjà présentes et complète jusqu'à `--target`.

## Capacité réelle du corpus et montée vers 10 000

Le plafond dépend du **volume de passages** extraits des documents et du nombre
d'**angles** de questionnement appliqués à chaque passage.

### Génération multi-angles

Un même passage porte plusieurs questions *réellement distinctes* (pas des
paraphrases) : symptômes, action curative, prévention, calendrier, cause,
conséquence, question de débutant, bonnes pratiques. Le pipeline interroge le
LLM sous chacun de ces angles (`ANGLES`), puis la déduplication exacte +
sémantique élimine les recoupements.

```
paires utiles ≈ chunks_uniques × angles_pertinents × par_chunk × validation
```

Ordre de grandeur avec le jeu de sources complet (~700 passages après dédup) et
les 8 angles : **~6 000 à 9 000 Q/R** distinctes et sourcées, configurable via
`--target` (défaut 10 000).

### Leviers, par ordre de préférence

1. **Ajouter des documents officiels** au manifeste — chaque PDF ajoute des
   passages (levier le plus sain). Les fiches techniques CNRA sont déjà toutes
   intégrées.
2. **Multi-angles** (déjà actif) — multiplie les questions fidèles par passage.
   Sélectionner un sous-ensemble : `--angles symptomes,action,prevention`.
3. Augmenter `--par-chunk` (paires par couple passage×angle).
4. **Ne jamais** gonfler par paraphrases : la dédup sémantique les élimine.

Tout plafonnement (cible non atteinte) est journalisé en clair à la fin du run
(`run_termine` : écrites / générées / rejetées / doublons).

### Vitesse : privilégier un GPU loué

La génération multi-angles représente des milliers d'appels LLM. Sur CPU
(Ollama, ~3–8 tok/s) viser 10 000 prend de nombreuses heures. Sur un **GPU loué**
(RunPod / Vast.ai, RTX 4090 ~0,40 $/h) servant Mistral via vLLM, le corpus
complet se génère en **1–3 h** pour ~1–2 $, puis l'instance est libérée. Le
pipeline est reprenable : on peut l'interrompre et relancer sans perte.

## Garde-fous appliqués à chaque paire

Réutilisés depuis `enrich_corpus.py` :

- longueur instruction 10–500, réponse 50–2000 caractères ;
- **refus de tout dosage phytosanitaire chiffré** (filtre regex) ;
- présence d'au moins une source citée.

Le prompt système interdit en outre l'invention de faits absents du passage et
impose la redirection vers l'agent ANADER pour les sujets sensibles.
