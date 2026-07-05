# Sprint latence #3 — outils et premier octet du matin

**Date** : 2026-07-06 · **Statut** : validé (Waopron : « livre les trois »)

## Contexte

Le plancher CPU (~15-20 s à chaud) est atteint ; les secondes restantes sont
autour de la génération : appels d'outils (géocodage systématique, aucune
réutilisation des résultats météo/GFW) et préfixe KV froid après une période
calme (le premier producteur paie ~15-25 s de plus). Mesures prod 06/07 :
RAG neuf 26,4 s, météo neuve 21,9 s, quasi-paraphrase 1,45 s (cache sémantique).

## Lot A — Coordonnées statiques des localités

- `scripts/gen_coordonnees_localites.py` (ponctuel) : géocode via Open-Meteo
  toutes les localités connues (zones/sièges de l'annuaire ANADER +
  `LOCALITES_NORD`), en ne retenant que les résultats `country_code == "CI"`
  (corrige au passage le risque d'homonyme étranger du géocodage live), et
  écrit `api/app/data/coordonnees_localites.json` (`{clé normalisée: [lat, lon]}`).
- `localites.coordonnees(nom) -> tuple[lat, lon] | None` (chargement mémoïsé,
  fail-soft {} si fichier absent).
- `MeteoOpenMeteo` et `SatelliteGfw` consultent la table AVANT le géocodage
  HTTP (qui reste le repli pour un nom hors table). Gain : ~0,5-1 s par
  question météo/satellite + un point de défaillance réseau en moins.

## Lot B — Cache Redis des résultats d'outils

- `core/cache.py` : `get_outil(cle) -> str | None` / `set_outil(cle, payload,
  ttl_s)` — préfixe `outil:`, fail-soft (Redis en panne = cache transparent).
- `OutilMeteo`/`OutilSatellite` : paramètres optionnels `cache` (petit Protocol
  `CacheOutilPort`) et `ttl_s`. Clés : `outil:meteo:<localité normalisée>` et
  `outil:satellite:<lat arrondi 3 déc>,<lon>` (ou localité). Un résultat VIDE
  n'est jamais mis en cache (un échec ne doit pas coller 30 min).
- Config : `outil_cache_meteo_ttl_s = 1800` (la pluie sous 30 min ne change
  pas de conseil), `outil_cache_satellite_ttl_s = 86400` (alertes GFW
  hebdomadaires) ; `0` = cache coupé. Câblage dans `api_deps`.
- Gain : 1-3 s sur les questions répétées par localité (60 zones seulement,
  forte répétition attendue) + économie du quota GFW.

## Lot C — Keepalive du préfixe KV

- `application/keepalive.py` : boucle de fond qui appelle
  `inference.generer("ok", max_tokens=1)` toutes les `kv_keepalive_s`
  secondes — le préfixe système (message system constant + `cache_prompt`)
  reste chaud dans llama-server, le premier producteur du matin ne paie plus
  son préremplissage. Erreurs avalées + log (`keepalive_echec`), la boucle ne
  meurt jamais ; paramètre `iterations` injectable pour les tests.
- `main.py` : tâche lancée dans le lifespan si `kv_keepalive_s > 0`
  (mirroir de `prewarm_task`/`purge_task`, annulée à l'arrêt).
- Config : `kv_keepalive_s = 0` (OFF par défaut, convention du projet) ;
  ConfigMap prod : `KV_KEEPALIVE_S=600`. Coût : ~2-3 s de CPU toutes les
  10 min (~0,5 %), nul pendant le trafic réel (le préfixe est déjà chaud).

## Hors périmètre

MoE/GPU (décision infra), tuning RAG (rendement décroissant prouvé),
pré-calcul météo des 60 zones en tâche de fond (attendre les données d'usage).

## Tests

Un test par comportement : table (nom connu/inconnu/fichier absent), adaptateurs
sans appel de géocodage (MockTransport qui échoue sur l'hôte geo), hit/miss/vide
du cache outil, TTL transmis, keepalive (n appels, erreur tolérée, respect de
l'intervalle), câblage. Aucun appel réseau réel.
