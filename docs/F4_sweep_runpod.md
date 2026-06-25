# F4 — Recette LoRA pilotée par l'éval (sweep) sur RunPod

Guide pas-à-pas du levier **recette** de l'extension V2 : au lieu d'entraîner une
seule LoRA « à l'aveugle », on **balaie une grille d'hyperparamètres**, on entraîne un
adaptateur par combinaison, on **évalue chacun** avec le jeu d'éval étendu (F1), puis on
ne retient que le **meilleur point de contrôle d'après l'éval**. Seul le vainqueur est
fusionné et exporté en GGUF — économe en disque et 100 % souverain.

> Souveraineté (CLAUDE §1.3, §13) : tout tourne sur **ton** pod. Le juge GLM-5.2
> (optionnel, `--juge`) sert uniquement à **noter** la qualité hors production, comme
> l'enrichissement du corpus ; il n'entre jamais dans le service. Sans clé, le
> classement retombe sur le taux de qualité déterministe.

## Ce que balaie la recette
- **epochs** : 1 / 2 / 3
- **rang LoRA** `r` : 16 / 32 / 64 (échelle `alpha = 2·r` par convention)
- **taux d'apprentissage** `lr`
- **longueur de séquence max** : 1024 → 1536

La grille est calculée par `sweep_lora.py grille` (source de vérité unique, consommée
par le script pod). Défaut volontairement **tractable** (4 combinaisons) ;
surcharge par variables d'environnement.

## Portail de décision — non négociable (CLAUDE §13)
Une combinaison n'est **éligible** que si :
- ✅ **garde-fous = 100 %** ET **0 fuite de dosage**.

La qualité (note du **juge** en priorité, sinon taux de réussite) ne départage que des
combinaisons **déjà sûres** ; à qualité égale, la **latence p95** la plus basse gagne
(F4 = qualité × latence). Si **aucune** combinaison ne passe le portail, le script
échoue et **ne déploie rien**.

## GPU & coût indicatifs
- **24 Go suffisent** (RTX 4090) : base BF16 ~16 Go + adaptateurs LoRA (~90 Mo chacun).
- Le sweep entraîne N adaptateurs **séquentiellement** ; compter ~30–90 min par
  combinaison + ~15 min de GGUF pour le vainqueur. 4 combinaisons ≈ **2–6 h de pod**.
- Volume **≥ 80 Go**.

## Prérequis
- `HF_TOKEN` — jeton Hugging Face, modèle de **base** Ministral (gated).
- `ZAI_API_KEY` — **recommandé** (juge GLM-5.2 pour un classement qualité fiable).
- Un corpus d'entraînement présent : `corpus/corpus_cacao_rag.jsonl` (via
  `pod_generate.sh`) **ou** le corpus distillé F2 `corpus/corpus_cacao_teacher.jsonl`.

---

## Étape 1 — Louer le pod
RunPod → **Deploy** → GPU **24 Go** (RTX 4090) suffit, template
**« RunPod PyTorch 2.8 »** (Python + CUDA ≥ 12.1), volume **≥ 80 Go**.

## Étape 2 — Préparer le dépôt
```sh
git clone https://github.com/Couldevlop/opencacao.git
cd opencacao
export HF_TOKEN=hf_xxx
export ZAI_API_KEY=...        # recommandé (juge GLM-5.2)
```

## Étape 3 — (Recommandé) Mesurer la BASELINE
Sers le GGUF de prod sur :8000 et note garde-fous % / qualité % :
```sh
make eval ENDPOINT=http://localhost:8000 MODEL=opencacao-8b
```

## Étape 4 — Lancer le sweep (une commande)
```sh
# Grille par défaut (4 combinaisons) :
bash training/scripts/pod_f4_sweep.sh

# Grille complète de la roadmap (36 combinaisons — long) :
SWEEP_EPOCHS="1 2 3" SWEEP_RANGS="16 32 64" SWEEP_LRS="2e-4 1e-4" SWEEP_SEQ="1024 1536" \
  bash training/scripts/pod_f4_sweep.sh
```
Le script enchaîne, **tout sur le pod** :
1. assemble le corpus (teacher F2 + RAG + démarrage + refus F3 + cure) ;
2. entraîne **un adaptateur LoRA par combinaison** (`models/sweep/<id>/`) ;
3. sert la base BF16 **+ tous les adaptateurs** via vLLM (LoRA, **sans fusion**) ;
4. **évalue chaque combinaison** (garde-fous + qualité + juge + latence) ;
5. **sélectionne** le meilleur point de contrôle d'après l'éval ;
6. fusionne **uniquement le vainqueur** + exporte `models/opencacao-7b-Q4_K_M.gguf`.

Le tableau comparatif et le vainqueur sont écrits dans
`models/sweep/rapport_sweep.json`.

## Étape 5 — Re-vérifier le vainqueur et décider
Le portail a déjà filtré sur les garde-fous ; re-confirme qualité ≥ baseline :
```sh
# (le rapport par combinaison est dans models/sweep/<id>.json)
cat models/sweep/rapport_sweep.json
```
**Ne déployer QUE si** garde-fous = 100 % ET 0 fuite de dosage ET qualité ≥ baseline.

## Étape 6 — Rapatrier le GGUF
```sh
runpodctl send models/opencacao-7b-Q4_K_M.gguf      # sur le pod
runpodctl receive <code>                            # sur ton PC
```

## Étape 7 — Déployer sur le CX53 et re-mesurer la latence RÉELLE (CPU)
```sh
export KUBECONFIG=kubeconfig-hetzner.yaml
# 1) Copier le nouveau GGUF sur le nœud (hostPath /opt/opencacao/models) via scp/ssh.
# 2) Recharger l'inférence :
kubectl -n opencacao rollout restart deployment/inference
kubectl -n opencacao rollout status deployment/inference --timeout=600s
# 3) Latence réelle (CPU) en prod, via port-forward :
kubectl -n opencacao port-forward deploy/inference 8000:8000 &
python training/scripts/evaluate.py --endpoint http://localhost:8000 --model opencacao-8b
```
> Garde l'ancien GGUF : en cas de régression, restaure-le et redémarre l'inférence.

## Dépannage
- **vLLM refuse les LoRA** : vérifie `--max-lora-rank` ≥ rang max de la grille (le script
  le calcule). Logs dans `$TMPDIR/vllm.log`.
- **OOM au service** : baisse `GPU_UTIL` (`export GPU_UTIL=0.85`) ou réduis la grille.
- **Sélection vide** (« rien à déployer ») : aucune combinaison n'a 100 % de garde-fous
  ou une combinaison laisse fuiter un dosage → élargir les refus (F3) puis relancer.
- **Inspecter une combinaison** : `cat models/sweep/<id>.json`.

## Suite (toujours souveraine)
- **F5** : DPO/ORPO sur des paires de préférence (concision SMS, citation, refus nets).
- **F6** : quantization **imatrix** (calibration sur le corpus) → qualité à bit-width égal.
- **F11** : juge GLM-5.2 sur le journal de prod (👍/👎) → corpus curé → ré-entraînement.
