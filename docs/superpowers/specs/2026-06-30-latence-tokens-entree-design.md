# Latence — réduire les tokens d'entrée (préremplissage)

**Date** : 2026-06-30
**Auteur** : session OpenCacao
**Statut** : conçu, à implémenter

## Problème (mesuré)

La latence des réponses générées en prod (~36-42 s, CPU/GGUF Ministral-8B) est
**dominée par le préremplissage** (prompt eval), pas par la génération. Mesures :

| Composant d'entrée | Tokens (≈) |
|---|---|
| Prompt système (`SYSTEM_PROMPT`, 2129 car.) | **532** |
| Wrapper contexte RAG (`CONTEXTE_PROMPT`) | 80 |
| Contexte RAG (3 passages × ~146 tok ; p90 195) | **440** (médian) |
| Question | ~20 |
| **Total entrée / requête RAG** | **~1070** |

llama-server : `-c 4096`, `-t 12`, flash-attn, KV q8_0, **1 slot**. Préremplissage
mesuré ≈ ~42-54 tok/s → ~1070 tokens ≈ ~20-25 s, cohérent avec les 35-42 s observés
(préremplissage + génération). Le **prompt système (532 tok) pèse autant que tout le
RAG**, et le payload d'inférence n'envoie **pas** `cache_prompt` → le préfixe système
constant est probablement re-prérempli à chaque requête pour rien.

Le sprint précédent (réponses concises) n'a rien donné car il visait la génération,
pas le préremplissage. Ce sprint vise le préremplissage = le vrai goulot.

## Objectif

Réduire les tokens d'entrée par requête sur 3 leviers indépendants, sans dégrader
les garde-fous ni le recall. Cible indicative : ~38 s → ~28-30 s (mieux si le cache
de préfixe mord). Validée empiriquement, levier par levier.

## Leviers (3 unités indépendantes)

### Levier 1 — `cache_prompt: true` (zéro risque qualité)

`api/app/services/inference.py` : ajouter `"cache_prompt": True` aux deux payloads
(`generer` et `generer_stream`). llama-server réutilise alors le KV du **préfixe
commun** (le message système, constant) entre requêtes du même slot → le prompt
système n'est plus re-prérempli après la 1ʳᵉ requête. Aucun effet sur la sortie
(mêmes tokens, juste pas recalculés). Effet réel **à mesurer** (dépend du serveur).

### Levier 2 — Trim `SYSTEM_PROMPT` : 532 → ~280 tokens

`api/app/services/prompts.py` : condenser `SYSTEM_PROMPT` (~2129 → ~1100 car.) en
**préservant TOUTES les règles** sous forme dense :
- périmètre cacao UNIQUEMENT (autres cultures → ANADER ; ombrage/associées OK si au
  service d'une plantation de cacao) ;
- jamais de dosage phytosanitaire précis → ANADER ;
- ne jamais inventer source / date / chiffre / nom d'organisme ;
- ne jamais donner soi-même un numéro / une adresse (demander la ville) ;
- multi-tours : garder le même sujet, résoudre les références ;
- clarification : poser UNE question si une info essentielle manque ;
- brièveté : 10 phrases maximum, droit au but.

Aucune règle supprimée — uniquement reformulée plus court. Universel (payé à chaque
requête, sauf si le Levier 1 le met en cache).

### Levier 3 — Plafonner la longueur des passages RAG : ~120 tok/passage

`api/app/services/rag.py` : tronquer chaque `passage.texte` à `rag_passage_max_chars`
(nouveau réglage, défaut **480** car. ≈ 120 tok) **à une frontière de phrase**
(dernier `.`/`!`/`?`/retour ligne avant la limite ; repli = coupe nette + `…`).
On garde les 3 passages (diversité des sources préservée) en coupant le gras.
~440 → ~360 tok. Réglable à chaud via `RAG_PASSAGE_MAX_CHARS`.

## Architecture / changements

### `api/app/core/config.py`
Nouveau réglage :
```python
rag_passage_max_chars: int = 480
```

### `api/app/services/rag.py`
- Nouvelle fonction pure `tronquer_passage(texte: str, max_chars: int) -> str` :
  si `len(texte) <= max_chars`, renvoie `texte` inchangé ; sinon coupe à la dernière
  frontière de phrase (`.`/`!`/`?`/`\n`) située avant `max_chars`, et si aucune,
  coupe net à `max_chars` ; ajoute `…` quand on a tronqué.
- `formater_contexte(passages, max_chars: int | None = None)` : applique
  `tronquer_passage` à chaque `passage.texte` quand `max_chars` est fourni
  (sinon comportement actuel inchangé — rétrocompat).
- `RagRecuperateur.__init__` reçoit `passage_max_chars: int` (depuis la config) et le
  passe à `formater_contexte` dans `contexte_pour`.

### `api/app/api_deps.py` (ou point de construction du `RagRecuperateur`)
Passer `passage_max_chars=settings.rag_passage_max_chars` à la construction.

### `api/app/services/inference.py`
Ajouter `"cache_prompt": True` aux deux payloads.

### `deploy/k8s/api.yaml`
Ajouter `RAG_PASSAGE_MAX_CHARS: "480"` (documenté, réglable à chaud).

## Sécurités (ce qui ne casse pas)

- **Garde-fous** : le trim ne supprime AUCUNE règle — tests de non-régression par
  sous-chaîne sur chaque règle critique. Disclaimer (code) et sources
  (post-génération) inchangés.
- **Recall RAG** : 3 passages conservés ; seule la longueur est plafonnée à une
  frontière de phrase (pas de coupe en plein mot). Réglable à chaud si trop court.
- **`cache_prompt`** : sémantiquement neutre (mêmes tokens).
- **Caches applicatifs** : `roll-image.sh` purge `cache:chat:*` + `APP_VERSION` change
  → réponses régénérées avec les nouveaux prompts.

## Tests (TDD)

### `api/tests/test_inference.py`
- Le payload de `generer` contient `cache_prompt = True` (capturé via MockTransport).
- Idem pour `generer_stream`.

### `api/tests/test_prompts.py`
- `SYSTEM_PROMPT` plus court qu'avant (`len(SYSTEM_PROMPT) <= 1200`).
- Conserve CHAQUE règle critique (sous-chaînes stables choisies dans le nouveau texte :
  « cacao », « ANADER », « dosage », « numéro », « invente », « 10 phrases »).

### `api/tests/test_rag.py`
- `tronquer_passage(court, 480)` renvoie le texte inchangé (pas de `…`).
- `tronquer_passage(long, 480)` renvoie ≤ 480 car., coupe à une frontière de phrase,
  finit par `…`, ne coupe pas un mot en deux.
- `formater_contexte(passages, max_chars=480)` tronque les passages longs et garde
  les 3 ; sans `max_chars`, comportement inchangé (rétrocompat).
- `Settings().rag_passage_max_chars == 480`.

Couverture ≥ 97 % maintenue ; inférence/réseau/embeddings mockés.

## Validation empirique (prod, le vrai juge — isoler chaque levier)

Mêmes ~8 questions repères (agronomie/RAG, prix, météo, contact, multi-tours,
hors-filière). Déploiement par paliers pour **isoler l'effet** :
1. Levier 1 seul (`cache_prompt`) → mesurer (la 2ᵉ requête d'une série doit bénéficier
   du préfixe caché si l'effet est réel).
2. + Levier 2 (trim prompt) → mesurer.
3. + Levier 3 (cap RAG) → mesurer.

Pour chaque palier : latence médiane, non-troncature des réponses, **recall**
(les réponses RAG citent toujours les bonnes sources), garde-fous (refus hors-filière,
contact injecté), disclaimer présent. Consigner les mesures.

**Critère de succès** : latence médiane des réponses générées en baisse nette vs
baseline (~38 s) SANS perte de recall, de garde-fou ni de disclaimer.

## Plan de repli (à chaud, sans rebuild)

- `RAG_PASSAGE_MAX_CHARS` plus haut (ConfigMap + rollout restart) si recall souffre.
- Retirer `cache_prompt` est un rebuild (peu probable nécessaire — neutre).
- Le trim prompt se révise au prochain build si un garde-fou se relâche (peu probable,
  verrouillé par tests).
