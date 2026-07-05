# Clarification « plantation » : couverture élargie + localité en premier

**Date** : 2026-07-05 · **Statut** : validé (choix « Oui + localité en 1er »)

## Problème

« Je veux planter des cacaoyers » reçoit une réponse directe au lieu du dialogue
consultatif : les motifs du thème `plantation` de
`api/app/services/clarification.py` ne couvrent que des formulations comme
« créer une plantation » ou « écartement », pas le verbe « planter » ni ses
variantes naturelles. Le producteur n'est donc jamais interrogé sur sa localité,
alors que variétés et calendrier en dépendent d'abord.

## Décision

1. **Élargir les motifs `_PLANTATION`** (normalisés, sans accents) : `planter`,
   `semer`, `pepiniere`, `creer un champ`, `demarrer un champ`, `champ de cacao`.
   Détection déterministe inchangée (aucune inférence, fiable sur CPU).
2. **Localité en tête** pour le thème `plantation` uniquement : la question
   « Dans quelle ville ou région vous trouvez-vous ? » devient la **première**
   puce (au lieu d'être ajoutée en fin). Les autres thèmes gardent l'ordre
   actuel. Comme aujourd'hui, la localité n'est pas redemandée si la ville
   figure déjà dans la question (`contacts.chercher`).

Sortie attendue pour « Je veux planter des cacaoyers » (1er tour) :

```
Pour bien démarrer votre plantation :
• Dans quelle ville ou région vous trouvez-vous ?
• Quelle surface envisagez-vous, et quel type de sol ?
• Avez-vous déjà des plants ou semences sélectionnés ?
Répondez-moi et je vous conseillerai au mieux.
```

## Portée

- `api/app/services/clarification.py` : motifs + placement de la puce localité.
- `api/tests/test_clarification.py` : un test par nouvelle formulation, ordre
  des puces, non-répétition de la localité si la ville est donnée.
- Aucun autre module : V2 (`conseil_service`) et V3 (`orchestrateur`) partagent
  déjà `clarification.analyser`, les deux chemins (sync et stream) en profitent.

## Hors portée

Détection d'intention par le modèle (contraire au choix déterministe documenté),
mémoire de localité inter-conversations.
