# Agent A10 « Normes » — référentiels de durabilité et qualité du cacao

> Design validé le 2026-07-02. Applique « la recette » d'ajout d'agent
> (`docs/agents_v3.md` §Recette) : nouvel adaptateur + mots-clés + enregistrement,
> zéro refactor de l'orchestrateur/registre/routeur.

## Objectif

Ajouter l'agent n°6 du socle agentique V3 : **Normes**. Il conseille les producteurs
sur les **référentiels de durabilité et de qualité** du cacao — certifications
volontaires (Rainforest Alliance, Fairtrade, agriculture biologique), normes qualité
(ISO) et la **norme régionale africaine ARS 1000** sur le cacao durable.

C'est le jumeau de l'agent Réglementation/EUDR : même moule (RAG + cadrage préfixé,
**aucun outil externe**), domaine différent. Sujet cacao → admis par les garde-fous.

## Frontière avec l'agent Réglementation/EUDR (décision de conception)

**Séparation nette** entre les deux agents « accès au marché » :

| Agent | Domaine | Déclencheurs |
|---|---|---|
| **Réglementation** (existant) | Accès marché **contraignant** : EUDR, loi UE, douane, export, traçabilité parcelle | eudr, déforestation, traçabilité, export, règlement, douane… |
| **Normes** (nouveau) | Référentiels **volontaires** + qualité : labels, certifications, ARS 1000, ISO, bio | certification, label, rainforest, fairtrade, bio, iso, ars 1000, norme, durabilité… |

Conséquence : on **retire** `certification`, `durabilite`, `durabilité` des mots-clés
de l'agent Réglementation (aucun test ne les utilise comme requête) et on les confie à
Normes. Le mot `traçabilité` **reste** à Réglementation (concept cœur EUDR : géoloc
parcelle). Sur une requête ambiguë à égalité de score, le tri stable du routeur fait
gagner l'agent enregistré en premier ; on enregistre Normes **après** Réglementation,
donc « certification pour exporter vers l'UE » reste à Réglementation, tandis que
« certification Rainforest Alliance ? » (2 mots-clés Normes) gagne nettement côté Normes.

## Périmètre & souveraineté

Référentiels couverts (cadrage général) : **Rainforest Alliance, Fairtrade / commerce
équitable, agriculture biologique/organic, ISO, ARS 1000**.

**Garde-fou de souveraineté (non négociable, comme l'EUDR).** Les critères, seuils,
montants de **prime**, exigences d'audit et dates de validité des certifications
**évoluent** et varient selon l'organisme et la campagne. Sans document RAG,
l'agent **n'invente AUCUN** de ces éléments : il explique le principe général puis
oriente vers l'**organisme certificateur**, la **coopérative** ou l'**agent ANADER**.
Il ne s'aventure pas sur les **primes chiffrées** (c'est le domaine de l'agent Prix, et
elles ne sont pas garanties) — cohérent avec l'anti-fabrication de prix déjà en place.

## Composants

- **`api/app/services/agents/agent_normes.py`** — `AgentNormes(AgentBase)` :
  - `nom = "normes"`, `mots_cles = _MOTS_NORMES`.
  - `peut_traiter` : `0.0` si aucun mot-clé (mot entier), sinon `min(0.7 + 0.1·touches, 1.0)` — même barème que Réglementation.
  - `_contexte` : préfixe le contexte RAG d'un cadrage `_CADRE_NORMES` ; sans RAG, `_CADRE_NORMES_SANS_DOC` (consigne anti-fabrication).
  - Constructeur `(inference, rag=None)`.
- **`api/app/services/agents/agent_reglementation.py`** — retrait de 3 mots-clés (voir Frontière).
- **`api/app/api_deps.py`** — une ligne dans `_construire_orchestrateur` :
  `registre.enregistrer(AgentNormes(inference, rag=rag))`, après Réglementation.
  Mise à jour de la docstring (nombre d'agents).
- **`docs/agents_v3.md`** — mention de l'agent n°6 dans la recette.

## Tests (`api/tests/agents/test_agent_normes.py`)

En TDD, sur le moule de `test_agent_reglementation.py` :
1. `peut_traiter` élevé (≥0.7) sur une question certification/label ; faible (<0.3) sur une question de taille du cacaoyer.
2. `traiter` injecte le cadrage Normes **et** le contexte RAG ; `reponse.agent == "normes"`.
3. Sans RAG, le cadrage est conservé.
4. Sans RAG, la **consigne anti-fabrication** est présente (pas de critère/seuil/prime/date inventés → redirection organisme certificateur).
5. Non-régression : l'agent Réglementation ne réclame plus `certification` seul (routage cédé à Normes).

Aucune API externe, inférence et RAG mockés. Objectif : couverture ≥ 80 %, suite verte.

## Hors périmètre (YAGNI)

- Pas d'outil externe ni d'appel réseau (contrairement à Météo/Prix).
- Pas de calcul de prime de certification (souveraineté + domaine agent Prix).
- Pas de composition multi-agents (Normes reste mono-agent, comme les autres).
