"""Génère les schémas d'architecture du dossier de référence OpenCacao-8B.

Produit des PNG haute résolution (palette « cacao » souveraine) dans
``docs/img/``, embarqués ensuite dans
``docs/OpenCacao-8B_Dossier-de-reference.docx``. Aucune dépendance lourde :
uniquement matplotlib. Reproductible (``python scripts/gen_doc_diagrams.py``).

Schémas produits :
    * architecture.png   — vue d'ensemble en couches (client → API → inférence)
    * pipeline.png       — pipeline de bout en bout (corpus → LoRA → GGUF → service)
    * deploiement.png    — déploiement K3s/Hetzner + chaîne CD GitOps
    * sweep_f4.png       — recette pilotée par l'éval (sweep + portail garde-fous)
    * garde_fous.png     — défense en profondeur des garde-fous métier
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

SORTIE = Path(__file__).resolve().parents[1] / "docs" / "img"
DPI = 200

# Palette « cacao » souveraine (brun cacao + vert agronomie + ocre).
C = {
    "cacao_dark": "#4E342E",
    "cacao": "#6D4C41",
    "cacao_light": "#EFE6DF",
    "cream": "#F7F2EA",
    "green": "#2E7D32",
    "green_light": "#D9EBD3",
    "ochre": "#B97A2B",
    "gold_light": "#F3E4C8",
    "slate": "#37474F",
    "slate_light": "#E4E9EC",
    "white": "#FFFFFF",
    "ink": "#2B2622",
    "red": "#A6342A",
    "red_light": "#F2DCD8",
    "blue": "#1565C0",
    "blue_light": "#D6E5F6",
}

# Police : DejaVu Sans (livrée avec matplotlib, accents complets).
plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["svg.fonttype"] = "none"


class Toile:
    """Toile de dessin à coordonnées 0–100, sans axes, avec cartouche de marque."""

    def __init__(self, largeur: float, hauteur: float, titre: str, sous_titre: str = ""):
        self.fig, self.ax = plt.subplots(figsize=(largeur, hauteur))
        self.ax.set_xlim(0, 100)
        self.ax.set_ylim(0, 100)
        self.ax.set_aspect("auto")
        self.ax.axis("off")
        self.fig.patch.set_facecolor(C["white"])
        self.ax.set_facecolor(C["white"])
        # Bandeau de titre.
        self.ax.add_patch(
            mpatches.Rectangle(
                (0, 92.5), 100, 7.5, facecolor=C["cacao_dark"], edgecolor="none"
            )
        )
        self.ax.add_patch(
            mpatches.Rectangle((0, 91.3), 100, 1.2, facecolor=C["ochre"], edgecolor="none")
        )
        self.ax.text(
            2.5,
            96.7,
            titre,
            color=C["white"],
            fontsize=14.5,
            fontweight="bold",
            va="center",
            ha="left",
        )
        if sous_titre:
            self.ax.text(
                2.5,
                93.4,
                sous_titre,
                color=C["gold_light"],
                fontsize=9,
                va="center",
                ha="left",
            )
        self.ax.text(
            97.5,
            95.6,
            "OpenCacao-8B",
            color=C["gold_light"],
            fontsize=11,
            fontweight="bold",
            va="center",
            ha="right",
        )
        # Pied de page.
        self.ax.text(
            2.5,
            1.2,
            "Dossier de référence — OpenLab Consulting · IA souveraine pour la Côte d'Ivoire",
            color=C["cacao"],
            fontsize=8,
            va="center",
            ha="left",
            style="italic",
        )

    def boite(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        titre: str,
        sous: str = "",
        fc: str = C["white"],
        ec: str = C["cacao"],
        tc: str = C["ink"],
        taille_titre: float = 11,
        taille_sous: float = 8.5,
        gras: bool = True,
        lw: float = 1.6,
    ) -> dict:
        """Dessine une boîte arrondie centrée en (x, y) ; renvoie sa géométrie."""
        self.ax.add_patch(
            FancyBboxPatch(
                (x - w / 2, y - h / 2),
                w,
                h,
                boxstyle="round,pad=0.2,rounding_size=1.4",
                facecolor=fc,
                edgecolor=ec,
                linewidth=lw,
                mutation_aspect=0.6,
            )
        )
        if sous:
            self.ax.text(
                x,
                y + h * 0.16,
                titre,
                color=tc,
                fontsize=taille_titre,
                fontweight="bold" if gras else "normal",
                ha="center",
                va="center",
            )
            self.ax.text(
                x,
                y - h * 0.22,
                sous,
                color=tc,
                fontsize=taille_sous,
                ha="center",
                va="center",
            )
        else:
            self.ax.text(
                x,
                y,
                titre,
                color=tc,
                fontsize=taille_titre,
                fontweight="bold" if gras else "normal",
                ha="center",
                va="center",
            )
        return {"x": x, "y": y, "w": w, "h": h}

    @staticmethod
    def _ancre(b: dict, cote: str) -> tuple[float, float]:
        return {
            "g": (b["x"] - b["w"] / 2, b["y"]),
            "d": (b["x"] + b["w"] / 2, b["y"]),
            "h": (b["x"], b["y"] + b["h"] / 2),
            "b": (b["x"], b["y"] - b["h"] / 2),
        }[cote]

    def fleche(
        self,
        b1: dict,
        c1: str,
        b2: dict,
        c2: str,
        label: str = "",
        couleur: str = C["cacao_dark"],
        style: str = "arc3,rad=0",
        pointe_double: bool = False,
        lw: float = 1.8,
        label_dy: float = 2.2,
        label_dx: float = 0.0,
    ) -> None:
        """Trace une flèche entre deux boîtes (du côté c1 vers le côté c2)."""
        p1 = self._ancre(b1, c1)
        p2 = self._ancre(b2, c2)
        self.ax.add_patch(
            FancyArrowPatch(
                p1,
                p2,
                arrowstyle="<|-|>" if pointe_double else "-|>",
                mutation_scale=14,
                connectionstyle=style,
                color=couleur,
                linewidth=lw,
                shrinkA=3,
                shrinkB=3,
            )
        )
        if label:
            mx, my = (p1[0] + p2[0]) / 2 + label_dx, (p1[1] + p2[1]) / 2 + label_dy
            self.ax.text(
                mx,
                my,
                label,
                color=couleur,
                fontsize=8,
                ha="center",
                va="center",
                bbox={
                    "boxstyle": "round,pad=0.2",
                    "facecolor": C["white"],
                    "edgecolor": "none",
                    "alpha": 0.9,
                },
            )

    def zone(
        self, x: float, y: float, w: float, h: float, label: str, fc: str, ec: str
    ) -> None:
        """Trace une zone de regroupement (couche) avec une étiquette en haut."""
        self.ax.add_patch(
            FancyBboxPatch(
                (x - w / 2, y - h / 2),
                w,
                h,
                boxstyle="round,pad=0.2,rounding_size=1.6",
                facecolor=fc,
                edgecolor=ec,
                linewidth=1.3,
                linestyle="--",
                mutation_aspect=0.6,
            )
        )
        self.ax.text(
            x - w / 2 + 1.8,
            y + h / 2 - 2.2,
            label,
            color=ec,
            fontsize=9,
            fontweight="bold",
            ha="left",
            va="center",
        )

    def sauver(self, nom: str) -> Path:
        chemin = SORTIE / nom
        self.fig.savefig(chemin, dpi=DPI, bbox_inches="tight", facecolor=C["white"])
        plt.close(self.fig)
        return chemin


# --------------------------------------------------------------------------- #
# Schéma 1 — Architecture d'ensemble                                          #
# --------------------------------------------------------------------------- #
def fig_architecture() -> Path:
    t = Toile(
        11,
        6.6,
        "Architecture d'ensemble",
        "Couches strictes : aucune logique métier hors des services ; inférence jamais exposée",
    )
    client = t.boite(
        14, 70, 20, 12, "Producteur", "Navigateur · UI vanilla JS", fc=C["green_light"],
        ec=C["green"],
    )

    # Couche API.
    t.zone(58, 62, 60, 40, "Conteneur API (FastAPI, public)", C["cream"], C["cacao"])
    routers = t.boite(
        40, 73, 22, 11, "Routers /v1", "chat · sessions · feedback",
        fc=C["white"], ec=C["cacao"],
    )
    services = t.boite(
        40, 52, 22, 13, "Services", "guardrails · prompts\ncache · inference",
        fc=C["gold_light"], ec=C["ochre"],
    )
    rag = t.boite(
        78, 73, 20, 11, "RAG", "index dense · sources", fc=C["white"], ec=C["cacao"]
    )
    redis = t.boite(
        78, 52, 20, 11, "Redis", "cache · rate-limit 20/min", fc=C["slate_light"],
        ec=C["slate"],
    )

    # Couche inférence.
    t.zone(58, 22, 60, 22, "Inférence interne (jamais exposée publiquement)", C["cacao_light"], C["cacao_dark"])
    infer = t.boite(
        58, 22, 46, 12, "Moteur d'inférence",
        "vLLM (GPU) · llama.cpp (CPU, GGUF Q4_K_M) — Ministral 3 8B + LoRA",
        fc=C["white"], ec=C["cacao_dark"],
    )

    t.fleche(client, "d", routers, "g", "question / réponse", couleur=C["green"], pointe_double=True)
    t.fleche(routers, "b", services, "h", "garde-fous + cache")
    t.fleche(services, "d", redis, "g", style="arc3,rad=0.0", pointe_double=True)
    t.fleche(routers, "d", rag, "g", "contexte")
    t.fleche(
        services, "b", infer, "h", "prompt ancré  ↕  réponse + disclaimer",
        couleur=C["cacao_dark"], pointe_double=True, style="arc3,rad=0.05",
        label_dx=6, label_dy=0.5,
    )
    return t.sauver("architecture.png")


# --------------------------------------------------------------------------- #
# Schéma 2 — Pipeline de bout en bout                                         #
# --------------------------------------------------------------------------- #
def fig_pipeline() -> Path:
    t = Toile(
        12,
        5.4,
        "Pipeline de bout en bout",
        "Du document officiel au modèle servi — ponctuel sur GPU loué, souverain",
    )
    etapes = [
        ("Sources\nofficielles", "CNRA · ANADER\nCCC · FAO · FIRCA", C["green_light"], C["green"]),
        ("Corpus RAG", "génération ancrée\n+ refus + cure", C["white"], C["cacao"]),
        ("Assemblage", "valide · dédoublonne\ncorpus_entrainement", C["white"], C["cacao"]),
        ("LoRA 4-bit", "QLoRA r=16/α=32\nNF4 · 1 epoch", C["gold_light"], C["ochre"]),
        ("Fusion", "merge_and_export\n+ SHA-256", C["white"], C["cacao"]),
        ("Export GGUF", "Q4_K_M ~5 Go", C["white"], C["cacao"]),
        ("Service", "vLLM / llama.cpp", C["cacao_light"], C["cacao_dark"]),
    ]
    xs = [9.5, 24, 38.5, 53, 67.5, 81, 93]
    y = 56
    boites = []
    for (titre, sous, fc, ec), x in zip(etapes, xs):
        boites.append(
            t.boite(x, y, 13, 22, titre, sous, fc=fc, ec=ec, taille_titre=9.5, taille_sous=7.5)
        )
    for a, b in zip(boites, boites[1:]):
        t.fleche(a, "d", b, "g", lw=1.6)

    # Bandeau « évaluation » qui pilote (F1/F4).
    eval_b = t.boite(
        53, 22, 60, 12, "Évaluation (jeu étendu)",
        "garde-fous = 100 % · qualité · juge GLM-5.2 · latence p50/p95",
        fc=C["red_light"], ec=C["red"], taille_titre=10, taille_sous=8,
    )
    t.fleche(boites[3], "b", eval_b, "h", "recette pilotée (F4)", couleur=C["red"], style="arc3,rad=0.2")
    t.fleche(eval_b, "d", boites[6], "b", "porte de déploiement", couleur=C["red"], style="arc3,rad=-0.25")
    return t.sauver("pipeline.png")


# --------------------------------------------------------------------------- #
# Schéma 3 — Déploiement K3s / Hetzner + CD GitOps                            #
# --------------------------------------------------------------------------- #
def fig_deploiement() -> Path:
    t = Toile(
        11.5,
        6.8,
        "Déploiement souverain — K3s / Hetzner",
        "Service CPU (GGUF) sur un nœud CX53, livraison continue GitOps",
    )
    net = t.boite(
        12, 69, 18, 11, "Internet", "opencacao.\nopenlabconsulting.com",
        fc=C["green_light"], ec=C["green"], taille_sous=7.5,
    )

    t.zone(57, 46, 70, 66, "Cluster K3s — nœud CX53 (16 vCPU / 32 Go)", C["cream"], C["cacao"])
    ingress = t.boite(
        37, 69, 20, 11, "Ingress Traefik", "TLS Let's Encrypt", fc=C["white"], ec=C["cacao"]
    )
    api = t.boite(
        37, 50, 20, 11, "Deployment api", "FastAPI · garde-fous", fc=C["gold_light"], ec=C["ochre"]
    )
    infer = t.boite(
        37, 31, 20, 11, "Deployment inference", "llama.cpp CPU · GGUF",
        fc=C["cacao_light"], ec=C["cacao_dark"],
    )
    redis = t.boite(
        72, 50, 16, 10, "Redis", "cache · RL", fc=C["slate_light"], ec=C["slate"]
    )
    pvc = t.boite(
        72, 30, 24, 15, "PVC /data", "SQLite sessions\njournal · index RAG",
        fc=C["white"], ec=C["cacao"], taille_sous=7.5,
    )

    t.fleche(net, "d", ingress, "g", "HTTPS", couleur=C["green"])
    t.fleche(ingress, "b", api, "h")
    t.fleche(api, "b", infer, "h", "interne :8000")
    t.fleche(api, "d", redis, "g", pointe_double=True)
    t.fleche(api, "d", pvc, "g", style="arc3,rad=-0.25")
    t.fleche(infer, "d", pvc, "g", style="arc3,rad=0.2")

    # Chaîne CD GitOps (bas).
    t.zone(50, 10, 92, 15, "Livraison continue (GitOps)", C["blue_light"], C["blue"])
    cd = [
        ("GitHub", "tag v*"),
        ("release.yml", "build image"),
        ("GHCR", "registre"),
        ("ArgoCD", "sync"),
        ("Cluster", "rollout"),
    ]
    cxs = [12, 31, 50, 69, 88]
    cbs = []
    for (titre, sous), x in zip(cd, cxs):
        cbs.append(
            t.boite(x, 8.5, 15, 8, titre, sous, fc=C["white"], ec=C["blue"],
                    taille_titre=9, taille_sous=7)
        )
    for a, b in zip(cbs, cbs[1:]):
        t.fleche(a, "d", b, "g", couleur=C["blue"], lw=1.4)
    return t.sauver("deploiement.png")


# --------------------------------------------------------------------------- #
# Schéma 4 — Recette pilotée par l'éval (F4)                                  #
# --------------------------------------------------------------------------- #
def fig_sweep() -> Path:
    t = Toile(
        11.5,
        6.2,
        "F4 — Recette LoRA pilotée par l'éval",
        "Balayage d'hyperparamètres → portail garde-fous → meilleur point de contrôle",
    )
    grille = t.boite(
        13, 70, 20, 16, "Grille", "epochs × rang\n× lr × seq",
        fc=C["gold_light"], ec=C["ochre"], taille_sous=8,
    )
    train = t.boite(
        37, 70, 20, 16, "N adaptateurs", "train_lora.py\n(un par combo)",
        fc=C["white"], ec=C["cacao"], taille_sous=8,
    )
    serve = t.boite(
        62, 70, 20, 16, "vLLM base + LoRA", "servis sans fusion\n(économe)",
        fc=C["white"], ec=C["cacao"], taille_sous=8,
    )
    evalb = t.boite(
        86, 70, 20, 16, "Évaluation", "garde-fous · qualité\njuge · latence",
        fc=C["red_light"], ec=C["red"], taille_sous=8,
    )
    t.fleche(grille, "d", train, "g")
    t.fleche(train, "d", serve, "g")
    t.fleche(serve, "d", evalb, "g")

    # Portail (losange) garde-fous.
    cx, cy = 62, 38
    t.ax.add_patch(
        mpatches.FancyBboxPatch(
            (cx - 16, cy - 9), 32, 18, boxstyle="round,pad=0.2,rounding_size=9",
            facecolor=C["red_light"], edgecolor=C["red"], linewidth=1.8, mutation_aspect=0.55,
        )
    )
    t.ax.text(cx, cy + 2.4, "Portail garde-fous", color=C["red"], fontsize=10.5,
              fontweight="bold", ha="center", va="center")
    t.ax.text(cx, cy - 3.0, "garde-fous = 100 % ET 0 fuite de dosage ?", color=C["ink"],
              fontsize=8.5, ha="center", va="center")
    portail = {"x": cx, "y": cy, "w": 32, "h": 18}
    t.fleche(evalb, "b", portail, "d", style="arc3,rad=0.25")

    rejet = t.boite(
        15, 38, 20, 13, "Rejeté", "jamais déployé", fc=C["slate_light"], ec=C["slate"],
        taille_sous=8,
    )
    t.fleche(portail, "g", rejet, "d", "non", couleur=C["red"])

    classer = t.boite(
        62, 14, 30, 13, "Classement", "qualité (juge) puis latence p95",
        fc=C["green_light"], ec=C["green"], taille_sous=8,
    )
    t.fleche(portail, "b", classer, "h", "oui", couleur=C["green"])
    gagnant = t.boite(
        92, 14, 14, 13, "Vainqueur", "fusion → GGUF", fc=C["white"], ec=C["green"],
        taille_titre=9.5, taille_sous=7.5,
    )
    t.fleche(classer, "d", gagnant, "g", couleur=C["green"])
    return t.sauver("sweep_f4.png")


# --------------------------------------------------------------------------- #
# Schéma 5 — Défense en profondeur des garde-fous                            #
# --------------------------------------------------------------------------- #
def fig_garde_fous() -> Path:
    t = Toile(
        10.5,
        5.6,
        "Garde-fous métier — défense en profondeur",
        "Jamais de dosage phytosanitaire · sources obligatoires · redirection ANADER",
    )
    couches = [
        ("1 · Corpus de refus", "41 exemples : dosage, médical, image, hors-filière", C["green_light"], C["green"]),
        ("2 · Prompt système", "règles explicites + disclaimer ANADER imposé", C["gold_light"], C["ochre"]),
        ("3 · Garde-fou d'entrée", "question ré-ancrée au dernier tour → refus", C["cacao_light"], C["cacao"]),
        ("4 · Garde-fou de sortie", "regex anti-dosage chiffré sur la réponse", C["red_light"], C["red"]),
        ("5 · Évaluation continue", "test par règle · 0 fuite tolérée (non négociable)", C["slate_light"], C["slate"]),
    ]
    y = 78
    for i, (titre, sous, fc, ec) in enumerate(couches):
        larg = 86 - i * 9
        t.boite(50, y, larg, 11, titre, sous, fc=fc, ec=ec, taille_titre=11, taille_sous=8.2)
        y -= 14.5
    t.ax.annotate(
        "", xy=(95, 12), xytext=(95, 84),
        arrowprops={"arrowstyle": "-|>", "color": C["cacao_dark"], "lw": 2},
    )
    t.ax.text(97.6, 48, "requête traversante", color=C["cacao_dark"], fontsize=8.5,
              rotation=90, ha="center", va="center")
    return t.sauver("garde_fous.png")


def main() -> None:
    """Génère tous les schémas du dossier."""
    SORTIE.mkdir(parents=True, exist_ok=True)
    for fabrique in (
        fig_architecture,
        fig_pipeline,
        fig_deploiement,
        fig_sweep,
        fig_garde_fous,
    ):
        chemin = fabrique()
        print(f"écrit : {chemin.relative_to(SORTIE.parents[1])}")
    # Évite un avertissement de police inutilisée (chargement paresseux).
    _ = fm.fontManager


if __name__ == "__main__":
    main()
