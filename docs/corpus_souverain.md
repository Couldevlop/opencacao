# Génération souveraine du corpus (option B)

Générer le corpus Q/R d'entraînement avec un **modèle-maître ouvert auto-hébergé**,
sans appeler aucune API tierce. C'est l'option retenue pour rester cohérent avec la
thèse du livre blanc *« IA souveraine pour la Côte d'Ivoire »* : poids ouverts +
auto-hébergement + aucun service IA propriétaire externe.

> **Pourquoi pas l'API Z.ai (GLM-5.2) ?** C'était l'option la plus simple, mais elle
> fait transiter les extraits par un service tiers (hors prod, données publiques —
> exception tolérée par la spec §13). L'option B supprime cette exception **et** la
> dépendance à un compte externe (clé, solde, facturation). Voir la comparaison
> « Option B vs Option D » plus bas.

La production n'est **jamais** concernée : le modèle servi reste opencacao-8b
(Ministral) auto-hébergé. Le modèle-maître ne sert qu'**hors ligne**, une fois, pour
produire les données ; il n'apparaît jamais dans le chemin de réponse aux producteurs.

## Deux façons de lancer

### A. Workflow dissocié — pod = modèle-maître, RAG en local (recommandé)

Le pod GPU ne fait **que** servir le modèle-maître ; la génération RAG (sources,
embeddings, dédup) tourne sur **ta machine** et n'envoie au pod que les requêtes de
rédaction. Le pod n'exécute aucun RAG.

**Sur le pod GPU** (RunPod, etc.) :
```bash
export CORPUS_LLM_API_KEY=...     # clé partagée pod <-> local (sinon générée + affichée)
bash training/scripts/pod_corpus_souverain.sh
```
Le script installe vLLM, sert le modèle-maître (Qwen2.5-72B-AWQ par défaut) avec une
clé d'API, et affiche l'endpoint à utiliser (`https://<POD_ID>-8000.proxy.runpod.net`).

**En local**, contre ce pod :
```bash
export CORPUS_LLM_BASE_URL=https://<POD_ID>-8000.proxy.runpod.net
export CORPUS_LLM_API_KEY=...     # la même clé
bash training/scripts/generate_souverain_local.sh 2000   # lot de mesure
```
→ écrit `corpus/corpus_cacao_teacher.jsonl`. L'endpoint du pod est protégé par la clé
(il est exposé via le proxy public). Aucune donnée ne part vers un service tiers.

### B. Tout-en-un sur un seul hôte GPU (Docker)

Si tu disposes d'un hôte GPU avec Docker + NVIDIA Container Toolkit et que tu veux tout
au même endroit :
```bash
# Lot de mesure (2 000) ou corpus complet (10 000)
HF_TOKEN=hf_xxx TARGET=2000 docker compose -f docker-compose.corpus.yml up --build
```
Le service `teacher` (vLLM) sert le modèle-maître en interne ; le service `generator`
l'interroge via `http://teacher:8000` (jamais une URL externe) et écrit le même
`corpus/corpus_cacao_teacher.jsonl`. Les embeddings et la déduplication tournent sur
CPU dans le générateur (le GPU est dédié au modèle-maître).

Étape suivante, une fois le corpus produit et validé :

```bash
python training/scripts/enrich_corpus.py --check corpus/corpus_cacao_teacher.jsonl
make train   # entraînement LoRA sur le nouveau corpus
```

## Les deux modèles-maîtres candidats

Le modèle-maître doit être **fort** (meilleur que le Ministral-8B actuel qui s'auto-
enseigne), **ouvert** (auto-hébergeable), **bon en français** et **tenir sur un seul
GPU loué**. Deux candidats de la classe 70B répondent à ces critères :

| Critère | **Qwen2.5-72B-Instruct (AWQ)** — *défaut* | **Llama-3.3-70B-Instruct** |
|---|---|---|
| Taille / quantification | 72B, **AWQ ~40 Go** | 70B ; FP16 ~140 Go, **FP8 ~70 Go**, AWQ ~40 Go |
| GPU minimal | 1× 48 Go (A6000 / A100-40 serré / A100-80 / H100) | FP8 : **H100 requis** ; sinon variante AWQ/GPTQ sur Ampere |
| Compatibilité matérielle | **Ampere ET Hopper** (AWQ ne dépend pas du FP8) | FP8 natif = **Hopper uniquement** |
| Qualité français | Excellente (multilingue très solide) | Bonne (multilingue nettement amélioré en 3.3) |
| Licence | Licence Qwen (permissive ; clause >100 M d'utilisateurs actifs/mois) | Llama 3.3 Community (permissive ; attribution « Built with Llama », clause >700 M MAU) |
| Origine | Alibaba (Chine) — **auto-hébergé, rien ne sort** | Meta (USA) — **auto-hébergé, rien ne sort** |
| Coût indicatif (GPU loué) | ~0,5–1,5 $/h (GPU 48 Go) | ~2–3 $/h si H100 pour le FP8 |

### Points de friction

- **Qwen2.5-72B-AWQ** : la licence Qwen impose une autorisation séparée au-delà de
  100 M d'utilisateurs actifs/mois — sans objet pour un projet-démo, mais à connaître.
  Modèle d'origine chinoise : comme il est **auto-hébergé**, aucune donnée ne sort, donc
  cela n'entame pas la souveraineté (le critère est l'auto-hébergement, pas l'origine
  des poids) ; à signaler néanmoins par transparence.
- **Llama-3.3-70B** : le FP8 (qui le fait tenir sur un seul GPU) exige un GPU **Hopper
  (H100)**, plus cher et moins disponible que l'Ampere. Sur Ampere, il faut passer par
  une variante AWQ/GPTQ communautaire (un cran de moins en fidélité). La licence Llama
  impose l'attribution « Built with Llama ».
- **Communs aux deux** : téléchargement initial des poids (40–70 Go) → premier
  démarrage long (~15–25 min, d'où le `start_period` élevé du healthcheck) ; débit de
  génération piloté par la concurrence (`CONCURRENCE`, défaut 8) et la VRAM disponible.

### Le choix retenu : Qwen2.5-72B-Instruct-AWQ — et pourquoi

C'est le **défaut** de `docker-compose.corpus.yml`, pour trois raisons :

1. **Friction matérielle minimale = coût le plus bas.** L'AWQ tourne aussi bien sur
   Ampere (A100/A6000) que sur Hopper. Pas besoin d'un H100 rare et cher comme le
   réclame le Llama en FP8 : on loue un GPU 48 Go d'entrée de gamme à ~0,5–1,5 $/h.
2. **Excellent en français**, essentiel pour rédiger un corpus destiné aux planteurs
   ivoiriens — au moins à parité avec le Llama-3.3, souvent au-dessus.
3. **Tient large sur un seul GPU** (~40 Go), ce qui laisse de la marge VRAM pour un
   contexte de 8 k tokens et une bonne concurrence.

**Llama-3.3-70B reste un excellent plan B**, surtout si tu disposes déjà d'un H100 ou
si tu préfères l'écosystème/licence Meta. Le passage de l'un à l'autre est trivial — une
variable d'environnement, aucun changement de code :

```bash
TEACHER_MODEL=meta-llama/Llama-3.3-70B-Instruct TEACHER_QUANT=fp8 \
  HF_TOKEN=hf_xxx TARGET=2000 docker compose -f docker-compose.corpus.yml up --build
```

> **Alternative la plus « cohérente Mistral »** : si tu veux rester dans la famille du
> modèle de base (Ministral), `mistralai/Mistral-Small-3` (24B, Apache-2.0, français
> natif, tient sur un GPU 24–48 Go) est un maître plus léger — plafond de qualité
> inférieur à un 70B, mais 100 % ouvert et européen. À considérer si la licence/origine
> prime sur la qualité brute.

## Option B (auto-hébergé) vs Option D (API Z.ai / GLM-5.2)

| | **Option B — ce document** | **Option D — API Z.ai** |
|---|---|---|
| Souveraineté | ✅ totale, aucune donnée ne sort | ⚠️ extraits publics transitent par Z.ai (hors prod) |
| Dépendance externe | aucune (tu loues un GPU) | compte Z.ai : clé, **solde à recharger**, facturation USD |
| Reproductibilité | ✅ conteneur + poids + code ouverts | dépend d'un service tiers (modèle pouvant évoluer) |
| Qualité enseignant | très bonne (70B) | maximale (GLM-5.2, 753B) |
| Mise en place | GPU loué + ~20 min de chargement | immédiate (un appel d'API) |
| Coût | ~1 $/h de GPU, prévisible | au token, nécessite un solde provisionné |

**En clair :** l'option B échange un peu de mise en place (un GPU loué quelques heures)
contre la **souveraineté pure et l'autonomie totale** — pas de solde d'API à gérer, pas
d'exception à justifier dans le livre blanc. C'est le choix cohérent avec la thèse du
projet ; l'option D ne se justifie que pour un test ponctuel ou si aucun GPU n'est
disponible.
