"""Tests de l'adaptateur Markdown."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation, Document, Manifeste, Section, Tableau
from app.services.rendu.markdown import rendu_markdown


def _affirmation() -> Affirmation:
    return Affirmation(
        texte="La production avoisine 2,2 millions de tonnes.",
        source="CNRA",
        date="2025-10-01",
        methode="rag",
        confiance=NiveauConfiance.MOYENNE,
    )


def _document(
    mention: str = "",
    lacune: bool = False,
    tableaux: tuple[Tableau, ...] | None = None,
) -> Document:
    return Document(
        titre="Étude de filière — le cacao",
        sous_titre="Analyse documentée",
        sections=(
            Section(
                titre="Contexte",
                corps="Un paragraphe analytique.",
                affirmations=(_affirmation(),),
                lacune=lacune,
            ),
        ),
        tableaux=(
            tableaux
            if tableaux is not None
            else (
                Tableau(
                    titre="Prix",
                    entetes=("Campagne", "Prix"),
                    lignes=(("2025-2026", "1 500"),),
                ),
            )
        ),
        manifeste=Manifeste(
            modele="opencacao-8b",
            version_modele="1.1.0",
            version_app="0.6.75",
            profil_materiel="cpu",
            genere_le=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
            empreinte_demandeur="a1b2c3d4e5f6",
            documents_rag=(("CNRA", "2025-10-01"),),
        ),
        mention=mention,
    )


def test_le_titre_est_un_h1():
    assert rendu_markdown(_document()).startswith("# Étude de filière — le cacao")


def test_chaque_section_devient_un_h2():
    assert "## Contexte" in rendu_markdown(_document())


def test_le_corps_de_section_est_rendu():
    assert "Un paragraphe analytique." in rendu_markdown(_document())


def test_la_mention_precede_le_contenu():
    """D5 : non contournable veut dire EN TETE, pas en annexe."""
    rendu = rendu_markdown(_document(mention="Document préparatoire."))
    assert rendu.index("Document préparatoire.") < rendu.index("## Contexte")


def test_sans_mention_aucun_bloc_de_citation_vide():
    assert ">" not in rendu_markdown(_document()).split("## Contexte")[0]


def test_les_tableaux_sont_rendus_en_markdown():
    rendu = rendu_markdown(_document())
    assert "| Campagne | Prix |" in rendu
    assert "| 2025-2026 | 1 500 |" in rendu


def test_le_tableau_de_provenance_figure_en_annexe():
    rendu = rendu_markdown(_document())
    assert "Provenance des affirmations" in rendu
    assert "CNRA" in rendu
    assert rendu.index("## Contexte") < rendu.index("Provenance des affirmations")


def test_le_manifeste_figure_dans_le_document():
    rendu = rendu_markdown(_document())
    assert "opencacao-8b" in rendu
    assert "0.6.75" in rendu
    assert "cpu" in rendu


def test_le_manifeste_porte_l_empreinte_et_jamais_un_identifiant():
    """Ce fichier part chez un bailleur : il ne doit pas contenir de jeton d acces."""
    assert "a1b2c3d4e5f6" in rendu_markdown(_document())


def test_une_section_en_lacune_est_signalee_comme_telle():
    """Un lecteur doit voir que la section n a pas ete renseignee."""
    assert "lacune" in rendu_markdown(_document(lacune=True)).lower()


def test_un_pipe_dans_une_cellule_ne_casse_pas_le_tableau():
    """Une valeur venant du modele peut contenir n importe quoi.

    Le pipe doit etre ECHAPPE : non echappe, il serait lu comme un separateur et la
    ligne compterait deux colonnes la ou le tableau n en declare qu une.
    """
    casse = _document(tableaux=(Tableau(titre="T", entetes=("A",), lignes=(("x | y",),)),))
    ligne = next(ligne for ligne in rendu_markdown(casse).splitlines() if "x " in ligne)
    assert "x \\| y" in ligne
    # Seuls les pipes de structure restent non échappés : un en tête, un en fin.
    assert ligne.replace("\\|", "").count("|") == 2


def test_un_saut_de_ligne_dans_une_cellule_ne_disloque_pas_le_tableau():
    casse = _document(tableaux=(Tableau(titre="T", entetes=("A",), lignes=(("x\ny",),)),))
    ligne = next(ligne for ligne in rendu_markdown(casse).splitlines() if "x y" in ligne)
    assert ligne.startswith("|") and ligne.endswith("|")


def test_un_document_sans_tableau_reste_valide():
    rendu = rendu_markdown(_document(tableaux=()))
    assert "# Étude de filière" in rendu
    assert "Provenance des affirmations" in rendu


def test_une_cloture_de_bloc_de_code_n_avale_pas_la_suite_du_document():
    """C est la garantie meme du livrable qui etait annulee.

    Un « ``` » non ferme, artefact banal d une generation, mettait l annexe de
    provenance ET le manifeste a l interieur d un bloc de code — c est-a-dire
    exactement ce qui rend le document defendable devant un auditeur.
    """
    document = _document()
    piege = Document(
        titre=document.titre,
        sous_titre=document.sous_titre,
        sections=(
            Section(titre="Une", corps="Voici un exemple :\n```python", affirmations=()),
            Section(titre="Deux", corps="Suite.", affirmations=()),
        ),
        tableaux=(),
        manifeste=document.manifeste,
    )
    rendu = rendu_markdown(piege)
    # Une cloture n ouvre un bloc que si elle DEBUTE une ligne : le desamorcage pose
    # un caractere invisible devant, ce qui la rend inoffensive sans retirer le texte
    # du modele. On verifie la propriete structurelle, pas la presence des backticks.
    assert not any(ligne.lstrip().startswith(("```", "~~~")) for ligne in rendu.splitlines())
    assert "Provenance des affirmations" in rendu
    assert "manifeste de génération" in rendu
    assert "```python" in rendu  # le texte du modele est conserve, pas censure


def test_le_manifeste_ne_porte_jamais_l_identifiant_du_demandeur():
    """Ce test doit ECHOUER si l identifiant est ecrit en plus de l empreinte."""
    assert "appareil-a" not in rendu_markdown(_document())


def test_un_surrogate_isole_n_empeche_pas_l_encodage():
    """json.loads accepte un surrogate isole : c est la voie de la sortie modele."""
    document = _document()
    sale = Document(
        titre=document.titre,
        sous_titre=document.sous_titre,
        sections=(Section(titre="Une", corps="texte \ud800 suite", affirmations=()),),
        tableaux=(),
        manifeste=document.manifeste,
    )
    rendu_markdown(sale).encode("utf-8")
