# Plateforme agentique V3 — Architecture & cours

> Ce document a deux fonctions :
> 1. **Doc d'architecture** du socle agentique (orchestrateur + registre + routeur + agents + outils).
> 2. **Cours d'IA agentique** : chaque brique est expliquée par *le concept*, *les décisions de conception* et *le modèle mental* à retenir. Lis-le dans l'ordre — l'ordre des sections **est** la progression pédagogique.

## Carte mentale (le flux d'une requête)

```
                    requête (question, langue, historique, ip)
                                   │
                                   ▼
        ┌──────────────────────  ORCHESTRATEUR  ──────────────────────┐
        │ 1. fil_ancre        (anti-dérive multi-tours)               │
        │ 2. garde-fou ENTRÉE (cacao-only, centralisé) ──► refus ─────┼─► Conseil (ANADER)
        │ 3. ROUTEUR          (qui répond ? score peut_traiter)       │
        │ 4. rate-limit       (avant inférence, après routage)        │
        │ 5. dispatch ───►  AGENT  ───► OUTIL (météo/prix) ──► LLM     │
        │ 6. garde-fou SORTIE (vérifie la génération)                 │
        │ 7. journalisation   (trace + interaction_id)                │
        └──────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
                    Conseil (réponse + sources + confiance + disclaimer)
```

Couches (clean architecture) :
- `domain/agents.py` — **contrat** pur (aucune dépendance framework).
- `application/{registre,routage,orchestrateur,contexte}.py` — **orchestration** pure (testable sans réseau).
- `services/agents/*` et `services/outils/*` — **adaptateurs concrets** (agents et outils).

La frontière *contrat/orchestration pure* ↔ *adaptateurs concrets* est ce qui rend la plateforme extensible : **un nouvel agent n'est qu'un nouvel adaptateur.**

---

## 1. Le contrat d'agent — `domain/agents.py`

### Le concept
Un **agent = une capacité bornée derrière une interface stable**. Il déclare *ce qu'il sait faire* (routage), reçoit une *requête normalisée*, rend une *réponse normalisée*. Le reste du système ne connaît **que** cette interface, jamais l'implémentation. C'est l'**inversion de dépendance** (le « D » de SOLID) appliquée à l'agentique.

Quatre pièces :
| Pièce | Rôle |
|---|---|
| `AgentRequete` | entrée normalisée (question, langue, fil_ancre, historique, ip) — `frozen` |
| `AgentReponse` | sortie normalisée (texte, sources, confiance, agent, redirection) — `frozen` |
| `AgentPort` (Protocol) | contrat : `nom`, `description`, `mots_cles`, `peut_traiter()`, `traiter()` |
| `Outil` (Protocol) | contrat d'un outil invocable : `nom`, `invoquer(**kwargs)` |

### Les décisions
- **`dataclass(frozen=True)`** → requêtes/réponses **immuables**. Un agent ne peut pas modifier par surprise une donnée qu'un autre lira. Pilier de la fiabilité en async.
- **`Protocol` (typage structurel)** plutôt qu'une classe mère imposée → un agent est conforme *parce qu'il a les bonnes méthodes*, pas par héritage. Liberté maximale ; on ne piège pas les futurs agents.
- **`peut_traiter() -> float` (0..1)** → chaque agent **s'auto-évalue**. Il se décrit ; le routeur décide. On commence déterministe (mots-clés) : explicable, testable, souverain (aucun LLM pour router).
- **`invoquer(**kwargs)`** (et pas des arguments fixes) → absorbe la variabilité des outils (météo prend `localite`, prix ne prend rien). Un bon contrat anticipe la diversité des implémentations.

### Modèle mental
> Le contrat est la *constitution* de la plateforme. Tout le reste en dépend ; lui ne dépend de rien. Ajouter l'agent n°11 = écrire une classe conforme + l'enregistrer. Zéro refactor.

---

## 2. Le registre dynamique — `application/registre.py`

### Le concept
Un **annuaire d'agents** : on enregistre des instances, on les retrouve par nom ou énumération. C'est le **point d'extension n°1** : il rend la plateforme *ouverte à l'extension, fermée à la modification* (le « O » de SOLID).

### Les décisions
Pourquoi pas un simple `dict` ? On veut une frontière explicite avec 3 garanties :
1. **Refus des doublons** (`ValueError`) — deux agents `"meteo"` = un écrase l'autre silencieusement = bug.
2. **Énumération stable** (`tous()`, `noms()`) — le routeur balaie les agents.
3. **Observabilité** — on journalise chaque enregistrement (`structlog`).

### Modèle mental
> Le registre est la *prise électrique* du framework. Brancher un agent suffit à le rendre routable ; rien d'autre ne bouge.

---

## 3. Le routeur d'intention — `application/routage.py`

### Le concept
« *Qui* doit répondre à cette requête ? » Chaque agent s'auto-évalue (`peut_traiter`) ; le routeur **classe** par score décroissant et coupe sous un `seuil`. C'est la graine du *planner* des architectures multi-agents (ReAct, plan-and-execute), ici en version plate (un tour).

### Les décisions
- **Déterministe d'abord** — aucun appel LLM pour router : explicable, testable, souverain. L'interface (`classer`/`meilleur`) ne changera pas si on bascule plus tard vers un routage sémantique (embeddings).
- **Un classement, pas un seul gagnant** — certaines requêtes mobilisent plusieurs agents (« quel temps pour traiter, et à quel prix vendre ? »). Le routeur renvoie une liste ordonnée ; l'orchestrateur décide combien activer.

### Modèle mental
> Le routeur *note*, il ne *décide* pas seul. La décision finale (activer 1 ou N agents, repli) appartient à l'orchestrateur.

---

## 4. L'orchestrateur — `application/orchestrateur.py` (le cœur)

### Le concept
C'est le **control plane** (plan de contrôle) / la **boucle d'agent**. Tout le reste exécute ; lui **décide** : qui agit, dans quel ordre, sous quelles contraintes. Équivalent agentique de `ConseilService`.

Les 7 étapes — **l'ordre encode la sécurité et l'équité** :
```
1. fil_ancre          reconstruit l'intention réelle (multi-tours)
2. garde-fou ENTRÉE   refus AVANT tout agent
3. routage            choix de l'agent (repli RAG si rien)
4. rate-limit         AVANT l'inférence, APRÈS le routage
5. dispatch           agent.traiter()
6. garde-fou SORTIE   filtre la génération
7. journalisation     trace + interaction_id
```

### Les décisions (c'est ici qu'est l'expertise)
1. **Garde-fous CENTRALISÉS, pas par agent.** Point d'application unique de la politique (*policy enforcement point*). Le filtre « cacao uniquement » ne peut pas être oublié sur un futur agent : tout passe par l'orchestrateur. Souveraineté structurelle.
2. **Défense en profondeur : entrée ET sortie.** L'entrée bloque la *demande* interdite (sur le fil ancré → pas de contournement multi-tours). La sortie inspecte ce que l'agent a *réellement généré* (un LLM peut produire un dosage même sur une question anodine). Principe : **ne jamais faire confiance à la sortie d'un LLM sans la vérifier.**
3. **Rate-limit après le routage, avant l'inférence.** Un refus ne coûte rien (pas de génération CPU ~38 s) → il ne doit pas consommer le quota. On ne facture que le travail coûteux. Équité.
4. **Repli systématique (jamais d'impasse).** Routeur indécis → agent RAG par défaut. « Je ne sais pas router » ≠ « je ne réponds pas » : on dégrade vers le généraliste.

### Détails d'artisan
- **`dataclasses.replace`** pour ajouter l'`interaction_id` à un `Conseil` `frozen` (copie au lieu de mutation).
- **Renvoie l'entité `Conseil` existante** → tout l'aval V2 (router HTTP, DTO, disclaimer, streaming) marche sans changement. C'est ce qui permet le flag `agents_enabled` (bascule V2↔V3 transparente).

### Modèle mental
> L'orchestrateur est un **routeur + garde + journaliseur**. Dans les systèmes avancés, cette boucle « décider → agir → vérifier » se répète en cycles (plan-act-observe) avec mémoire. Notre version est plate (un cycle) ; la structure est identique, donc extensible vers du multi-étapes sans réécriture.

### Note DRY — `application/contexte.py`
`fil_ancre` (ancrage anti-dérive) et `texte_conversation` sont partagés entre `conseil_service` (V2) et `orchestrateur` (V3). Extraits dans `contexte.py` pour éviter la duplication.

---

## 5. Le squelette d'agent — `services/agents/base.py` + `agent_rag.py`

### Le concept
**Agentifier** une capacité existante = l'envelopper dans `AgentPort`. Avant d'écrire 4 agents qui font tous « appeler le LLM → extraire les sources → estimer la confiance → signer », on factorise cette mécanique dans `AgentBase`. C'est le pattern **Template Method** : la base définit le squelette (`_generer`), chaque agent ne fournit que sa spécificité (quel contexte injecter, comment scorer).

### Les décisions
- **`AgentBase` est optionnelle, pas obligatoire.** Le contrat reste un `Protocol` ; la base est un *confort* (DRY). On sépare *ce qu'on doit respecter* (contrat) de *ce qu'on peut réutiliser* (commodité). C'est ça qui garde le framework non-enfermant.
- **RAG = agent par défaut.** Généraliste ancré sur sources officielles → toujours un bon repli. Son `peut_traiter` renvoie un plancher modéré (0.4) : éligible partout, facile à battre par un spécialiste.

### Modèle mental
> Un agent concret = *le contexte qu'il sait fabriquer* + *le score qu'il s'attribue*. Le reste est mutualisé.

---

## 6. Le tool use — `services/outils/meteo.py` + `services/agents/agent_meteo.py`

### Le concept (le fondateur de l'agentique)
> Un chatbot **parle** (depuis sa mémoire figée). Un agent **agit** : il appelle des **outils** qui ramènent des données fraîches, puis raisonne dessus.

L'agent Météo : (1) appelle `OutilMeteo` → prévisions ; (2) **injecte ces faits dans le contexte** du prompt ; (3) le LLM raisonne sur des faits, pas sur sa mémoire (*grounding*). C'est le « function calling » des grands frameworks, mais explicite et déterministe → souverain et testable.

### Les décisions
- **Séparer l'OUTIL de l'AGENT.** L'outil *récupère la donnée* (I/O réseau, mockable, réutilisable) ; l'agent *raisonne dessus* (logique métier, sans réseau direct). Séparation I/O ↔ logique → on teste chacun isolément.
- **Port mockable (`MeteoPort`).** Aucun appel réseau en test ; la source (Open-Meteo, API nationale…) est interchangeable. **Aucun LLM tiers** — données factuelles uniquement (souveraineté).
- **Fail-soft.** Si l'API plante, l'outil renvoie `{}` au lieu d'exploser ; l'agent dégrade en conseil générique. *Un outil qui échoue ne fait jamais tomber l'agent.*

### Modèle mental
> Outil = *les yeux et les mains* de l'agent sur le monde réel. L'agent = *le cerveau* qui décide quoi en faire.

---

## 7. Réplication du pattern — `services/outils/prix.py` + `agent_prix.py`

### Le concept
L'agent Prix est le **jumeau** de l'agent Météo : même moule (outil + port mockable + injection de contexte), domaine différent. Sa valeur pédagogique : **prouver que le framework tient.**

> Le test d'un bon socle : le coût marginal d'un agent supplémentaire est *faible et constant*. Ajouter l'agent n°5..n°11 = recopier le moule en changeant le domaine.

Différence instructive : `OutilMeteo.invoquer` prend `localite`, `OutilPrix.invoquer` ne prend rien (prix national). Le contrat `invoquer(**kwargs)` absorbe les deux — **un choix de Task 1 qui paie ici.**

---

## 8. Synthèse multi-agents — `services/agents/agent_reporting.py`

*(À compléter à la livraison de la Task 8.)*

---

## Recette — Ajouter un agent (ex. Maladie) en 4 étapes

*(À compléter à la livraison de la Task 10.)*

---

## Garde-fous & souveraineté (rappel non négociable)
- Périmètre **cacao uniquement** : vivier/anacarde/médical/dosages → redirection ANADER. Décision Waopron juin 2026.
- Garde-fous **dans l'orchestrateur**, jamais par agent.
- **Aucun service externe** (OpenAI/Anthropic/Cohere) en production. Les outils appellent des *sources de données*, pas des LLM tiers, toujours derrière un port mockable.
- Disclaimer ANADER systématique (porté par l'entité `Conseil`).
