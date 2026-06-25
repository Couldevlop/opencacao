"""Données sourcées + génération des visuels du business plan OpenLab Consulting.

Produit les graphiques (barres, camemberts) et schémas d'architecture du
portefeuille de projets R&D IA d'OpenLab Consulting (Côte d'Ivoire d'abord), dans
``docs/img_bp/``. Toutes les statistiques portent une source nommée + URL (cf.
``DATA`` et ``SOURCES``), réutilisées par ``scripts/build_businessplan.py``.

Usage : python scripts/gen_bp_assets.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

SORTIE = Path(__file__).resolve().parents[1] / "docs" / "img_bp"
DPI = 200
plt.rcParams["font.family"] = "DejaVu Sans"

# Palette marque OpenLab (orange + anthracite) + accents.
OR = "#EA5B13"
OR_D = "#B8410A"
OR_L = "#FCE7D8"
DARK = "#1F1F1F"
GREY = "#6B6B6B"
BLUE = "#1565C0"
GREEN = "#2E7D32"
PURPLE = "#6A1B9A"
TEAL = "#00838F"
RED = "#C62828"
LIGHT = "#F2F2F2"
WHITE = "#FFFFFF"

# --------------------------------------------------------------------------- #
# Statistiques sourcées (45 points — recherche vérifiée). Chaque entrée :       #
# (libellé, valeur, année, source, url, confiance).                            #
# --------------------------------------------------------------------------- #
DATA = {
    "cote_ivoire": [
        ("PIB (nominal)", "86,9 Md$", "2024", "Banque mondiale (WDI)", "https://data.worldbank.org/indicator/NY.GDP.MKTP.CD?locations=CI", "élevée"),
        ("Croissance du PIB", "6,0 %/an", "2024", "Banque mondiale", "https://data.worldbank.org/indicator/NY.GDP.MKTP.KD.ZG?locations=CI", "élevée"),
        ("Population", "31,9 M", "2024", "Banque mondiale (WDI)", "https://data.worldbank.org/indicator/SP.POP.TOTL?locations=CI", "élevée"),
        ("Pénétration Internet", "38,4 % (11,2 M)", "2024", "DataReportal — Digital 2024", "https://datareportal.com/reports/digital-2024-cote-divoire", "élevée"),
        ("Connexions mobiles", "149 % (43,6 M)", "2024", "GSMA Intelligence / DataReportal", "https://datareportal.com/reports/digital-2024-cote-divoire", "élevée"),
        ("Mobile money / PIB", "> 5 %", "2024", "GSMA State of the Industry", "https://www.gsma.com/sotir/", "moyenne"),
    ],
    "ia_data": [
        ("Marché IA Afrique 2025", "4,51 Md$", "2025", "Mastercard (via Ecofin)", "https://www.ecofinagency.com/news-digital/1308-48038", "élevée"),
        ("Marché IA Afrique 2030", "16,53 Md$", "2030", "Statista / Mastercard", "https://www.statista.com/outlook/tmo/artificial-intelligence/africa", "élevée"),
        ("TCAC marché IA Afrique", "27,4 %", "2025-2030", "Mastercard", "https://www.ecofinagency.com/news-digital/1308-48038", "élevée"),
        ("Marché data centers Afrique", "1,94 Md$ → 4,36 Md$", "2025-2031", "Mordor Intelligence", "https://www.mordorintelligence.com/industry-reports/africa-data-center-market", "élevée"),
        ("Valeur annuelle IA générative Afrique", "jusqu'à 100 Md$/an", "2024", "McKinsey & Company", "https://www.mckinsey.com/capabilities/quantumblack/our-insights/leading-not-lagging-africas-gen-ai-opportunity", "moyenne"),
    ],
    "assurance": [
        ("Marché mondial assurance agricole", "41,5 Md$ → 70,0 Md$", "2024-2033", "Grand View Research", "https://www.grandviewresearch.com/industry-analysis/agriculture-insurance-market-report", "élevée"),
        ("Primes assurance agricole Afrique", "320 M$ (~1,6 % du non-vie)", "2020", "Atlas Magazine", "https://www.atlas-mag.net/en/article/agricultural-insurance-products-and-schemes", "moyenne"),
        ("Agriculture / PIB Côte d'Ivoire", "17,9 %", "2024", "Banque mondiale", "https://tradingeconomics.com/cote-d-ivoire/agriculture-value-added-percent-of-gdp-wb-data.html", "élevée"),
        ("Emploi agricole Côte d'Ivoire", "~45 % de la pop. active", "2021", "Banque mondiale", "https://www.worldbank.org/en/country/cotedivoire/overview", "moyenne"),
        ("Pénétration assurance (petits exploitants Afrique)", "~1 % assurés", "2023", "One Acre Fund / Swiss Re", "https://oneacrefund.org/", "moyenne"),
    ],
    "compliance": [
        ("Marché mondial RegTech", "20,7 Md$ → 44,1 Md$", "2025-2030", "Mordor Intelligence", "https://www.mordorintelligence.com/industry-reports/global-regtech-industry", "élevée"),
        ("Coût conformité financière (US/CA)", "61 Md$/an", "2023", "LexisNexis Risk Solutions", "https://risk.lexisnexis.com/about-us/press-room/press-release/20240221-true-cost-of-compliance-us-ca", "élevée"),
        ("Coût conformité financière (EMEA)", "85 Md$/an", "2023", "LexisNexis Risk Solutions", "https://risk.lexisnexis.com/global/en/about-us/press-room/press-release/20240306-true-cost-of-compliance-emea", "élevée"),
        ("Amendes AML/KYC mondiales", "4,6 Md$", "2024", "Fenergo", "https://resources.fenergo.com/newsroom/", "élevée"),
    ],
    "procurement": [
        ("Commande publique / PIB (Afrique)", "17 %", "2023", "Banque mondiale", "https://blogs.worldbank.org/en/governance/expanding-role-public-procurement-africas-economic-development", "moyenne"),
        ("Commande publique / PIB (mondial)", "~15 %", "2019", "Banque mondiale / OCP", "https://blogs.worldbank.org/en/voices/hidden-1-trillion-halting-waste-public-procurement", "élevée"),
        ("Commande publique / PIB (OCDE)", "13 %", "2025", "OCDE — Government at a Glance", "https://www.oecd.org/en/publications/government-at-a-glance-2025_0efd0bcd-en/", "élevée"),
        ("Marché logiciels d'achat", "10,7 Md$ → 17,1 Md$", "2026-2031", "Mordor Intelligence", "https://www.mordorintelligence.com/industry-reports/procurement-software-market", "élevée"),
        ("Pertes par corruption (par contrat)", "10 à 25 %", "2021", "Transparency International", "https://knowledgehub.transparency.org/guide/topic-guide-on-public-procurement/4890", "moyenne"),
        ("Gaspillage commande publique mondiale", "~25 % (~1 000 Md$ récupérables)", "2020", "Banque mondiale", "https://blogs.worldbank.org/en/voices/hidden-1-trillion-halting-waste-public-procurement", "élevée"),
    ],
    "govtech": [
        ("Marché mondial GovTech", "825 Md$ → 3 091 Md$", "2026-2035", "Business Research Insights", "https://www.businessresearchinsights.com/market-reports/govtech-market-102878", "moyenne"),
        ("TCAC GovTech", "15,8 %", "2026-2035", "Business Research Insights", "https://www.businessresearchinsights.com/market-reports/govtech-market-102878", "moyenne"),
        ("Dépense IT public + éducation (monde)", "824 Md$ (> 1 000 Md$ d'ici 2028)", "2024", "Gartner", "https://www.gartner.com/en/documents/5361663", "élevée"),
        ("Indice e-gouvernement Afrique (EGDI)", "0,42 (vs 0,64 monde)", "2024", "ONU — E-Government Survey 2024", "https://www.capmad.com/technology-en/egdi-2024-african-e-administration-in-constant-evolution/", "moyenne"),
    ],
    "cocoa": [
        ("Part production mondiale de cacao", "44,4 %", "2023", "ICCO", "https://www.icco.org/statistics/", "élevée"),
        ("Production de fèves", "1,75 Mt", "2023/24", "ICCO / USDA FAS", "https://www.icco.org/november-2024-quarterly-bulletin-of-cocoa-statistics/", "élevée"),
        ("Valeur export fèves de cacao", "3,33 Md$", "2022", "OEC", "https://oec.world/en/profile/bilateral-product/cocoa-beans/reporter/civ", "moyenne"),
        ("Producteurs de cacao", "~1 million (~6 M dépendants)", "2023", "ICCO", "https://www.icco.org/statistics/", "moyenne"),
        ("Cacao / PIB", "14 %", "2024", "FAO (Conseil Café-Cacao)", "https://www.fao.org/investment-centre/", "moyenne"),
        ("Cacao / recettes d'exportation", "45 %", "2024", "FAO (Conseil Café-Cacao)", "https://www.fao.org/investment-centre/", "moyenne"),
    ],
}

# Liste à plat des sources (annexe).
SOURCES = []
for theme, rows in DATA.items():
    for libelle, val, an, src, url, conf in rows:
        SOURCES.append((libelle, val, an, src, url))

# Portefeuille (note du document d'opportunités — projets hors cacao = business plan).
PORTFOLIO = [
    ("Data Factory CI", 95, OR),
    ("IA Assurance Agricole", 85, GREEN),
    ("OpenCompliance AI", 80, BLUE),
    ("IA Marchés Publics", 75, PURPLE),
    ("OpenGov AI", 70, TEAL),
]


# =========================================================================== #
# Graphiques                                                                  #
# =========================================================================== #
def _style_ax(ax, title, source):
    ax.set_title(title, fontsize=12, fontweight="bold", color=DARK, loc="left", pad=10)
    ax.figure.text(0.01, 0.01, f"Source : {source}", fontsize=7, color=GREY, style="italic")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color("#CCCCCC")
    ax.spines["bottom"].set_color("#CCCCCC")
    ax.tick_params(colors=GREY, labelsize=9)


def bar(nom, title, labels, values, source, *, colors=None, fmt="{:.0f}", unit="", horiz=False):
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    colors = colors or [OR] * len(values)
    if horiz:
        y = range(len(labels))
        ax.barh(y, values, color=colors, edgecolor="white")
        ax.set_yticks(y)
        ax.set_yticklabels(labels)
        ax.invert_yaxis()
        for i, v in enumerate(values):
            ax.text(v, i, "  " + fmt.format(v) + unit, va="center", ha="left",
                    fontsize=9, fontweight="bold", color=DARK)
        ax.set_xlim(0, max(values) * 1.18)
    else:
        x = range(len(labels))
        ax.bar(x, values, color=colors, edgecolor="white", width=0.62)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        for i, v in enumerate(values):
            ax.text(i, v, fmt.format(v) + unit, va="bottom", ha="center",
                    fontsize=9, fontweight="bold", color=DARK)
        ax.set_ylim(0, max(values) * 1.18)
    _style_ax(ax, title, source)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    p = SORTIE / nom
    fig.savefig(p, dpi=DPI, facecolor=WHITE)
    plt.close(fig)
    return p


def pie(nom, title, labels, values, source, colors):
    fig, ax = plt.subplots(figsize=(5.4, 3.8))
    wedges, _texts, autotexts = ax.pie(
        values, labels=labels, colors=colors, autopct="%1.1f%%", startangle=90,
        wedgeprops={"edgecolor": "white", "linewidth": 1.5}, textprops={"fontsize": 9, "color": DARK},
        pctdistance=0.72,
    )
    for at in autotexts:
        at.set_color("white")
        at.set_fontweight("bold")
    ax.set_title(title, fontsize=12, fontweight="bold", color=DARK, pad=10)
    fig.text(0.01, 0.01, f"Source : {source}", fontsize=7, color=GREY, style="italic")
    fig.tight_layout()
    p = SORTIE / nom
    fig.savefig(p, dpi=DPI, facecolor=WHITE)
    plt.close(fig)
    return p


# =========================================================================== #
# Schémas d'architecture (parametriques)                                      #
# =========================================================================== #
def _box(ax, x, y, w, h, title, sub, fc, ec, fs=9.5):
    ax.add_patch(FancyBboxPatch((x - w / 2, y - h / 2), w, h,
                 boxstyle="round,pad=0.2,rounding_size=1.2", facecolor=fc,
                 edgecolor=ec, linewidth=1.5, mutation_aspect=0.55))
    if sub:
        ax.text(x, y + h * 0.16, title, ha="center", va="center", fontsize=fs, fontweight="bold", color=DARK)
        ax.text(x, y - h * 0.24, sub, ha="center", va="center", fontsize=fs - 1.8, color=DARK)
    else:
        ax.text(x, y, title, ha="center", va="center", fontsize=fs, fontweight="bold", color=DARK)
    return {"x": x, "y": y, "w": w, "h": h}


def _arrow(ax, b1, b2, color=DARK):
    p1 = (b1["x"] + b1["w"] / 2, b1["y"])
    p2 = (b2["x"] - b2["w"] / 2, b2["y"])
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle="-|>", mutation_scale=12,
                 color=color, linewidth=1.5, shrinkA=2, shrinkB=2))


def architecture(nom, titre, sources, pipeline, consommateurs, gouvernance, accent=OR):
    """Schéma générique : sources → pipeline IA → consommateurs + bande gouvernance."""
    fig, ax = plt.subplots(figsize=(11, 5.6))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    fig.patch.set_facecolor(WHITE)
    # bandeau
    ax.add_patch(mpatches.Rectangle((0, 92), 100, 8, facecolor=DARK))
    ax.add_patch(mpatches.Rectangle((0, 90.8), 100, 1.2, facecolor=accent))
    ax.text(2.5, 96, titre, color=WHITE, fontsize=14, fontweight="bold", va="center")
    ax.text(97.5, 96, "OpenLab Consulting", color="#F3C9A8", fontsize=10, fontweight="bold", va="center", ha="right")

    def col(items, x, fc, ec):
        n = len(items)
        top, bot = 74, 34
        ys = [54] if n == 1 else [top - i * ((top - bot) / (n - 1)) for i in range(n)]
        return [_box(ax, x, y, 18, 11, t, s, fc, ec) for (t, s), y in zip(items, ys)]

    src_b = col(sources, 12, OR_L, accent)
    n = len(pipeline)
    left, right = 31, 81
    px = [55] if n == 1 else [left + i * ((right - left) / (n - 1)) for i in range(n)]
    pipe_b = [_box(ax, x, 52, 13.5, 13, t, s, "#FFFFFF", accent, fs=8.4) for (t, s), x in zip(pipeline, px)]
    cons_b = col(consommateurs, 90, LIGHT, GREY)

    for b in src_b:
        _arrow(ax, b, pipe_b[0], accent)
    for a, b in zip(pipe_b, pipe_b[1:]):
        _arrow(ax, a, b, DARK)
    for b in cons_b:
        _arrow(ax, pipe_b[-1], b, GREEN)

    # bande gouvernance / souveraineté
    ax.add_patch(FancyBboxPatch((6, 8), 88, 12, boxstyle="round,pad=0.2,rounding_size=2",
                 facecolor="#EAF1F8", edgecolor=BLUE, linewidth=1.3, linestyle="--", mutation_aspect=0.5))
    ax.text(50, 14, gouvernance, ha="center", va="center", fontsize=9, color=BLUE, fontweight="bold")
    fig.text(0.5, 0.015, "Architecture souveraine — hébergement Côte d'Ivoire, données résidentes",
             ha="center", fontsize=7.5, color=GREY, style="italic")
    p = SORTIE / nom
    fig.savefig(p, dpi=DPI, bbox_inches="tight", facecolor=WHITE)
    plt.close(fig)
    return p


def main() -> None:
    SORTIE.mkdir(parents=True, exist_ok=True)

    # --- Graphiques portefeuille / macro ---
    bar("portfolio.png", "Portefeuille R&D — probabilité de réussite",
        [p[0] for p in PORTFOLIO], [p[1] for p in PORTFOLIO],
        "Évaluation OpenLab Consulting", colors=[p[2] for p in PORTFOLIO],
        fmt="{:.0f}", unit=" %", horiz=True)
    pie("cocoa_share.png", "Part de la Côte d'Ivoire dans la production mondiale de cacao",
        ["Côte d'Ivoire", "Reste du monde"], [44.4, 55.6], "ICCO, 2023", [OR, "#D9CFC8"])
    bar("ai_africa.png", "Marché de l'IA en Afrique (Md$)", ["2025", "2030"], [4.51, 16.53],
        "Mastercard / Statista — TCAC 27,4 %", colors=[OR_L, OR], fmt="{:.1f}", unit="")

    # --- Par projet ---
    bar("assurance_penetration.png", "Pénétration de l'assurance agricole (% d'exploitants)",
        ["Afrique", "Am. latine", "Asie"], [1, 15, 50],
        "One Acre Fund / Swiss Re", colors=[RED, OR, GREEN], fmt="{:.0f}", unit=" %")
    bar("regtech.png", "Marché mondial de la RegTech (Md$)", ["2025", "2030"], [20.7, 44.1],
        "Mordor Intelligence — TCAC 16,4 %", colors=[OR_L, OR], fmt="{:.1f}")
    bar("compliance_cost.png", "Coût annuel de la conformité financière (Md$)",
        ["US / Canada", "EMEA"], [61, 85], "LexisNexis Risk Solutions, 2023",
        colors=[BLUE, OR], fmt="{:.0f}")
    bar("procurement_gdp.png", "Commande publique en % du PIB",
        ["OCDE", "Monde", "Afrique"], [13, 15, 17], "OCDE / Banque mondiale",
        colors=[GREY, OR_L, OR], fmt="{:.0f}", unit=" %")
    pie("procurement_waste.png", "Gaspillage estimé de la commande publique mondiale",
        ["Gaspillé / perdu", "Efficace"], [25, 75], "Banque mondiale, 2020", [RED, "#CFE3CF"])
    bar("govtech.png", "Marché mondial de la GovTech (Md$)", ["2026", "2035"], [825, 3091],
        "Business Research Insights — TCAC 15,8 %", colors=[OR_L, OR], fmt="{:.0f}")
    bar("egdi.png", "Indice de e-gouvernement (EGDI, 0-1)", ["Afrique", "Monde"], [0.42, 0.64],
        "ONU — E-Government Survey 2024", colors=[OR, GREY], fmt="{:.2f}")

    # --- Schémas d'architecture (5) ---
    architecture(
        "archi_datafactory.png", "Data Factory CI — plateforme Data/IA",
        [("Sources", "ERP, IoT"), ("Bases métier", "SQL, API"), ("Flux", "temps réel")],
        [("Ingestion", "ETL"), ("Data Lake", "gouverné"), ("MLOps", "modèles"), ("Service", "API / BI")],
        [("Métiers", "décision"), ("Apps IA", "production")],
        "Gouvernance des données · lignage · sécurité · souveraineté", accent=OR)
    architecture(
        "archi_assurance.png", "IA Assurance Agricole — scoring du risque",
        [("Satellite", "NDVI, pluie"), ("Rendements", "historique"), ("Parcelles", "géodonnées")],
        [("Préparation", "features"), ("Scoring", "risque / indice"), ("Indice", "déclencheur"), ("API", "intégration")],
        [("Assureurs", "tarification"), ("Banques / IMF", "crédit")],
        "Indice paramétrique auditable · conformité assurantielle · données résidentes", accent=GREEN)
    architecture(
        "archi_compliance.png", "OpenCompliance AI — conformité réglementaire",
        [("Réglements", "textes, lois"), ("Documents", "procédures"), ("Veille", "mises à jour")],
        [("Index RAG", "recherche"), ("Assistant", "LLM cité"), ("Workflows", "contrôles"), ("Audit", "reporting")],
        [("Conformité", "équipes"), ("Direction", "tableau de bord")],
        "Réponses citées & traçables · journal d'audit · LLM souverain", accent=BLUE)
    architecture(
        "archi_marches.png", "IA Marchés Publics — veille & réponse",
        [("Appels d'offres", "portails"), ("Référentiels", "entreprise"), ("Historique", "marchés")],
        [("Extraction", "NLP"), ("Analyse", "matching"), ("Assistance", "rédaction"), ("Alertes", "échéances")],
        [("PME", "soumission"), ("Acheteurs", "transparence")],
        "Détection d'opportunités · aide à la réponse · équité & traçabilité", accent=PURPLE)
    architecture(
        "archi_gov.png", "OpenGov AI — services publics augmentés",
        [("Citoyens", "multicanal"), ("Agents", "back-office"), ("Bases", "référentiels")],
        [("Assistant", "FR + langues"), ("RAG", "procédures"), ("Intégrations", "back-office"), ("Analytics", "pilotage")],
        [("Administrations", "efficacité"), ("Usagers", "accès")],
        "Accès équitable · souveraineté · conformité données personnelles", accent=TEAL)

    print(f"OK -> {SORTIE} (14 visuels)")


if __name__ == "__main__":
    main()
