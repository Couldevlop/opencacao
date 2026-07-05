# Agent Satellite (A8) MVP — alertes de déforestation GFW

**Date** : 2026-07-05 · **Statut** : validé (Waopron) · **Source validée** : GFW Data API,
alertes intégrées (`gfw_integrated_alerts`, GLAD+RADD), couverture Côte d'Ivoire vérifiée
(0 alerte en ville, 1 055 en lisière Taï/Guiglo), fraîcheur ~2 semaines (18/06/2026),
latence ~2-3 s. Clé API `opencacao-satellite` créée, **expire le 30/06/2027**.

## Objectif

Un producteur demande si sa zone est concernée par la déforestation (contexte EUDR :
cutoff 31/12/2020). L'agent interroge les alertes satellitaires GFW autour de sa
position et répond en langage naturel avec des **faits datés**, croisés au contexte
réglementaire RAG — sans jamais certifier une conformité.

## Architecture (moule Open-Meteo, recette « 4 étapes »)

1. **Outil** — `services/outils/satellite.py` : `SatellitePort` (Protocol,
   `async alertes(lat, lon) -> dict`) + `OutilSatellite` (contrat `Outil`, fail-soft
   `{}` sur exception, comme `OutilMeteo`).
2. **Adaptateur réel** — `services/outils/satellite_gfw.py` (`SatelliteGfw`) : httpx
   injectable (MockTransport en test), POST
   `/dataset/gfw_integrated_alerts/latest/query/json`, clé en en-tête `x-api-key`.
   Deux requêtes sur un polygone carré tampon (~1 km, `0.009°`) :
   `COUNT(*) WHERE date >= '2021-01-01'` puis, si count > 0,
   `date, COUNT(*) GROUP BY date` (bornée à l'année courante-1) pour les dates
   récentes. Retour : `{"alertes_depuis_2021": int, "dates_recentes": [str],
   "tampon_km": 1}` ou `{}`.
   **Particularités d'API encodées** : suivre le 307 (`follow_redirects=True`, POST
   préservé) ; pas de `MAX()` nu (plante côté tuiles) ; alias SQL ignorés (champ
   `count`). Sans `GFW_API_KEY` configurée : `SatelliteIndisponible` (moule
   `PrixIndisponible`) → l'agent donne une consigne, n'invente rien.
3. **Agent** — `services/agents/agent_satellite.py` (`AgentSatellite`, hérite
   `AgentBase`) : `nom="satellite"`. `_contexte()` :
   - **Localisation** : coordonnées GPS si présentes dans le fil (regex
     `lat, lon` avec bornes Côte d'Ivoire : lat 4..11, lon -9..-2), sinon localité
     nommée détectée sur tout le fil (`localites.py`) puis géocodée — le géocodage
     (endpoint Open-Meteo, sans clé) est fait par `SatelliteGfw` avec le même
     endpoint que `MeteoOpenMeteo` ; pas d'extraction de module partagé au MVP
     (2 usages ≠ duplication de logique métier, l'appel fait 6 lignes) —, sinon
     consigne « demander la position » (pattern agent Météo, 3 cas).
   - **Faits injectés** : aucune alerte → contexte « 0 alerte de déforestation
     détectée depuis 2021 dans un rayon d'~1 km ; préciser que c'est indicatif
     (zone autour du point, pas la parcelle cadastrée) et ne PAS affirmer une
     conformité EUDR ». Alertes → « N alertes, dernières dates …, expliquer les
     implications EUDR et rediriger vers le Conseil Café-Cacao / ANADER ». Outil
     vide `{}` → consigne « vérification satellite indisponible, ne donner aucun
     statut, rediriger ».
   - **Souveraineté** : l'agent CONSTATE des alertes, il ne certifie jamais
     (« conforme/non conforme ») — le mot d'ordre est dans chaque consigne.
4. **Routage & frontière** : `mots_cles` Satellite = `deforestation/déforestation`,
   `geolocalisation/géolocalisation`, `satellite`, `parcelle`, `foret/forêt`
   (mot entier, via `compter_mots_cles` ; PAS `alerte` seul — happerait
   « alerte pluie », domaine météo). **Amendement 06/07 (revue finale, décision
   Waopron)** : co-occurrence requise — les mots FAIBLES (`parcelle`, `forêt`)
   ne comptent que si un mot FORT (`déforestation`, `satellite`,
   `géolocalisation`) est présent ; sans mot fort, score 0. Sinon « traiter les
   chenilles sur ma parcelle » (agronomie pure) était détourné du RAG vers un
   appel GFW hors sujet. Ces deux premiers sont RETIRÉS
   de `agent_reglementation.py` (même chirurgie que Normes) ; `eudr`,
   `traçabilité`, `conformité`, `réglementation` restent à l'agent Réglementation.
   Satellite enregistré APRÈS Réglementation (égalité de score → EUDR gagne).
5. **Câblage** — `api_deps._construire_orchestrateur` : une ligne
   `registre.enregistrer(AgentSatellite(inference, OutilSatellite(source), rag=rag))`
   avec `source = SatelliteGfw(cle=settings.gfw_api_key)` si la clé est configurée,
   sinon `SatelliteIndisponible()`. Config : `gfw_api_key: str = ""` +
   `gfw_url` surchargeables (`GFW_API_KEY` — Secret K8s cloisonné namespace
   opencacao, jamais en ConfigMap).

## Hors périmètre MVP (→ J1 et suivants)

Polygones de parcelles réels (PostGIS/GeoJSON), dossier de due diligence EUDR,
cartographie web, upload de tracés GPS, cache des réponses GFW.

## Tests (TDD, un par comportement)

- Outil : fail-soft `{}` sur exception ; adaptateur GFW via MockTransport (307 suivi,
  count>0 → dates ; count=0 → pas de 2e requête) ; indisponible → consigne.
- Agent : GPS dans le fil prioritaire sur la localité ; localité seule géocodée ;
  aucune localisation → demande la position ; faits injectés dans le contexte ;
  consigne « jamais certifier » présente ; score élevé sur « déforestation près de
  ma parcelle », quasi nul sur une question prix.
- Frontière : « alertes déforestation sur ma parcelle » → satellite ;
  « que demande l'EUDR pour exporter ? » → reglementation.
- Câblage : agent enregistré ; sans `GFW_API_KEY` l'app démarre (source
  indisponible).
- Garde-fous : hérités de l'orchestrateur (déjà couverts), aucun test dupliqué.

## Exploitation

Clé GFW : `.env` local (gitignored) + Secret K8s à créer au déploiement.
**Rappel d'expiration à planifier (juin 2027).** Latence attendue d'une réponse
satellite : ~3 s d'outil + génération LLM (~15-40 s) — pas de premier octet
spécifique à prévoir (mono-agent, flux existant).
