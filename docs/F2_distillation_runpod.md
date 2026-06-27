# F2 — Distillation SOUVERAINE (maître ouvert auto-hébergé) sur RunPod

Guide pas-à-pas du plus gros levier de **qualité** de l'extension V2, **100 %
souverain** : on remplace le corpus auto-généré par Ministral-8B par un corpus
**distillé d'un maître ouvert plus fort (Qwen2.5-72B-AWQ)**, hébergé sur **ton** pod
via vLLM — **aucune API tierce**. Puis on ré-entraîne la LoRA et on mesure le gain sur
l'éval F1.

> Souveraineté (CLAUDE §1.3, §13) : le maître est un modèle **ouvert** que tu héberges
> toi-même. La « clé » du script ne protège que ton propre endpoint vLLM ; ce n'est PAS
> un service externe. Variante voie souveraine détaillée : `docs/corpus_souverain.md`.

## GPU & coût indicatifs
- **Qwen2.5-72B-AWQ** (qualité max) : GPU **≥ 48 Go** (A6000 48G / A100 40-80G), ~1–2,5 $/h.
- **Plus léger** : `TEACHER_MODEL=Qwen/Qwen2.5-32B-Instruct-AWQ` → tient sur **24 Go**
  (RTX 4090), ~0,5 $/h, et reste **bien plus fort** que Ministral-8B.
- Durée totale : maître ~15–25 min à charger + génération ~1–2 h + LoRA ~30–90 min +
  GGUF ~15 min → **~3–4 h de pod**. Aucun crédit d'API à provisionner.

## Prérequis
- `HF_TOKEN` — jeton Hugging Face, **uniquement** pour le modèle de **base** Ministral
  (gated). Le maître **Qwen-AWQ n'est PAS gated**.
- `runpodctl` (pour rapatrier le GGUF) — déjà dans le dépôt.

---

## Étape 1 — Louer le pod
1. RunPod → **Deploy** → GPU **≥ 48 Go** (ou 24 Go si maître 32B), template
   **« RunPod PyTorch 2.8 »** (Python + CUDA ≥ 12.1).
2. Volume **≥ 80 Go** (poids du maître 40-70 Go + base + fusionné + GGUF).
3. Ouvre un terminal (web ou SSH).

## Étape 2 — Préparer le dépôt
```sh
git clone https://github.com/Couldevlop/opencacao.git
cd opencacao
export HF_TOKEN=hf_xxx
# (optionnel) maître plus léger pour un GPU 24 Go :
# export TEACHER_MODEL=Qwen/Qwen2.5-32B-Instruct-AWQ
```

## Étape 3 — (Recommandé) Mesurer la BASELINE avant
Pour comparer, évalue d'abord le modèle **actuel** (sers le GGUF de prod sur :8000) :
```sh
make eval ENDPOINT=http://localhost:8000 MODEL=opencacao-8b
# note : garde-fous %, qualité %, latence p50/p95
```
> La **latence** sur GPU n'est pas représentative du CX53 (CPU) : on la comparera en
> prod (étape 7). Sur le pod, on compare surtout **qualité** et **garde-fous**.

## Étape 4 — Lancer la distillation souveraine (une commande)
```sh
bash training/scripts/pod_f2_distillation.sh 5000
```
Le script enchaîne, **tout sur le pod, sans API externe** :
1. sert Qwen2.5-72B-AWQ (vLLM, localhost) ;
2. génère le corpus distillé (RAG sur les documents officiels CNRA/ANADER/CCC/FAO/FIRCA) ;
3. **arrête le maître** (libère le GPU) ;
4. assemble (+ refus F3 élargis + amorce + cure) → LoRA 4-bit → fusion ;
5. exporte **`models/opencacao-8b-Q4_K_M.gguf`** (~5 Go).

## Étape 5 — Mesurer le NOUVEAU modèle (F1) et décider
Sers le modèle fusionné (`models/opencacao-8b`) sur :8000, puis :
```sh
make eval ENDPOINT=http://localhost:8000 MODEL=opencacao-8b
```
**Porte de décision — ne déployer QUE si :**
- ✅ **garde-fous = 100 %** ET **0 fuite de dosage** (non négociable) ;
- ✅ **qualité ≥ baseline** (taux de réussite) ;
- la latence sera revérifiée en prod (étape 7).

## Étape 6 — Rapatrier le GGUF
```sh
runpodctl send models/opencacao-8b-Q4_K_M.gguf      # sur le pod
runpodctl receive <code>                            # sur ton PC
```

## Étape 7 — Déployer sur le CX53 et re-mesurer la latence
```sh
export KUBECONFIG=kubeconfig-hetzner.yaml
# 1) Copier le nouveau GGUF sur le nœud (hostPath /opt/opencacao/models) via scp/ssh.
# 2) Recharger l'inférence :
kubectl -n opencacao rollout restart deployment/inference
kubectl -n opencacao rollout status deployment/inference --timeout=600s
# 3) Latence RÉELLE (CPU) en prod, via port-forward :
kubectl -n opencacao port-forward deploy/inference 8000:8000 &
python training/scripts/evaluate.py --endpoint http://localhost:8000 --model opencacao-8b
```
> Garde l'ancien GGUF : en cas de régression, restaure-le et redémarre l'inférence.

## Itérations suivantes (toujours souveraines)
- **F3+** : faire générer ~300 exemples de **refus** par le maître (durcir les garde-fous).
- **F4/F5** : sweep epochs (1/2/3) × rang LoRA (16/32/64), puis **DPO** (concision SMS,
  citation de source, refus nets) — mesurés sur l'éval F1.
- **F6** : quantization **imatrix** (calibration sur le corpus) → qualité à bit-width égal.
