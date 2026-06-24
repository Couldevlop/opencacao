# OpenCacao V2 — Conversations persistantes, mémoire & identité

Documentation de la **V2 conversationnelle** : sessions durables, mémoire serveur,
identité anonyme par appareil et conformité RGPD. La spécification de référence reste
`CLAUDE_OpenCacao.md` ; **sa mise à jour V2 attend la validation de Waopron Coulibaly**
(ce document en tient lieu en attendant).

## 1. Vue d'ensemble

La V1 était sans état : le navigateur renvoyait tout l'historique à chaque requête
(perdu au rechargement). La V2 rend l'assistant **100 % conversationnel** — façon
ChatGPT/Claude — avec des conversations enregistrées côté serveur, reprenables,
nommables et recherchables, le tout **souverain** (aucune dépendance hors spec §2.1,
aucun service externe en production).

Chantier livré en 6 sprints (roadmap : `docs/OpenCacao_V2_Roadmap_Agile.xlsx`).

## 2. Architecture

```
Navigateur (web/)                         API FastAPI (api/)
  device-id (UUID localStorage)  --X-Device-Id-->  routers/sessions.py
  sidebar + reprise + recherche  --session_id-->   routers/chat.py
        |                                                |
        |                                   application/dialogue_session.py
        |                                   (mémoire serveur : fenêtre + résumé)
        v                                                v
  localStorage (id session active)            core/sessions.py  (SQLite + WAL)
                                              -> /data/opencacao_sessions.db (PVC)
```

- **`core/sessions.py`** — dépôt SQLite (bibliothèque standard, migrations via
  `PRAGMA user_version`). Aucune dépendance externe, aucun conteneur de BD.
- **`application/dialogue_session.py`** — oriente le chat vers la mémoire serveur
  quand un `session_id` est fourni ; sans lui, comportement V1 inchangé (rétrocompat).
- **`application/memoire.py`** — borne le contexte transmis au modèle (cf. §4).
- **`services/titres.py`** — titre automatique déterministe (cf. §4).

## 3. API REST (`/v1`)

| Méthode | Chemin | Rôle |
|---|---|---|
| `POST` | `/v1/sessions` | Crée une conversation (rate-limit par IP). |
| `GET` | `/v1/sessions` | Liste les conversations **de l'appareil**, plus récente en tête. |
| `GET` | `/v1/sessions/recherche?q=` | Recherche plein-texte (titre + messages). |
| `GET` | `/v1/sessions/{id}` | Conversation + messages (= **export** RGPD). |
| `PATCH` | `/v1/sessions/{id}` | Renomme la conversation. |
| `DELETE` | `/v1/sessions/{id}` | Supprime la conversation (droit à l'effacement). |
| `POST` | `/v1/chat` · `/v1/chat/stream` | Chat ; `session_id` optionnel (mémoire serveur). |

Toutes les requêtes portent l'en-tête **`X-Device-Id`** (cf. §5). Une conversation
inconnue **ou appartenant à un autre appareil** renvoie `404`.

## 4. Mémoire conversationnelle

- **Contexte serveur** : avec un `session_id`, l'historique fait autorité côté serveur
  (jamais renvoyé par le client).
- **Fenêtre glissante + résumé** (`memoire.fenetre_dialogue`) : au-delà de
  `SESSIONS_RESUME_SEUIL` messages (défaut 16), les tours anciens sont condensés en un
  **résumé extractif déterministe** (questions posées + dernier conseil) et seuls
  `SESSIONS_FENETRE_MESSAGES` messages récents (défaut 8) sont réinjectés mot pour mot.
  Choix **sans appel au modèle** : zéro latence supplémentaire sur le nœud CPU/GGUF
  (CX53), reproductible (risque R1 de la roadmap).
- **Titre automatique** (`titres.depuis_question`) : dérivé **déterministe** de la
  première question (aucune inférence) ; un renommage manuel n'est jamais écrasé.
- **Garde-fous ré-ancrés** : les garde-fous d'entrée sont évalués sur la question
  **ancrée au dernier tour** — une demande de dosage étalée sur deux tours est bloquée.

## 5. Identité anonyme par appareil (RGPD by design)

- Le navigateur génère un **UUID opaque** (`device-id.js`), conservé en `localStorage`
  et envoyé via `X-Device-Id`. **Ce n'est ni une authentification ni une donnée
  personnelle** : aucune IP, aucun nom — juste un jeton aléatoire.
- Côté serveur, chaque conversation est rattachée à ce `proprietaire` (colonne
  ajoutée par la migration 2). Listage, lecture, renommage, suppression et recherche
  sont **cloisonnés par appareil** : un navigateur ne voit jamais les conversations
  d'un autre.
- L'authentification légère (magic link, D2 de la roadmap) est **différée** : elle
  exigerait une infrastructure e-mail, hors périmètre du démonstrateur souverain.

## 6. Conformité RGPD

- **Minimisation** : aucune donnée personnelle stockée pour une session (pas d'IP,
  identifiant anonyme). Les visites sont journalisées au niveau pays uniquement.
- **Rétention & purge** : les conversations inactives au-delà de
  `SESSIONS_RETENTION_JOURS` (défaut 365) sont **purgées automatiquement** (au
  démarrage puis une fois par jour ; `0` désactive). Messages supprimés en cascade.
- **Droit à l'effacement** : `DELETE /v1/sessions/{id}` (par appareil).
- **Portabilité** : `GET /v1/sessions` + `GET /v1/sessions/{id}` exportent l'ensemble
  des conversations et messages de l'appareil en JSON.

## 7. Déploiement (K3s)

La base SQLite des conversations vit sur le volume persistant `/data` (PVC `dataset`,
partagé avec le journal et l'index RAG), monté par le déploiement `api`. Réplica
**unique** (SQLite mono-écrivain, mode WAL). Les migrations s'appliquent au démarrage,
de façon idempotente — aucune action manuelle au redéploiement.

Variables (ConfigMap `api-config`) : `SESSIONS_ENABLED`, `SESSIONS_DB_PATH`,
`SESSIONS_RETENTION_JOURS`, `SESSIONS_FENETRE_MESSAGES`, `SESSIONS_RESUME_SEUIL`.

## 8. Tests

`pytest` (≥ 90 % sur `api/app`) couvre la persistance, la mémoire, l'identité, la
recherche et la purge. Le frontend (vanilla JS, sans dépendance) est validé par
`node --check` et un harnais de graphe d'imports/exports.
