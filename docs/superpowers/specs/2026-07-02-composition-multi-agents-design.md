# Composition multi-agents — brancher la synthèse Reporting dans l'orchestrateur

> Design 2026-07-02. Comble le **dernier manque fonctionnel du socle V3** documenté
> dans `docs/agents_v3.md` : « `AgentReporting.synthetiser` n'est pas encore branché
> dans l'orchestrateur (dispatch mono-agent) ». 100 % code, aucune dépendance externe.

## Problème

L'orchestrateur route vers **un seul** agent (`routeur.meilleur` → `agent.traiter`).
L'agent Reporting sait pourtant **fusionner** plusieurs analyses (`synthetiser`), mais
il n'est jamais appelé ainsi : quand il est routé, il répond seul, sans rien à
synthétiser. Une question composite (« fais-moi un **bilan** météo **et** prix pour
Daloa ») ne mobilise donc pas plusieurs agents.

## Déclencheur (fan-out)

Un agent est un **synthétiseur** s'il expose une méthode `synthetiser` (duck-typing,
pas de nom en dur — un futur agent de synthèse marche sans changement). Règle :

> Si un agent synthétiseur est **présent dans le classement de routage** (score ≥ seuil),
> l'orchestrateur bascule en **composition** : les autres agents classés produisent des
> contributions, le synthétiseur les fusionne.

On teste la **présence** dans le classement, pas la position de tête : « bilan météo et
prix » fait souvent gagner Météo/Prix au score ; on veut quand même composer. Le mot de
synthèse (« bilan/rapport/synthèse ») suffit à déclencher le fan-out.

## Contributeurs (bornés pour la latence)

- Contributeurs = agents du classement **hors** le synthétiseur, triés par score,
  **plafonnés à `MAX_CONTRIBUTEURS = 2`**.
- Justification latence : l'inférence prod est **CPU (~15-46 s/génération)**. Composer
  = N générations séquentielles + 1 synthèse. Le plafond borne à **3 générations** au
  total (2 contributions + 1 synthèse), ~1-2 min. Acceptable car la synthèse est une
  demande **explicite** de l'utilisateur (« rapport/bilan »), pas le cas courant.
- RAG a un score plancher 0.4 (toujours ≥ seuil) → il y a **toujours** au moins une
  contribution. Repli défensif : si le classement ne contient que le synthétiseur, on
  prend l'agent de repli (`agent_defaut`).
- Parallélisme écarté : le serveur llama.cpp CPU traite une requête à la fois → un
  `asyncio.gather` se sérialiserait côté inférence sans gain. Séquentiel, plus simple.

## Flux (fan-in)

```
classement = routeur.classer(requete)           # liste ordonnée (déjà existante)
synth = premier agent du classement avec .synthetiser
si synth:                                        # COMPOSITION
    contribs = [a for a,_ in classement if a is not synth][:2]  ou [repli]
    reponse = synth.synthetiser(requete, [a.traiter(requete) for a in contribs])
sinon:                                            # MONO-AGENT (inchangé)
    agent = classement[0] ou repli
    reponse = agent.traiter(requete)
```

Puis **inchangé** : garde-fou de SORTIE sur le texte synthétisé, cache (tour unique),
enrichissement contact ANADER, journalisation. `synthetiser` agrège déjà les sources
(union) et retient la confiance **la plus basse** (prudence) — souveraineté préservée.

## Rate-limit

**Un seul** `hit_rate_limit` par requête (comme aujourd'hui), avant toute génération :
une demande utilisateur = une unité de quota, même si elle déclenche 3 générations.

## Streaming (`traiter_stream`)

La synthèse a besoin de **toutes** les contributions avant de produire → pas de vrai
token-par-token multi-agents dans cette itération. Quand un synthétiseur est présent,
on **calcule** la réponse composée puis on l'émet **en bloc** via `flux.evenements_token`
(même mécanique que les hits de cache / refus), suivie de l'événement final. Le endpoint
reste correct ; l'affichage progressif réel de la synthèse est une évolution ultérieure
(nécessiterait `synthetiser_stream`). Documenté comme limitation connue.

## Composants touchés

- **`api/app/application/orchestrateur.py`** :
  - `MAX_CONTRIBUTEURS = 2` (constante module).
  - `_est_synthetiseur(agent) -> bool` (`hasattr(agent, "synthetiser")`).
  - `_composer(requete, classement) -> AgentReponse` (fan-out + fan-in borné).
  - `traiter` : remplacer `meilleur()` + dispatch par `classer()` une fois, puis
    composition-ou-mono produisant `reponse` ; suite (étapes 5-8) inchangée.
  - `traiter_stream` : si synthétiseur présent → composer puis émettre en bloc ; sinon
    chemin streaming actuel inchangé.
- **`api/app/services/agents/agent_reporting.py`** : inchangé (`synthetiser` déjà prêt).

## Tests (`api/tests/agents/test_orchestrateur.py`, en TDD)

Ajouter un `_AgentSynthetiseur` espion (expose `synthetiser`, enregistre les
contributions reçues) et :
1. **Composition déclenchée** : reporting + météo + rag → `synthetiser` reçoit les
   contributions de météo et rag ; la réponse est celle du synthétiseur.
2. **Contributeurs plafonnés** : reporting + 3 spécialistes → au plus 2 contributions.
3. **Le synthétiseur n'est pas sa propre contribution** (exclu du fan-out).
4. **Repli** : reporting seul (aucun autre agent) → contribue l'agent de repli.
5. **Mono-agent préservé** : aucune régression sur les tests de routage existants
   (pas de synthétiseur → `agent.traiter`).
6. **Garde-fou de sortie** s'applique au texte synthétisé (dosage dans la synthèse → refus).
7. **Stream** : une requête de synthèse émet la réponse composée puis l'événement final.

Inférence/RAG mockés, aucun réseau. Objectif : suite verte, couverture ≥ 97 %.

## Hors périmètre (YAGNI)

- Pas de parallélisme des contributions (inutile sur inférence CPU mono-requête).
- Pas de planification multi-étapes (plan-act-observe) — le socle reste « plat ».

## Addendum (02/07) — correctif streaming anti-524 (post-déploiement v0.6.58)

**Problème constaté en prod.** La première version émettait la synthèse **en bloc**
(après toutes les générations) et laissait la composition s'exécuter sur le endpoint
**synchrone** `/v1/chat`. Résultat mesuré : une requête « bilan » enchaîne ~3 générations
CPU (~3 min) → **Cloudflare coupe à ~100 s → HTTP 524** (l'utilisateur reçoit une erreur).
Rollback prod v0.6.58 → v0.6.57.

**Correctif (livré).**
1. **`/v1/chat` synchrone** : la composition est **désactivée** — on retombe en mono-agent
   (le endpoint synchrone ne peut pas éviter le 524, il rend tout le corps d'un coup).
2. **`/chat/stream`** : composition avec **premier octet immédiat**. L'orchestrateur émet
   un événement `{"type":"progress"}` **avant** le fan-out (→ pas de 524), un heartbeat
   `progress` après **chaque** contribution (→ pas de time-out idle), puis **streame** la
   synthèse token par token via **`AgentReporting.synthetiser_stream`** (nouveau), filtrée
   par le garde-fou de sortie phrase par phrase. Sources/confiance dérivent des
   contributions via **`AgentReporting.agreger`** (aucune re-génération).
3. **Front** : le parseur SSE ignore les types d'événements inconnus (`token`/`done`/`error`
   seuls traités) → les `progress` gardent le flux vivant **sans polluer** la réponse
   affichée. Aucun changement front requis (une amélioration UX — afficher la progression
   comme statut éphémère — reste possible plus tard).

**Détection du synthétiseur** : `hasattr(agent, "synthetiser_stream")` (la méthode réellement
appelée en flux), pas `synthetiser`.
