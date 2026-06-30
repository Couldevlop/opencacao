# Détection de localité — agent Météo

**Date** : 2026-06-30
**Auteur** : session OpenCacao
**Statut** : conçu, à implémenter (TDD)

## Problème

L'agent Météo géocode une localité avant d'interroger Open-Meteo. La détection
actuelle (`agent_meteo.py`) est une regex fragile `\bà\s+([A-ZÉÈÀ]…)` :

- exige une majuscule (les producteurs écrivent souvent en minuscules sur mobile) ;
- exige la préposition « à » (rate « sur Gagnoa », « je suis à Soubré » sans
  majuscule, « Daloa, il pleut ? ») ;
- quand rien n'est détecté, retombe sur `geo_defaut="Côte d'Ivoire"` → géocodage du
  **centroïde du pays** → prévision générique inutile pour le producteur.

## Périmètre

**Agent Météo uniquement.** L'agent Prix s'appuie sur le prix bord-champ
**national administré** (Conseil Café-Cacao) + RAG : il n'existe pas de prix
géolocalisé à récupérer, donc la détection de localité n'a aucun effet mécanique
sur Prix. Hors périmètre.

## Décisions

1. **Source de détection** : réutiliser la liste officielle des localités déjà
   présente dans `contacts_zones.yaml` (10 Directions Régionales / 60 zones),
   extraite dans un module partagé à responsabilité unique.
2. **Sans localité détectée** : ne PAS injecter de prévision ; injecter une
   **consigne explicite** demandant la commune et interdisant d'inventer une donnée
   météo (même pattern de souveraineté que `agent_prix._formater_cours` pour un prix
   manquant). Le modèle demande alors poliment la commune.
3. **Détection sur tout le fil** : concaténer les tours `historique` (rôle user) +
   `fil_ancre`, pas seulement le dernier tour, afin qu'une ville citée plus tôt
   reste connue au tour suivant.
4. **Retrait de la regex `à <Ville>`** et du paramètre `geo_defaut`. Plus de
   centroïde pays, plus de faux positifs (« à Midi »).

**Compromis assumé** : une ville hors des 60 zones n'est plus géocodée — on demande
la commune (sûr) plutôt que risquer un géocodage hasardeux.

## Architecture

### Nouveau module `api/app/services/localites.py`

Brique unique : « trouver une localité ivoirienne connue dans un texte ». Source de
vérité = `app/data/contacts_zones.yaml`.

API publique :

- `detecter(texte: str) -> str | None` — renvoie le **nom canonique** (casse
  d'origine du YAML, ex. `San-Pédro`) de la première localité reconnue, ou `None`.
  Matching insensible casse/accents, mot-frontière, libellé le plus long d'abord.
- `chercher_zone(texte: str) -> dict | None` — renvoie le dict de la Direction
  Régionale correspondante (consommé par `contacts.py`).

Internes (migrés depuis `contacts.py`) :

- `_normaliser(texte)` — minuscule + suppression des accents.
- `_index()` (lru_cache) — parse le YAML, construit la liste
  `(regex_sur_libellé_normalisé, nom_canonique, dr_dict)` triée par longueur de
  libellé décroissante (un libellé long « san pedro » prime sur un court).

### Refactor `api/app/services/contacts.py`

`chercher()` délègue le scan à `localites.chercher_zone()` puis construit son
`ContactDR` (nom/siège/tel/email/verifie + zone matchée). On supprime
`_index_zones` et `_normaliser` de `contacts.py` (désormais dans `localites`).
**Comportement public identique** — la suite de tests existante de `contacts`
verrouille la non-régression.

### `api/app/services/agents/agent_meteo.py`

- Supprimer `_detecter_localite` (regex) et le paramètre `geo_defaut`.
- `_contexte` :
  1. `texte = _fil_complet(requete)` — concat des `content` des tours `historique`
     de rôle `user` + `requete.fil_ancre`.
  2. `localite = localites.detecter(texte)`.
  3. si `localite` → `previsions = await self._outil.invoquer(localite=localite)` →
     `_formater_previsions(localite, previsions)` (inchangé).
  4. sinon → renvoyer la **consigne** (texte fixe) : aucune prévision possible sans
     commune ; demander dans quelle commune se trouve le producteur ; ne JAMAIS
     inventer de donnée météo.

Le câblage `api_deps.py:111` (`AgentMeteo(inference, OutilMeteo(meteo))`) est déjà
sans `geo_defaut` : aucun changement de construction.

## Flux de données

```
question + historique
        │
        ▼
_fil_complet ──► localites.detecter ──► localite ?
                                          │
                 ┌────────────────────────┴───────────────┐
                 │ oui                                     │ non
                 ▼                                         ▼
   OutilMeteo.invoquer(localite)              consigne « demande la commune,
   → Open-Meteo (géocode + prévision)           n'invente aucune météo »
                 │                                         │
                 ▼                                         │
   _formater_previsions(localite, …)                       │
                 └───────────────► contexte ◄──────────────┘
                                      │
                                      ▼
                         inférence (contexte injecté)
```

## Gestion des erreurs / dégradation

- YAML illisible → `_index()` renvoie `[]` → `detecter` renvoie `None` → chemin
  consigne (sûr, pas de plantage).
- Open-Meteo en échec → `OutilMeteo` fail-soft `{}` existant (inchangé) ;
  `_formater_previsions` renvoie `None` sur prévision vide (inchangé).

## Tests (TDD — écrits avant le code)

### `api/tests/services/test_localites.py`
- `detecter("Quel temps à daloa ?")` → `"Daloa"` (casse/accents insensibles).
- `detecter("prévisions sur San-Pédro")` → `"San-Pédro"`.
- libellé multi-mots : un libellé long prime sur un court englobé.
- mot-frontière : pas de match partiel (ville ⊄ d'un autre mot).
- aucun match → `None`.
- YAML absent/illisible (monkeypatch chemin) → `None`.

### `api/tests/agents/test_agent_meteo.py`
- ville dans `fil_ancre` → `OutilMeteo` invoqué avec cette ville ; contexte = prévisions.
- ville **seulement dans `historique`** (pas dans le dernier tour) → toujours détectée.
- aucune ville → contexte = consigne (contient « commune », interdit d'inventer ;
  `OutilMeteo` non invoqué).
- les tests de mots-clés météo existants restent verts.

### `api/tests/services/test_contacts.py` (existant)
- suite verte après refactor (garde-fou de non-régression).

Couverture min. 80 % sur `api/app/` maintenue ; inférence et réseau mockés.

## Hors périmètre

- Routage multi-tours : si le producteur répond « Daloa » sans mot-clé météo, le
  routeur peut ne pas re-sélectionner l'agent Météo. Concern de l'orchestrateur,
  non traité ici. La détection sur l'historique atténue le cas le plus courant
  (ville citée dans le même fil météo).
- Agent Prix (prix national, pas de géolocalisation).
- Géocodage de villages hors des 60 zones officielles.
