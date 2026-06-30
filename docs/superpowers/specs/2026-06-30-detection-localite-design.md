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
2. **Trois cas** dans l'agent Météo, évalués sur tout le fil :
   - **localité cacaoyère** détectée → prévisions Open-Meteo (inchangé) ;
   - **localité non cacaoyère** détectée (zone de savane du Nord) → consigne
     expliquant que la localité n'est pas concernée par la culture du cacao, sans
     prévision (même rationnel que le garde-fou `REFUS_ZONE_NON_CACAO`) ;
   - **aucune localité** → consigne demandant la commune, sans inventer de météo
     (même pattern de souveraineté que `agent_prix._formater_cours`).
   Dans les deux cas de consigne, on n'injecte PAS de contexte vide : on guide le
   modèle, qui répond alors dans la langue/le ton du producteur.
3. **Détection sur tout le fil** : concaténer les tours `historique` (rôle user) +
   `fil_ancre`, pas seulement le dernier tour, afin qu'une ville citée plus tôt
   reste connue au tour suivant.
4. **Retrait de la regex `à <Ville>`** et du paramètre `geo_defaut`. Plus de
   centroïde pays, plus de faux positifs (« à Midi »).
5. **Connaissance « zone cacaoyère ou non »** : on réutilise la deny-list curée
   existante `_LOCALITES_NORD` (15 villes de savane, décision métier Waopron) ; on
   ne l'élargit PAS. Elle migre dans `localites.py` (source unique de la
   connaissance des localités) ; `guardrails.py` l'importe — comportement identique.

**Compromis assumé** : une ville hors des 60 zones n'est plus géocodée — on demande
la commune (sûr) plutôt que risquer un géocodage hasardeux.

## Architecture

### Nouveau module `api/app/services/localites.py`

Brique unique : « trouver une localité ivoirienne connue dans un texte ». Source de
vérité = `app/data/contacts_zones.yaml`.

API publique :

- `detecter(texte: str) -> str | None` — renvoie le **nom canonique** (casse
  d'origine du YAML, ex. `San-Pédro`) de la première localité **cacaoyère** reconnue
  (zones du YAML **moins** la deny-list Nord), ou `None`. Sert au géocodage Météo.
  Matching insensible casse/accents, mot-frontière, libellé le plus long d'abord.
- `detecter_nord(texte: str) -> str | None` — renvoie le nom d'affichage de la
  première ville **non cacaoyère** (deny-list de savane du Nord) citée, ou `None`.
- `chercher_zone(texte: str) -> dict | None` — renvoie le dict de la Direction
  Régionale correspondante, **toutes zones** (Nord inclus : un producteur du Nord
  garde droit au contact ANADER). Consommé par `contacts.py`.

Données et internes (migrés depuis `contacts.py` et `guardrails.py`) :

- `LOCALITES_NORD: dict[str, str]` — deny-list curée des villes de savane non
  cacaoyères (15 entrées clé normalisée → nom d'affichage). Importée par
  `guardrails.py` (qui ne la redéfinit plus). **Non élargie** (décision Waopron).
- `_normaliser(texte)` — minuscule + suppression des accents.
- `_index()` (lru_cache) — parse le YAML, construit la liste
  `(regex_sur_libellé_normalisé, nom_canonique, dr_dict)` triée par longueur de
  libellé décroissante (un libellé long « san pedro » prime sur un court). `detecter`
  exclut les entrées dont le nom est dans `LOCALITES_NORD`.

### Refactor `api/app/services/contacts.py`

`chercher()` délègue le scan à `localites.chercher_zone()` puis construit son
`ContactDR` (nom/siège/tel/email/verifie + zone matchée). On supprime
`_index_zones` et `_normaliser` de `contacts.py` (désormais dans `localites`).
**Comportement public identique** — la suite de tests existante de `contacts`
verrouille la non-régression.

### Refactor `api/app/services/guardrails.py`

`_LOCALITES_NORD` (le dict) migre dans `localites.py` ; `guardrails.py` importe
`localites.LOCALITES_NORD` pour construire `_RE_LOCALITES_NORD`. Le `_normaliser`
local de `guardrails` reste (il sert à d'autres règles) — pas de couplage forcé.
**Comportement de `evaluer` identique** : ses tests existants (dont le cas
`ZONE_NON_CACAO`) verrouillent la non-régression.

### `api/app/services/agents/agent_meteo.py`

- Supprimer `_detecter_localite` (regex) et le paramètre `geo_defaut`.
- `_contexte` :
  1. `texte = _fil_complet(requete)` — concat des `content` des tours `historique`
     de rôle `user` + `requete.fil_ancre`.
  2. `localite = localites.detecter(texte)` (cacaoyère). Si trouvée →
     `previsions = await self._outil.invoquer(localite=localite)` →
     `_formater_previsions(localite, previsions)` (inchangé).
  3. sinon `nord = localites.detecter_nord(texte)`. Si trouvée → **consigne zone non
     cacaoyère** (texte fixe nommant la localité) : cette localité est en savane du
     Nord, non cacaoyère ; explique au producteur qu'elle n'est pas concernée par la
     culture du cacao ; ne donne aucune prévision ni donnée inventée.
  4. sinon → **consigne commune** (texte fixe) : aucune prévision possible sans
     commune ; demande dans quelle commune se trouve le producteur ; ne JAMAIS
     inventer de donnée météo.

`OutilMeteo` n'est invoqué que dans le cas 2 (localité cacaoyère). Le câblage
`api_deps.py:111` (`AgentMeteo(inference, OutilMeteo(meteo))`) est déjà sans
`geo_defaut` : aucun changement de construction.

## Flux de données

```
question + historique
        │
        ▼
_fil_complet ──► localites.detecter (cacaoyère) ──► localite ?
                                                       │
        ┌──────────────────────────────────────┬──────┴── oui ──┐
        │ non                                   │                ▼
        ▼                                       │   OutilMeteo.invoquer(localite)
localites.detecter_nord ──► ville Nord ?        │   → Open-Meteo (géocode + prév.)
        │                                       │                │
   ┌────┴── oui ──┐         ┌── non ──┐         │                ▼
   ▼              │         ▼         │         │   _formater_previsions(localite,…)
consigne « zone   │   consigne        │         │                │
non cacaoyère »   │   « demande la    │         │                │
                  │     commune »     │         │                │
                  └────────┬──────────┴─────────┴────────────────┘
                           ▼
                       contexte
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
- `detecter("temps à Korhogo ?")` → `None` (ville Nord exclue du détecteur cacao).
- `detecter_nord("temps à Korhogo ?")` → `"Korhogo"`.
- `detecter_nord("temps à Daloa ?")` → `None`.

### `api/tests/agents/test_agent_meteo.py`
- ville cacaoyère dans `fil_ancre` → `OutilMeteo` invoqué avec cette ville ;
  contexte = prévisions.
- ville cacaoyère **seulement dans `historique`** (pas dans le dernier tour) →
  toujours détectée.
- **ville Nord** (ex. Korhogo) → contexte = consigne « zone non cacaoyère » (nomme la
  localité, interdit d'inventer ; `OutilMeteo` non invoqué).
- aucune ville → contexte = consigne « commune » (contient « commune », interdit
  d'inventer ; `OutilMeteo` non invoqué).
- les tests de mots-clés météo existants restent verts.

### `api/tests/services/test_contacts.py` et `test_guardrails.py` (existants)
- suites vertes après refactor (garde-fous de non-régression, dont `ZONE_NON_CACAO`).

Couverture min. 80 % sur `api/app/` maintenue ; inférence et réseau mockés.

## Hors périmètre

- Routage multi-tours : si le producteur répond « Daloa » sans mot-clé météo, le
  routeur peut ne pas re-sélectionner l'agent Météo. Concern de l'orchestrateur,
  non traité ici. La détection sur l'historique atténue le cas le plus courant
  (ville citée dans le même fil météo).
- Agent Prix (prix national, pas de géolocalisation).
- Géocodage de villages hors des 60 zones officielles.
