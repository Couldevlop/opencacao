"""Rendu d'un document dépouillé — sans sous-titre, sans mention, sans tableau.

Les trois gabarits livrés portent tous un sous-titre, si bien qu'aucun test ne rendait
jamais un document qui en est dépourvu. Or ``_texte`` rend une chaîne vide quand la clé
``sous_titre`` est absente du YAML : un gabarit sans sous-titre est parfaitement légal,
et ajouter un livrable est un fichier YAML. Le chemin existait donc, sans être exercé —
invisible à la couverture par lignes, révélé par la couverture par branches.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation, Document, Manifeste, Section
from app.services.rendu.diapositives import rendu_pptx
from app.services.rendu.markdown import rendu_markdown
from app.services.rendu.tableur import rendu_excel
from app.services.rendu.word import rendu_word


def _depouille() -> Document:
    """Document légal mais minimal : ni sous-titre, ni mention, ni tableau."""
    return Document(
        titre="Bulletin régional — Daloa",
        sous_titre="",
        sections=(
            Section(
                titre="Conditions météorologiques",
                corps="Les relevés de la période indiquent une pluviométrie régulière.",
                affirmations=(
                    Affirmation(
                        texte="Pluviométrie de 120 mm sur la période.",
                        source="Open-Meteo",
                        date="2026-07-30",
                        methode="meteo",
                        confiance=NiveauConfiance.MOYENNE,
                    ),
                ),
            ),
        ),
        tableaux=(),
        manifeste=Manifeste(
            modele="opencacao-8b",
            version_modele="1.1.0",
            version_app="0.6.75",
            profil_materiel="cpu",
            genere_le=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
            empreinte_demandeur="0123456789ab",
            outils=(("meteo", "2026-07-30T12:00:00Z"),),
        ),
        mention="",
    )


def test_le_markdown_se_rend_sans_sous_titre():
    rendu = rendu_markdown(_depouille())
    assert rendu.startswith("# Bulletin régional — Daloa")
    # Aucune ligne réduite à ses marqueurs d'emphase : c'est ce qu'un sous-titre vide
    # rendu naïvement produirait. Le gras existe ailleurs dans le document, on ne
    # traque donc pas toute emphase, mais l'emphase VIDE.
    lignes = rendu.split("\n")
    assert not [ligne for ligne in lignes if ligne.strip() in {"*", "**", "****", "*_*"}]
    assert "Conditions météorologiques" in rendu


def test_le_word_se_rend_sans_sous_titre():
    octets = rendu_word(_depouille())
    # Un .docx est un ZIP : la signature suffit à dire qu'il est ouvrable.
    assert octets[:2] == b"PK"
    assert len(octets) > 1000


def test_le_pptx_se_rend_sans_sous_titre():
    octets = rendu_pptx(_depouille())
    assert octets[:2] == b"PK"
    assert len(octets) > 1000


def test_l_excel_se_rend_sans_sous_titre():
    octets = rendu_excel(_depouille())
    assert octets[:2] == b"PK"
    assert len(octets) > 1000
