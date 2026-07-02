# Cache sémantique dans l'orchestrateur (parité V2) + extraction d'un helper partagé

> Design 2026-07-02. Comble un écart de parité : l'orchestrateur V3 ne fait que
> l'exact-match, alors que `ConseilService` V2 a un cache **sémantique** (paraphrases).
> Occasion d'éliminer la duplication en extrayant un helper commun.

## Problème

- **V3 (orchestrateur)** : seul le cache **exact** est branché (`get_cached`/`set_cached`).
  Une paraphrase (« comment tailler ? » vs « comment élaguer ? ») ne touche pas le cache
  et repart en génération CPU (~15-46 s).
- **V2 (`ConseilService`)** : a le cache sémantique, mais via des méthodes **privées**
  (`_vecteur_question`, `_hit_semantique`) + champs `_embeddings/_seuil*`. Copier cette
  logique dans l'orchestrateur créerait une **duplication** (deux sources de vérité qui
  dériveront).

## Approche : un helper partagé `CacheSemantique`

Nouveau module **`api/app/application/cache_semantique.py`** — une petite classe qui
encapsule la couche sémantique, réutilisée par V2 et V3 :

```python
class CacheSemantique:
    def __init__(self, cache, embeddings, seuil, seuil_lexical): ...
    async def vecteur(self, question, historique) -> list[float] | None
    async def hit(self, question, langue, embedding) -> dict | None
    async def indexer(self, question, langue, embedding) -> None
```

- `vecteur` : embedding de la question, ou **None** si embeddings absent / multi-tours /
  échec (mêmes conditions que V2).
- `hit` : `cache.get_semantic(langue, embedding, seuil)` + **garde-fou lexical**
  (`couverture_lexicale >= seuil_lexical`) — bloque un voisin sémantique au qualificatif
  divergent (« cacaoyer adulte » vs « jeune »). Retourne le paquet décodé ou None.
- `indexer` : `cache.index_semantic(question, langue, embedding)` (no-op si embedding None).
- **Instance « désactivée »** : `embeddings=None` → `vecteur`/`hit` renvoient None,
  `indexer` ne fait rien. L'appelant tient donc **toujours** un `CacheSemantique` (pas de
  `None` à tester partout) ; il est simplement inerte quand la couche est off.

## Intégration

**`ConseilService` (refactor DRY, zéro changement de comportement)** : remplace
`_vecteur_question`/`_hit_semantique` et les appels `index_semantic` par le helper. Les
champs `_embeddings/_seuil_semantique/_seuil_lexical` disparaissent au profit d'un
`self._semantique: CacheSemantique`. Les tests V2 existants garantissent l'absence de
régression.

**`Orchestrateur`** : nouveau paramètre `cache_semantique: CacheSemantique | None = None`
(défaut = instance inerte si None). Dans `traiter` **et** `traiter_stream`, après le miss
exact (tour unique) :
1. `embedding = await sem.vecteur(question, historique)`
2. `paquet = await sem.hit(question, langue, embedding)` → si trouvé, servir comme un hit
   de cache (exact) : enrichissement contact + journalisation (sync) / émission bloc +
   final (stream). `logger.info("cache_semantique_hit")`.
3. Sur le chemin d'écriture (après génération, tour unique) : `await sem.indexer(...)`
   juste après `set_cached`. En composition (stream), on indexe aussi la synthèse.

**`api_deps`** : construire un `CacheSemantique` depuis `request.app.state.embeddings`
(si `semantic_cache_enabled`) + seuils de config, et le passer à l'orchestrateur
(`_construire_orchestrateur`/`get_orchestrateur`) comme à `ConseilService`. Même source
d'embeddings que le RAG (partagée), même gating qu'en V2.

## Souveraineté / sûreté

- Cache **tour unique** uniquement (une réponse multi-tours dépend du contexte).
- Le garde-fou lexical est conservé (anti-faux-positif sémantique).
- Le cache stocke le conseil **non enrichi** ; l'enrichissement contact (dépend de la
  conversation) reste appliqué à chaque requête — inchangé.
- Aucun LLM tiers : les embeddings viennent du service interne (port mockable).

## Tests

- **`test_cache_semantique.py`** (nouveau) : `vecteur` (None si off/multi-tours/échec),
  `hit` (seuil respecté, rejet lexical), `indexer` (no-op si embedding None), instance
  inerte (embeddings=None).
- **`test_orchestrateur.py`** : hit sémantique servi (paraphrase) au tour unique ;
  indexation après génération ; pas de sémantique en multi-tours ; parité stream.
- **V2** : la suite existante doit rester verte après refactor (non-régression).

Inférence/embeddings mockés, aucun réseau. Objectif : suite verte, couverture ≥ 97 %.

## Hors périmètre (YAGNI)

- Pas de changement des seuils prod (`SEMANTIC_CACHE_*`) — parité de comportement.
- Pas de cache sémantique en multi-tours (hors socle, comme V2).
- Pas de refonte du port cache (`get_semantic`/`index_semantic` déjà en place).
