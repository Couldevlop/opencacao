# Progression pendant l'attente — design

**Date** : 2026-07-07 · **Statut** : validé (Waopron, option A)

## Problème

Sur `/v1/chat/stream`, un flux mono-agent (satellite, météo, RAG…) reste
silencieux ~15-26 s (routage → appel d'outil → préremplissage CPU) avant le
premier token. L'interface n'affiche que trois points animés, sans indiquer ce
que fait l'assistant. Les événements `progress` existent déjà mais ne sont émis
que pendant la composition multi-agents (anti-524) et le front les ignore.

## Décisions

- **Contenu** : étapes déterministes du pipeline, messages fixes en français —
  pas de « réflexion » générée (chaque génération CPU coûte ~15 s).
- **Persistance** : la ligne de statut disparaît au premier token de la réponse.
- **Approche** : progression émise par l'orchestrateur (table statique
  nom-d'agent → libellé), pas de hook dans les agents (~95 % de l'attente est le
  préremplissage de la génération, qu'un seul libellé couvre honnêtement).

## Backend

- `app/application/flux.py` : constantes partagées `PROGRES_ANALYSE`
  (« J'analyse votre question… »), `PROGRES_REDACTION` (« Je rédige ma
  réponse… ») et helper `progres(texte) -> dict`.
- `app/application/orchestrateur.py` :
  - table module-level `PROGRES_AGENTS` (rag, meteo, prix, satellite,
    reglementation, normes, reporting) ;
  - `traiter_stream` : `progres(PROGRES_ANALYSE)` en tout premier octet (tous
    les flux — étend l'anti-524 au-delà de la composition) ;
  - mono-agent : `progres(PROGRES_AGENTS[agent.nom])` avant le dispatch ;
  - composition : libellé humain du contributeur AVANT chaque contribution
    (remplace le heartbeat brut `[nom]`, propriété anti-524 préservée), puis
    `progres(PROGRES_REDACTION)` avant la synthèse.
- `app/application/conseil_service.py` (parité V2) : `PROGRES_ANALYSE` en tête
  de `conseiller_stream`, `PROGRES_REDACTION` avant `generer_stream`.
- `/v1/chat` synchrone et schéma SSE inchangés (le type `progress` existe déjà ;
  les clients qui l'ignorent restent compatibles).

## Frontend

- `api-client.js` : `options.onProgress(texte)` optionnel sur l'événement
  `progress` (ignoré si absent).
- `chat-view.js` : `majSaisie(texte)` ajoute/actualise un libellé à côté des
  trois points de l'indicateur `#typing` ; `cacherSaisie()` inchangé.
- `main.js` : `onProgress: (t) => vue.majSaisie(t)`.
- `styles.css` : `.typing-label` (italique atténué) ; conteneur en
  `aria-live="polite"`.

## Tests

Pytest (inférence mockée) : ordre `progress → token → done` ; libellé correct
par agent ; refus/clarification/cache n'émettent que « J'analyse… » ; libellés
humains en composition (plus de `[nom]`) ; parité `conseil_service` ; aucun
`progress` après le premier token.
