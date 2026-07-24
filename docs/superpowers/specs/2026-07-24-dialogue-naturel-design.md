# Dialogue naturel hybride — alignement conversationnel

**Date** : 2026-07-24
**Statut** : design validé, à implémenter

## Objectif

Rendre l'expérience conversationnelle d'OpenCacao **naturelle sur la quasi-totalité des
questions** — proche de ce qu'un producteur ressent avec un assistant grand public — tout
en restant **à la mesure de l'infrastructure** (CPU/GGUF, Ministral-8B, ~16 tok/s) et sans
sacrifier la souveraineté (garde-fous cacao-only, anti-fabrication, redirection ANADER).

## Diagnostic

Le caractère « robotique » vient de deux endroits précis :

1. **`clarification.py`** (couche globale, étape 2 du pipeline, avant routage) intercepte
   les questions de diagnostic (symptôme, traitement, rendement, fertilisation, plantation)
   et renvoie des **puces scriptées identiques** à chaque fois (0 s, aucune inférence).
2. **`SYSTEM_PROMPT`** ordonne « Sois bref : 10 phrases maximum, va droit au but, sans
   rappel ni reformulation » — un registre volontairement sec.

**Constat clé issu de l'architecture agentique (V3) :** le bon pattern existe déjà. Les
agents Météo et Prix, quand il leur manque une information (la ville), ne renvoient pas un
texte figé : ils passent une **consigne** au modèle (« demande la localité ») qui formule
la question **naturellement, dans le fil**, en respectant la souveraineté. Ce pattern
« garde-fou déterministe (l'agent sait qu'il manque une info) + formulation naturelle par
le modèle » est **déjà en prod et validé**. Le maillon incohérent est le `clarification.py`
global scripté.

Le présent design **généralise ce pattern d'agent** au reste du dialogue. Il n'invente rien :
il harmonise.

## Décisions cadrées

| Question | Décision |
|---|---|
| Niveau de changement | **Hybride** : le modèle mène la formulation ; les garde-fous déterministes restent en filet. |
| Déclenchement des clarifications | **Gate déterministe conservé** (détection de thème + anti-boucle) ; **formulation par le modèle**. |
| Alignement sur les agents | **Harmoniser sans toucher à l'ordre du pipeline** (durement gagné). Pas de réordonnancement, pas de nouvelle méthode sur les 7 agents. |
| Rollback | **Drapeau de config** `dialogue_naturel_enabled` (bascule sans rebuild) **+** version connue-bonne 0.6.70. |

## Composants

### 1. `clarification.py` — séparer décision et formulation

- Extraire `detecter_theme(question, historique) -> str | None` : la **détection de thème et
  l'anti-boucle ne changent pas** (mêmes règles, mêmes 17 tests). Renvoie `"symptome"`,
  `"traitement"`, `"rendement"`, `"fertilisation"`, `"plantation"` ou `None`.
- Conserver `analyser()` (texte scripté) tel quel : c'est le **comportement de repli** quand
  le drapeau est à `false`.
- Ajouter une table `thème -> consigne` : pour chaque thème, l'information à recueillir,
  formulée comme une instruction courte au modèle (pas comme des puces figées). Ex. pour
  `symptome` : « il te manque la partie atteinte et depuis quand ; pose UNE question brève et
  naturelle pour l'obtenir, sans conseiller encore ».
- La logique « localité déjà citée → ne pas redemander » est conservée.

### 2. Génération de la question de clarification (naturelle)

- Quand le drapeau est actif **et** qu'un thème est détecté, au lieu de renvoyer un texte
  figé, on appelle le modèle avec : `SYSTEM_PROMPT` réchauffé + la consigne du thème +
  l'historique, en **`max_tokens` borné (~80)** pour rester rapide.
- Coût : **~3-4 s** au lieu de 0 s instantané. C'est le prix du naturel, borné. Les réponses
  complètes ne changent pas de coût. **Aucun tour supplémentaire** n'est ajouté : même nombre
  d'échanges, seulement mieux formulés.
- En flux (`traiter_stream` / `conseiller_stream`), la question s'affiche **au fil de l'eau**.
- Encapsulé dans un helper partagé (`conseil_commun.py`) pour éviter la duplication entre les
  4 points d'appel (V2 `conseil_service` sync+stream, V3 `orchestrateur` sync+stream).

### 3. `prompts.py` — réchauffer le ton

- Remplacer « Sois bref : 10 phrases maximum, va droit au but, sans rappel ni reformulation »
  par un registre de conseiller chaleureux : « parle simplement et avec bienveillance à un
  producteur, comme un agent ANADER sur le terrain ; reste concis (~6-10 phrases), sans
  bavardage ni remplissage ».
- **Toutes les autres règles restent mot pour mot** : cacao-only, anti-fabrication (jamais de
  source/date/chiffre inventés), redirection ANADER, jamais de dosage, jamais de numéro
  inventé, cohérence de sujet et résolution des références.
- Le drapeau sélectionne entre `SYSTEM_PROMPT` (réchauffé) et `SYSTEM_PROMPT_STRICT`
  (l'actuel, conservé pour le repli).

### 4. Consignes d'agents (Météo, Prix) — légère harmonisation

- Déjà naturelles ; on réchauffe uniquement leur formulation pour un ton homogène. Touche
  minimale, aucune logique modifiée.

### 5. Drapeau de configuration

- `dialogue_naturel_enabled: bool = True` dans `config.py`, injecté dans les services.
- Variable d'environnement `DIALOGUE_NATUREL_ENABLED` dans le ConfigMap `api-config`.
- À `false` : clarification scriptée + `SYSTEM_PROMPT_STRICT` → comportement d'aujourd'hui à
  l'identique.

### 6. Garde-fous / refus — inchangés

Les refus (`hors_ci`, `transformation`, médical, dosage, image, zone Nord…) restent
**déterministes et instantanés**. Un garde-fou ne doit jamais dépendre du modèle. Leur
formulation est déjà polie ; on n'y touche pas.

## Pipeline (ordre inchangé)

```
0. progression (flux)
1. garde-fous d'entrée (refus déterministe)      ← inchangé
2. clarification (naturelle si drapeau on)        ← MODIFIÉ (formulation)
3. cache exact
4. routage d'intention
5. cache sémantique
6. dispatch agent (auto-clarification naturelle)  ← déjà naturel, ton harmonisé
7. rate-limit
8. agent.traiter
9. garde-fou de sortie                             ← inchangé
10. cache + index + journalisation
```

## Tests (TDD)

- `detecter_theme` : les 17 cas de détection existants passent (détection inchangée).
- Drapeau **on** : le chemin de clarification construit une génération modèle contenant la
  consigne du thème ; on vérifie la **construction du message** (pas la sortie du modèle) et
  le plafond `max_tokens`.
- Drapeau **off** : comportement scripté strictement identique (non-régression).
- `SYSTEM_PROMPT` réchauffé : contient encore **toutes** les règles garde-fous (assertions sur
  les phrases clés) ; `SYSTEM_PROMPT_STRICT` inchangé.
- Parité V2 (`conseil_service`) et V3 (`orchestrateur`), sync et flux.

## Rollback

- **Doux** : `DIALOGUE_NATUREL_ENABLED=false` dans le ConfigMap + redémarrage API (~15 s),
  sans reconstruire d'image.
- **Dur** : le changement sort en 0.6.71 ; la **0.6.70 reste la sauvegarde connue-bonne**.
  `roll-image.sh 0.6.70` restaure l'état actuel en ~30 s.

## Hors scope (YAGNI)

- Pas de conversation libre hors cacao.
- Pas de refus généré par le modèle (les garde-fous restent déterministes).
- Pas de nouvelle méthode de clarification sur les agents ni de réordonnancement du pipeline.
- Pas de modification de la mémoire conversationnelle au-delà de l'existant.

## Risques et mitigations

| Risque | Mitigation |
|---|---|
| Le modèle pose une mauvaise question de clarification | Gate déterministe (ne déclenche que sur un thème réel) + consigne courte et ciblée + `max_tokens` borné. |
| Latence des tours de clarification (0 s → ~3-4 s) | `max_tokens` borné ; tradeoff assumé et documenté ; aucun tour ajouté. |
| Régression de parité V2/V3 | Tests sur les 4 points d'appel, drapeau on et off. |
| Comportement inattendu en prod | Drapeau de bascule instantanée + version connue-bonne 0.6.70. |
