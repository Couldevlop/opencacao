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
        │ 3. clarification    (1er tour) ──► questions ───────────────┼─► Conseil
        │ 4. cache exact      (tour unique) ──► hit ──────────────────┼─► Conseil
        │ 5. ROUTEUR          (qui répond ? score peut_traiter)       │
        │ 6. rate-limit       (avant inférence, après routage)        │
        │ 7. dispatch ───►  AGENT  ───► OUTIL (météo/prix) ──► LLM     │
        │ 8. garde-fou SORTIE (vérifie la génération)                 │
        │ 9. enrichissement ANADER + journalisation                   │
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
3. clarification      1er tour : poser des questions plutôt que répondre à l'aveugle
4. cache exact        tour unique : sert une réponse cachée (instantané)
5. routage            choix de l'agent (repli RAG si rien)
6. rate-limit         AVANT l'inférence, APRÈS routage/cache
7. dispatch           agent.traiter()
8. garde-fou SORTIE   filtre la génération
9. enrichissement     contact ANADER local + journalisation
```

### Les décisions (c'est ici qu'est l'expertise)
1. **Garde-fous CENTRALISÉS, pas par agent.** Point d'application unique de la politique (*policy enforcement point*). Le filtre « cacao uniquement » ne peut pas être oublié sur un futur agent : tout passe par l'orchestrateur. Souveraineté structurelle.
2. **Concerns transverses centralisés (parité V2).** Clarification consultative, cache exact et enrichissement contact ANADER sont dans l'orchestrateur — comme les garde-fous, jamais dans les agents. Mutualisés avec la V2 via `application/conseil_commun.py` (le cache est même interopérable : une réponse pré-chauffée par la V2 est servie par la V3).
3. **Défense en profondeur : entrée ET sortie.** L'entrée bloque la *demande* interdite (sur le fil ancré → pas de contournement multi-tours). La sortie inspecte ce que l'agent a *réellement généré* (un LLM peut produire un dosage même sur une question anodine). Principe : **ne jamais faire confiance à la sortie d'un LLM sans la vérifier.**
4. **Rate-limit après routage/cache, avant l'inférence.** Un refus, une clarification ou un hit de cache ne coûtent rien (pas de génération CPU ~38 s) → ils ne consomment pas le quota. On ne facture que le travail coûteux. Équité.
5. **Repli systématique (jamais d'impasse).** Routeur indécis → agent RAG par défaut. « Je ne sais pas router » ≠ « je ne réponds pas » : on dégrade vers le généraliste.

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

### Le concept
Jusqu'ici un seul agent répond (routage *vers un* agent). L'agent Reporting **compose la sortie de plusieurs agents** (RAG + Météo + Prix) en une synthèse narrative. C'est le passage du **mono-agent au multi-agents** — le germe des architectures « agent superviseur ».

### Les décisions
- **Construit en dernier** : il *dépend* des autres. Il illustre qu'un agent peut consommer le travail d'agents pairs.
- **Agrégation prudente** : les sources des contributions sont unionnées sans doublon ; la confiance retenue est **la plus basse** des contributions (on ne surestime jamais une synthèse).
- **Fusion séquentielle simple** : le *fan-out / fan-in* est désormais **piloté par l'orchestrateur** (voir checklist, livré le 02/07) — contributions bornées à `MAX_CONTRIBUTEURS`, exécutées séquentiellement (l'inférence CPU traite une requête à la fois ; paralléliser ne gagnerait rien). L'exécution parallèle et le streaming incrémental de la synthèse restent des évolutions ultérieures.

### Modèle mental
> Le routage choisit QUI parle ; la synthèse fait PARLER ENSEMBLE. C'est la bascule du mono-agent vers le multi-agents.

---

## 9. Câblage derrière un flag — `config.py`, `api_deps.py`, adaptateur

### Le concept
Une plateforme agentique se met en service **progressivement**, derrière `agents_enabled` (OFF par défaut). La **composition racine** (`api_deps._construire_orchestrateur`) est le seul endroit où l'on assemble le graphe : registre → 4 agents → routeur → orchestrateur.

### La pièce clé : l'adaptateur `ConseilAgentique`
Le router POST passe par `get_dialogue_service` (sessions V2), qui appelle `conseiller()`/`conseiller_stream()` sur un `ConseilService`. L'orchestrateur expose `traiter()` — **même signature, même `Conseil` en retour**. On crée donc `ConseilAgentique`, un adaptateur qui présente l'orchestrateur **sous l'interface de `ConseilService`** (duck typing). Résultat : `DialogueSessionService` et le router restent **inchangés**, les sessions sont préservées, et `get_conseil_service` renvoie l'adaptateur quand le flag est ON.

### Les décisions
- **Feature flag** → bascule V2↔V3 sans risque, rollback instantané.
- **Composition root unique** → tout le câblage en un lieu ; le reste du code n'en sait rien.
- **Outils « indisponibles »** → Météo/Prix enregistrés avec une source neutre (`{}`) tant qu'aucune API réelle n'est branchée ; l'agent dégrade en conseil générique. Socle 100 % testable et déployable sans dépendance externe.

### Modèle mental
> Livrer sans casser : la V3 s'insère dans la V2 par un adaptateur, derrière un flag. Elle ne la remplace pas.

---

## 10. La parcelle — `models/parcelle.py`, `core/parcelles_store.py`, `services/parcelles.py`

*Chantier C1, livré le 28/07/2026. Spec : `docs/superpowers/specs/2026-07-28-v3-operationnelle-design.md` §5-6.*

### Le concept

Jusqu'ici, l'objet central de la plateforme était **la question**. Un producteur demandait, un agent répondait, et le fil s'effaçait. La V3 introduit un objet qui **persiste et s'enrichit** : la **parcelle**. Elle a une géométrie, une superficie, une direction régionale de rattachement, un historique de captures. Les agents cessent de répondre dans le vide : ils répondront *à propos de quelque chose*.

C'est le basculement de l'assistant vers l'instrument. Un chat n'a pas de mémoire du terrain ; une parcelle en est la mémoire.

### Les décisions

**Quatre modalités de capture, deux contrats serveur.** Photos, vidéo, parcours GPS, parcours + vidéo. L'API n'expose pourtant que **deux** contrats — un jeu d'images géoréférencées, une trace de points — et le navigateur fait le reste : il échantillonne la vidéo (1 image / 2 s, plafond 12) et redimensionne à 1024 px **avant** tout envoi. Ce n'est pas une optimisation, c'est une contrainte de terrain : téléverser une vidéo de 100 Mo sur un réseau mobile ivoirien échouera, et une photo de téléphone moderne fait 4000 px.

**L'étage 0 de la cascade de vision ne mobilise aucun modèle.** Netteté (variance du laplacien) et exposition sont calculées par le navigateur, qui possède déjà les pixels décodés ; le serveur valide **les en-têtes** PNG/JPEG en Python pur — ce qui donne les dimensions réelles *et* sert de contrôle de sécurité, puisque ces octets partent sur le disque. Une image refusée reçoit un **conseil de reprise en français simple** (« approchez-vous de la cabosse », « tournez-vous dos au soleil »), jamais un code d'erreur.

**Le nom de fichier dérive du SHA-256 du contenu**, jamais d'une donnée du client : aucune traversée de chemin n'est possible, et deux téléversements identiques ne consomment qu'un fichier. Une image refusée est **quand même consignée** en métadonnées avec son motif — le producteur doit voir ce qui a été rejeté — mais ses octets ne touchent pas le disque.

**La superficie est calculée, jamais saisie**, sur coordonnées projetées localement (`services/geometrie.py`) — jamais en degrés bruts : un degré de longitude ne vaut pas un degré de latitude. Une géométrie est refusée, avec un motif lisible, si un point sort de la Côte d'Ivoire, si le tracé se coupe lui-même, ou si la superficie sort de l'intervalle 0,1–50 ha.

**Persistance sur le moule de `core/sessions.py`** : `sqlite3` de la bibliothèque standard, migrations par `PRAGMA user_version`, `asyncio.to_thread`, mode WAL, et surtout **initialisation tolérante aux pannes** — si `/data` est inaccessible, l'API démarre quand même, les parcelles sont indisponibles et le chat continue. Les images ne sont pas en base : seule leur empreinte l'est.

**Cloisonnement plus strict que celui des sessions.** Les conversations V2 tolèrent un `X-Device-Id` absent et retombent dans un espace « hérité » partagé — compatibilité assumée. Les parcelles l'**exigent** (400 sinon) : une parcelle porte le polygone GPS exact de la plantation d'un producteur, et cet espace partagé serait une fuite. Les parcelles sont neuves, aucun client hérité à ménager.

### Modèle mental

> Le chat répond à une question et l'oublie. La parcelle, elle, **accumule**. C'est sur elle que se grefferont l'analyse visuelle (C2) et le dossier de traçabilité (C3) — deux chantiers qui n'auraient aucun objet sans elle.

---

## 11. La cascade de vision — `models/constat.py`, `application/constat_visuel.py`, `curation/revue_constats.py`

*Chantier C2, livré le 29/07/2026. Spec : `docs/superpowers/specs/2026-07-28-v3-operationnelle-design.md` §7.*

### Le concept

Un producteur photographie une cabosse tachetée et demande ce qu'elle a. La réponse honnête n'est pas un diagnostic : c'est un **constat**. Le système décrit ce qu'il observe, dit à quel point il en est sûr, croise avec ce qu'il sait de la parcelle — puis renvoie vers un agent ANADER.

La distinction n'est pas de la prudence rhétorique. Nommer une maladie sur photo, c'est engager un traitement ; se tromper, c'est faire pulvériser un produit inutile sur une plantation qui n'en a pas les moyens. Tant qu'aucun jeu de données ivoirien n'existe pour mesurer le taux de rappel par classe, l'étiologie reste fermée. **Constat, pas diagnostic** — et ce n'est pas une consigne au modèle, c'est la structure du code : le module `models/constat.py` ne porte aucun nom de maladie, aucun produit, aucune posologie.

La cascade compte sept étages, dont **cinq sont livrés** : recevabilité de l'image (étage 0, livré en C1), tri d'organe (1), fusion contextuelle (4), rédaction du constat (5), file de revue (6). Les étages 2 (localisation des lésions) et 3 (étiologie) sont **délibérément absents**.

### Les décisions

**Le port de vision est mockable, donc tout C2 se teste sans GPU.** `VisionPort` (`domain/ports.py`) déclare `decrire(images, consigne) -> str | None`. Seul le service du modèle a besoin d'une carte graphique ; la cascade, ses garde-fous et son endpoint se construisent et se vérifient sur un poste ordinaire. C'est ce qui a permis d'attaquer le chantier le plus risqué **en premier**, plutôt que de le repousser jusqu'au moment où il aurait été trop tard pour en sortir.

**Vision indisponible → aucun constat.** Pas une description approximative, pas un « il semblerait que » : `None`, et l'API répond 503 avec une phrase qui oriente vers l'ANADER. Le pattern « contexte vide → fabrication » a déjà coûté un correctif sur les agents (v0.6.48) ; il ne revient pas par la vision. En profil CPU — celui de la production actuelle — c'est la source neutre `VisionIndisponible` qui est branchée, et elle se déclare telle quelle.

**Sortie compromise → rejet, jamais réécriture.** `guardrails.contient_diagnostic` vérifie la description du modèle de vision **et** le constat rédigé. S'il y trouve un nom de maladie, un produit ou un dosage, le constat est jeté. On ne rafistole pas une sortie qui a franchi un interdit : une réécriture laisserait croire que la consigne a tenu. Le vocabulaire phytosanitaire existant est réutilisé tel quel — « appliquez un fongicide » est déjà une prescription, même sans chiffre.

**Le levier est la consigne, pas le plafond de tokens** — leçon acquise en juillet sur le dialogue naturel. Mais on ne fait pas confiance au modèle pour la respecter : la consigne interdit explicitement, **et** le garde-fou de sortie vérifie. Ceinture et bretelles, parce que le coût d'une seule sortie fautive est un producteur qui traite à tort.

**La météo dégrade la confiance, elle ne la conforte jamais sans donnée.** Une observation évoquant une atteinte humide après trois semaines sèches est douteuse : l'étage 4 ne conclut rien, il **descend d'un cran** et écrit pourquoi. Relevé de pluie absent ? Dégradation aussi — l'absence de donnée n'est pas une confirmation. Les facteurs rédigés n'emploient jamais un nom de maladie ; un test le vérifie.

**Analyser est idempotent.** Produire un constat coûte une génération de vision *et* une génération de conseil, soit des dizaines de secondes de CPU. Une capture déjà analysée se relit au lieu d'être recalculée, et la route porte un quota dédié (3/min/appareil) distinct du débit général : partager le budget d'un simple `GET` laisserait une poignée de requêtes saturer l'inférence.

**Le disclaimer ANADER est structurel.** Il est porté par le schéma de réponse, pas par la consigne au modèle. Un constat qui « oublierait » d'orienter vers l'agent n'existe pas.

### L'étage 6 — ce qui fait vraiment la différence

Chaque constat part en **file de revue**. Un agent ANADER confirme, corrige ou rejette ; la correction est persistée et alimente un export JSONL. C'est ce fichier qui deviendra le jeu de données ivoirien — celui qui, un jour, ouvrira les étages 2 et 3.

Le retournement mérite d'être vu : la faiblesse du système — un modèle qui peut se tromper — devient son moteur. Il s'améliore **parce qu'il est utilisé**, et la précision annoncée cesse d'être une promesse pour devenir une mesure, puis une publication.

Deux conséquences de sécurité, toutes deux traitées : ces routes voient les constats de **tous** les producteurs, elles vivent donc derrière l'authentification de la console (fail-closed — un mot de passe vide rend la console indisponible, il ne l'ouvre pas). Et la correction saisie par l'agent passe **les mêmes garde-fous que tout le reste** : elle devient une étiquette d'entraînement, une correction qui nomme une maladie empoisonnerait le jeu de données à sa source. L'export est minimisé : ce qui est observé, jamais chez qui — ni appareil, ni parcelle, ni coordonnée.

### Ce que ce chantier ne livre pas, délibérément

**Étages 2 et 3.** Ils exigent un jeu de données ivoirien qui n'existe pas encore — c'est précisément l'étage 6 qui va le construire. Le pré-diagnostic s'ouvrira au franchissement d'un seuil de rappel par classe (0,90 proposé sur pourriture brune et swollen shoot), **jamais à une date**.

**La porte de sortie reste ouverte.** Si le VLM se révèle inutilisable sur des photos ivoiriennes, `VISION_ENABLED` reste à `false`, C1, C3 et C4 se présentent sans lui, et la démonstration tient debout. La décision se prend une semaine avant l'événement, sur essai réel — pas la veille, et pas sans avoir essayé.

### Modèle mental

> La cascade ne cherche pas à savoir **ce qu'a** la plante. Elle cherche à dire **ce qu'elle voit**, avec quelle confiance, et à qui s'adresser ensuite. Chaque étage peut refuser de conclure ; aucun ne peut inventer. Ce qu'un agent humain corrige aujourd'hui est ce que le modèle saura demain.

---

## 12. L'atelier de livrables — `application/redaction.py`, `services/gabarits.py`, `services/rendu/`

*Chantier C3, livré le 29/07/2026. Spec : `docs/superpowers/specs/2026-07-28-v3-operationnelle-design.md` §8.*

### Le concept

Un bailleur ne lit pas un chat. Il lit une **étude** : un document qui a un plan, des tableaux, une bibliographie, et surtout qui dit **d'où vient chaque chiffre**. L'atelier produit ce document — étude de filière, dossier de parcelle, bulletin régional — en Markdown, Word, Excel et PowerPoint.

Le retournement à comprendre : ce n'est pas « le chat, mais en plus long ». C'est un objet différent, avec une exigence que le chat n'a pas — la **provenance vérifiable** — et un registre que le corpus ne connaît pas.

### Les décisions

**Le modèle n'écrit jamais un titre.** L'analyse du corpus du 28/07 est sans appel : 10 000 paires, réponses de 583 caractères en médiane, 1 201 au maximum, et **0,0 %** de titres, de puces, de listes ou de tableaux. Un 8B qui n'a jamais lu de document de 30 000 caractères ne peut pas en écrire un d'un seul jet. Mais il peut écrire quarante paragraphes de 700 caractères, ce qui est le même document. La structure vient donc du **gabarit YAML**, la prose du modèle. Le découpage par section n'est pas seulement une parade au time-out edge : *c'est ce qui rend l'étude possible*.

**Ajouter un livrable est un fichier YAML, pas du code.** Même discipline que « ajouter un agent = un adaptateur ». Le chargeur valide ce que le moteur ne pourra plus rattraper : une section sans titre resterait sans titre, une source inconnue serait ignorée en silence. Et parce que l'identifiant vient d'une requête HTTP, la sélection passe par une liste blanche calculée depuis le disque — jamais un chemin assemblé avec une donnée client.

**D4 — une section sans source ne mobilise pas le modèle.** Elle rend un **constat de lacune** qui dit ce qui manque et ce qu'il faudrait fournir. Ce n'est pas une dégradation, c'est la règle : générer sans contexte est exactement la fabrication qui a coûté un correctif en v0.6.48. Un test vérifie que l'inférence n'est **pas appelée** dans ce cas — c'est là que se joue la garantie, pas dans le texte produit.

**Aucune affirmation sans source, et c'est vérifié.** Chaque `Affirmation` porte source, date, méthode, confiance et l'empreinte du passage d'origine. `affirmations_sans_source` est le filet, et il ne se laisse pas contourner par un blanc, un tiret ou un « n/a ». Un tableau de provenance figure en annexe de tout livrable, et en **feuille dédiée** dans l'export Excel — c'est là qu'un auditeur voudra trier.

**Le registre analytique se joue sur deux fronts, pas un.** Le corpus est intégralement en registre *conseil au producteur* (« rendez-vous auprès de votre agent ANADER ») : sollicitée sur une section d'étude, la LoRA s'adresserait au producteur et renverrait vers l'ANADER — faux dans un document destiné à un bailleur. Un prompt système dédié redresse le registre… mais l'en-tête de contexte par défaut, injecté dans le tour **utilisateur**, disait précisément « oriente vers l'ANADER ». Le tour utilisateur étant plus proche de la génération, c'est lui que le modèle suivait : le redressement n'était appliqué qu'à moitié. `build_messages` accepte donc un en-tête de contexte et un libellé de demande, et la rédaction passe les siens.

**Les garde-fous du conseil ne sont pas ceux du livrable.** Distinction arbitrée le 29/07. Ce qui ne doit **jamais** être produit — dosage, avis médical, autre culture, diagnostic sur image — reste refusé, y compris sur le *sujet*, qui atterrit dans le titre sans jamais passer par le modèle et échapperait sinon à tout contrôle. Mais ce qui relève de *à qui l'assistant s'adresse* ne s'applique pas : un producteur à Korhogo est redirigé parce qu'on n'y cultive pas de cacao, alors qu'une **étude** sur la limite nord de la ceinture cacaoyère, ou sur la transformation locale, est un travail d'analyse légitime — c'est même ce qu'un bailleur commande. Même raisonnement pour `contient_diagnostic`, verrou du constat visuel : citer une maladie depuis une source n'est pas la diagnostiquer sur une photo.

**Chaque format porte SES garanties, sans les faire fuir dans les autres.** Préfixer d'une apostrophe une valeur commençant par `=` neutralise l'injection de formule dans un tableur (CWE-1236 — `openpyxl` n'échappe rien, et la cellule serait évaluée à l'ouverture chez le bailleur) et corromprait le Markdown. C'est pourquoi la parade vit dans l'adaptateur et non dans le domaine. Les trois formats binaires purgent par ailleurs les caractères de contrôle : un octet nul venant du modèle produit un XML invalide, donc un fichier que le destinataire ne peut pas ouvrir — le genre de défaut qui se découvre en démonstration.

**Le job survit au redémarrage.** Une étude représente 10 à 30 générations : le synchrone est exclu, et l'edge coupe de toute façon vers 100 s. Le job est persisté, exécuté en flux, et **le premier événement part avant toute génération** — c'est ce qui évite le 524. Un job resté inachevé après un redémarrage est orphelin : personne ne le reprendra, et le laisser ainsi ferait attendre un client indéfiniment. On l'assainit au démarrage.

**On demande un document, on ne le configure pas.** Arbitrage du 30/07, après une première interface à cases à cocher : personne ne remplit un formulaire pour demander une étude — on l'écrit. L'écran est donc une phrase libre, et `application/intention_rapport.py` la résout en un couple *(gabarit, sujet)*. Trois choix s'y jouent. La résolution est **déterministe** : faire trancher au modèle « de quel type de document s'agit-il ? » coûterait une génération complète *avant* la première section, pour une question à laquelle un vocabulaire déclaré répond en microsecondes. Le vocabulaire vit dans les gabarits (`declencheurs:`), donc ajouter un livrable reste un fichier YAML — y compris pour être reconnu à l'oral. Et ce qui n'est pas certain **n'est pas deviné** : une demande ambiguë ressort avec ses candidats et un statut 200, à charge pour l'écran de poser *une* question — la même doctrine de clarification que le reste d'OpenCacao. Le sujet est enfin dégagé de ce qui ne fait que renommer le type : « un bulletin pour la région de Daloa » donne *Daloa*, parce que « Bulletin régional — la région de Daloa » bégaie.

**La répétition entre sections était un défaut de COLLECTE, pas de génération.** Cinq sections d'une étude déclarent `rag` ; interrogées avec le seul sujet, elles recevaient les mêmes passages, et il ne restait au modèle qu'à inventer la différence. Aucun réglage de prompt n'aurait corrigé cela. Chaque section interroge donc le corpus avec sa propre requête — titre, consigne et sujet. Symétriquement, un outil (prix, météo) n'est **pas** réinterrogé par section : le prix officiel est le même au chapitre 1 et au 5, et l'outil continue de recevoir le *sujet nu*, jamais le titre, parce que la détection de localité lit dedans.

Mais la correction naïve aurait aggravé le mal. Le recouvrement lexical du RAG (F9) a pour dénominateur le nombre de mots de la requête : passer de 4 mots à 15 fait tomber le meilleur recouvrement du corpus de 0,75 à 0,27, sous le seuil de 0,5 — mesuré sur l'index de production, **plus aucun des 10 021 passages n'était éligible par la voie lexicale**, celle-là même qui rattrape les termes rares (maladie, variété, nom de source). Le canal **dense** se raffine donc par section, le canal **lexical** reste ancré sur le sujet court. Deux entrées, une seule récupération. Un défaut de ce genre ne se voit pas en test : il se serait manifesté en production par une hausse silencieuse du taux de lacunes.

### Modèle mental

> Le chat répond. L'atelier **produit une pièce** — un objet qui sortira de la plateforme, sera relu par un tiers et devra tenir devant lui. D'où trois obsessions que le chat n'a pas : *ce document dit d'où vient chacun de ses chiffres, il ne prétend rien qu'il ne puisse sourcer, et il ne fait rien d'inattendu sur la machine de celui qui l'ouvre.*

---

## Recette — Ajouter un agent en 4 étapes (appliquée à l'agent EUDR)

C'est l'aboutissement du socle : l'extensibilité prouvée. L'**agent n°5 — Réglementation EUDR** a été ajouté en suivant exactement cette recette :

1. **Écrire l'agent** — `api/app/services/agents/agent_reglementation.py` héritant d'`AgentBase`, avec `nom="reglementation"`, `mots_cles` (eudr, déforestation, traçabilité, export…), `peut_traiter()` (routage par mot entier), `traiter()` (préfixe un cadrage EUDR au contexte RAG).
2. **(Si besoin) un outil** — non nécessaire ici : l'agent réutilise le récupérateur RAG. (Pour un agent à données externes, on ajouterait `services/outils/<x>.py` + un port mockable, sur le moule de `meteo.py`/`prix.py`.)
3. **L'enregistrer** — **une seule ligne** dans `_construire_orchestrateur` (`api_deps.py`) : `registre.enregistrer(AgentReglementation(inference, rag=rag))`.
4. **Tester** — `api/tests/agents/test_agent_reglementation.py` (routage + cadrage injecté), en TDD.

**Aucune autre modification.** L'orchestrateur, le registre et le routeur sont restés intacts — preuve concrète d'« ouvert à l'extension, fermé à la modification ». Les agents suivants suivront le même moule.

> **Agent n°6 — Normes** (`agent_normes.py`) a été ajouté par la même recette : référentiels de durabilité/qualité du cacao (Rainforest Alliance, Fairtrade, agriculture biologique, ISO, ARS 1000). Frontière nette avec l'agent Réglementation, qui couvre l'accès marché *contraignant* (EUDR) : les mots-clés `certification`/`durabilité` ont été confiés à Normes (retirés de Réglementation) pour un routage sans ambiguïté. Même garde-fou de souveraineté que l'EUDR : sans document RAG, aucun critère/seuil/prime/date inventé → redirection vers l'organisme certificateur, la coopérative ou l'ANADER.

> **Agent n°7 — Satellite** (`agent_satellite.py`) : constats d'alertes de déforestation **Global Forest Watch** (alertes intégrées GLAD+RADD, dataset `gfw_integrated_alerts`) autour de la position du producteur — coordonnées GPS trouvées dans le fil (bornées Côte d'Ivoire) prioritaires, sinon localité nommée géocodée, sinon demande de position ; tampon ~1 km — croisés au contexte RAG EUDR. **Jamais de certification de conformité** : l'agent constate des faits datés et oriente vers le Conseil Café-Cacao/ANADER. Frontière de routage : `déforestation`/`géolocalisation` lui sont confiés (retirés de Réglementation, comme certification→Normes). Source validée le 05/07/2026 (couverture CI discriminante : 0 alerte en ville, 1 055 en lisière Taï ; fraîcheur ~2 semaines ; latence ~3 s) ; particularités d'API encodées : redirection 307 sur `latest`, pas de `MAX()` nu (GROUP BY), alias SQL ignorés. Clé `GFW_API_KEY` en **Secret K8s** (jamais en ConfigMap), **expire le 30/06/2027** ; sans clé, source neutre → consigne explicite, aucun statut inventé.

> 📄 Une version Word détaillée de ce cours existe : `docs/Documentation_Socle_Agentique_V3.docx` (régénérable via `python scripts/build_doc_agentique.py`).

---

## Checklist d'activation — **TOUT EST LIVRÉ, agents ACTIFS en production**

La plateforme est **active en production** (`AGENTS_ENABLED=ON` depuis la v0.6.41 du 30/06/2026) ; le flag reste un interrupteur de repli instantané vers la V2. Revue complète des 6 agents vérifiée en prod le 05/07/2026 (v0.6.63). État :

**✅ Fait (parité V2 dans l'orchestrateur)**
- **Enrichissement contact ANADER + clarification consultative** : l'orchestrateur applique désormais `clarification.analyser` (avant dispatch) et `conseil_commun.enrichir_contact` (sur chaque réponse), comme la V2. Mutualisé dans `application/conseil_commun.py`.
- **Cache exact de réponses** : `get_cached`/`set_cached` branchés (tour unique), sérialisation partagée avec la V2 → le pré-chauffage redevient utile et la latence est préservée.
- **Vrai streaming token-par-token** : `Orchestrateur.traiter_stream` streame les fragments de l'agent avec **garde-fou de sortie phrase par phrase** (aucun dosage diffusé), puis enrichissement + événement final. Mutualisé dans `application/flux.py` (`FiltreSortie`, `evenement_final`). L'adaptateur `conseiller_stream` y délègue → l'UI web a un affichage progressif identique à la V2. `AgentBase` expose `traiter_stream` (chaque agent ne définit que `_contexte`).

**✅ Complété depuis l'activation (items historiquement listés « avant ON »)**
- ~~**Cache sémantique**~~ **LIVRÉ (02/07)** : branché dans l'orchestrateur (parité V2). Après un miss exact (tour unique), l'orchestrateur vectorise la question et sert un voisin sémantiquement proche (cosinus ≥ seuil) validé par un **garde-fou lexical**. Logique extraite dans **`application/cache_semantique.py`** (`CacheSemantique`), **partagée** par `ConseilService` (V2) et l'orchestrateur (V3) — une seule source de vérité. Inerte si le service d'embeddings est absent (exact-match seul).
  - **Correctif 05/07 (v0.6.63, vécu prod)** : le **routage précède désormais le cache sémantique**, et une **intention de synthèse** (synthétiseur au classement) ne consulte ni n'alimente l'index sémantique. Sans cela, « bilan météo+prix » était servi par la seule réponse météo cachée (le seuil lexical prod abaissé à 0.3 laissait passer ce voisin inter-intentions) et la composition était court-circuitée. Le seuil 0.3 est conservé : le rappel des vraies paraphrases est intact.
- ~~**Sources météo/prix réelles**~~ **LIVRÉ (30/06, v0.6.41)** : `outils/meteo_openmeteo.py` (Open-Meteo — géocodage + précipitations 24 h) et `outils/prix_campagne.py` (prix bord-champ officiel du Conseil du Café-Cacao, configuré par campagne). Ports mockables conservés (aucun appel réseau en test) ; `MeteoIndisponible`/`PrixIndisponible` restent les replis fail-soft.
- ~~**Composition multi-agents**~~ **LIVRÉ (02/07)** : `AgentReporting.synthetiser` est branché dans l'orchestrateur. Déclencheur = présence d'un agent **synthétiseur** (duck-typing `hasattr(agent, "synthetiser_stream")`) dans le classement de routage ; les autres agents classés produisent des contributions (fan-out, plafonné à `MAX_CONTRIBUTEURS=2`), le synthétiseur les fusionne (fan-in). Séquentiel (inférence CPU mono-requête).
  - **Réservé au flux `/chat/stream`.** Une composition = jusqu'à 3 générations CPU (~3 min) → dépasse le time-out edge Cloudflare (~100 s) sur une réponse **synchrone** → **524** (vécu en prod). Correctif : (1) sur `/v1/chat` synchrone, on **retombe en mono-agent** (pas de composition) ; (2) sur `/chat/stream`, un événement `progress` est émis **immédiatement** (premier octet < 1 s → pas de 524), un heartbeat suit **chaque** contribution, puis la synthèse est **streamée token par token** (`AgentReporting.synthetiser_stream` ; sources/confiance via `agreger`). Le front ignore les types d'événements inconnus → `progress` garde le flux vivant sans polluer la réponse.

> Le routage déterministe reste volontairement conservateur : sans signal fort (mot climatique/marché en **mot entier**), on retombe sur le RAG (généraliste ancré). Un routage sémantique (embeddings) est la prochaine étape pour lever cette prudence.

---

## Garde-fous & souveraineté (rappel non négociable)
- Périmètre **cacao uniquement** : vivier/anacarde/médical/dosages → redirection ANADER. Décision Waopron juin 2026.
- Garde-fous **dans l'orchestrateur**, jamais par agent.
- **Aucun service externe** (OpenAI/Anthropic/Cohere) en production. Les outils appellent des *sources de données*, pas des LLM tiers, toujours derrière un port mockable.
- Disclaimer ANADER systématique (porté par l'entité `Conseil`, et par le schéma `ConstatReponse` côté vision).
- **Constat, pas diagnostic** : aucune sortie ne nomme une maladie, un ravageur, un produit ni une dose. Vaut pour le modèle de vision, pour le constat rédigé **et** pour la correction saisie par un agent ANADER en revue — même garde-fou, mêmes tests. Une sortie fautive est **rejetée**, jamais réécrite. L'étiologie s'ouvrira sur un seuil de rappel mesuré, jamais sur une date.
