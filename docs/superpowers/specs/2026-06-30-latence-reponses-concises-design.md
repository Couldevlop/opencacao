# Latence — réponses plus concises

**Date** : 2026-06-30
**Auteur** : session OpenCacao
**Statut** : conçu, à implémenter

## Problème

En production (CPU/GGUF Ministral-8B, ~8 tok/s), une génération complète prend
~38 s. La latence est **dominée par la longueur de sortie** (38 s ≈ ~300 tokens
générés ÷ 8 tok/s). Le `SYSTEM_PROMPT` n'a qu'une consigne molle de concision
(« Reste concis… surtout par SMS ») ; le modèle peut donc générer jusqu'au plafond
`max_tokens` (384 en prod) sur des réponses verbeuses.

## Objectif

Faire produire au modèle des réponses plus courtes via une **consigne ferme de
brièveté**, sans dégrader l'utilité ni les garde-fous. Réglage volontairement
prudent (priorité qualité).

## Décisions

1. **Consigne ferme** dans `SYSTEM_PROMPT` : « Réponds en 10 phrases maximum, va
   droit au but, sans rappel général ni reformulation de la question. » Remplace la
   règle molle existante. Toutes les autres règles (cacao-only, pas de dosage, pas
   de source/numéro inventé, multi-tours, clarification) restent **intactes**.
2. **`max_tokens` = 400** (uniforme, tous canaux). Note : 400 est légèrement
   au-dessus du plafond actuel (384) — il sert de **plafond de sécurité généreux**
   (une réponse de 10 phrases ≈ 250-330 tokens, jamais tronquée). Le levier de
   latence est donc **la consigne**, pas le plafond.

## Périmètre

- **Inclus** : `SYSTEM_PROMPT` (concision) ; valeur `max_tokens` (config + déploiement).
- **Exclus** : différenciation par canal (SMS vs Web) — tranché « uniforme » ;
  migration MoE / GPU (épopées séparées) ; pré-chauffage FAQ élargi (autre levier).

## Architecture / changements

### `api/app/services/prompts.py`
Remplacer la dernière puce du `SYSTEM_PROMPT` :

- Avant : `"- Reste concis : va à l'essentiel, surtout pour une réponse par SMS."`
- Après : `"- Sois bref : réponds en 10 phrases maximum, va droit au but, sans rappel général ni reformulation de la question. Mieux vaut une réponse courte et juste qu'une longue."`

Aucune autre modification du module (les règles garde-fous et multi-tours ne bougent pas).

### `api/app/core/config.py`
`inference_max_tokens` : défaut `512` → `400` (aligne le code sur la cible).

### `deploy/k8s/api.yaml`
`INFERENCE_MAX_TOKENS: "384"` → `"400"`.

## Sécurités (ce qui ne casse pas)

- **Disclaimer ANADER** : ajouté par le code (constante `DISCLAIMER`, champ
  `ChatResponse.disclaimer`), **jamais généré** → aucun risque de troncature.
- **Sources** : extraites du texte après génération (`postprocess.extraire_sources`)
  → inchangé.
- **Garde-fous entrée/sortie** : inchangés.
- **Caches** : `roll-image.sh <tag>` purge `cache:chat:*` (Redis) et le nouveau
  `APP_VERSION` invalide les clés de cache → les réponses verbeuses cachées ET le
  pré-chauffage FAQ (`prewarm.py` au démarrage) sont régénérés **concis**.

## Tests (TDD — écrits avant le code)

### `api/tests/test_prompts.py`
- Le `SYSTEM_PROMPT` contient la consigne ferme de brièveté (sous-chaîne
  « 10 phrases maximum »).
- Le `SYSTEM_PROMPT` conserve les règles critiques non négociables (sous-chaînes :
  « UNIQUEMENT le cacao », « dosages précis », « jamais toi-même un numéro »).
  Garde-fou de non-régression : la concision ne doit pas effacer une règle métier.

### `api/tests/test_inference.py` (ou test config)
- `Settings().inference_max_tokens == 400` (défaut aligné sur la cible).

> Le reste (gain réel de latence, non-troncature, qualité préservée) n'est PAS
> testable hors inférence réelle — voir la validation empirique.

## Validation empirique (le vrai juge — en prod, après déploiement)

Jeu de ~8 questions représentatives couvrant les chemins : agronomie (RAG), prix,
météo (localité), mise en relation ANADER (contact), multi-tours (clarification),
hors-filière (refus). Pour chacune, avant/après :

- mesurer la **latence** (temps total de `/v1/chat`) et la **longueur** de réponse ;
- vérifier manuellement : réponse **non tronquée** (finit proprement), **garde-fous
  intacts**, **disclaimer présent**, information utile préservée.

**Critère de succès** : latence médiane en baisse (même modeste) sans perte de
justesse, de garde-fou ni de disclaimer. Baseline ≈ 38 s.

## Plan de repli

Réglage à chaud sans redéploiement d'image : remonter `INFERENCE_MAX_TOKENS`
(ConfigMap) si troncature, ou assouplir la consigne (« 10 » → « 12-15 phrases »)
au prochain build si les réponses sont trop sèches.
