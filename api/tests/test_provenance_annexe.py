"""L'annexe de provenance doit se lire comme une annexe, pas comme un copier-coller.

Constat sur une étude réellement produite en production le 19/08/2026 (« Opportunités
pour un nouvel acheteur… ») : l'annexe recopiait les passages du RAG *mot pour mot*,
soit des blocs de 600 caractères, et **les mêmes revenaient dans presque toutes les
sections** — le corpus renvoyant les mêmes extraits pour chaque requête.

Deux conséquences, toutes deux mauvaises devant un bailleur :

* l'annexe fait quatre pages de redites, ce qui donne l'impression d'un document
  gonflé artificiellement ;
* les passages du corpus sont en registre *conseil au producteur* (« contactez votre
  agent ANADER »). Le CORPS des sections, lui, est bien en prose analytique — c'est
  vérifié ailleurs — mais l'annexe réintroduisait ce registre par la bande, au beau
  milieu d'un document institutionnel.

Le tableau de provenance sert à répondre à « d'où vient ce chiffre ? ». Une phrase
suffit ; le passage entier appartient à la source, pas au tableau.
"""

from __future__ import annotations

from app.application.provenance import LONGUEUR_AFFIRMATION, tableau_de_provenance
from app.models.domain import Confiance
from app.models.rapport import Affirmation, Document, Manifeste, Section

PASSAGE = (
    "Depuis octobre 2012, le gouvernement ivoirien a mis en place une réforme pour "
    "améliorer la commercialisation du cacao et garantir des revenus plus stables aux "
    "producteurs. Cette réforme vise à réguler le marché intérieur et à offrir une "
    "meilleure rémunération grâce à un mécanisme plus équitable. Pour en savoir plus, "
    "consultez votre agent ANADER local, qui pourra vous guider."
)


def _affirmation(texte: str, source: str = "ANADER") -> Affirmation:
    return Affirmation(
        texte=texte, source=source, date="", methode="rag", confiance=Confiance.ELEVEE
    )


def _manifeste() -> Manifeste:
    champs = {n: "" for n in Manifeste.__dataclass_fields__}
    return Manifeste(**champs)


def _document(*sections: Section) -> Document:
    return Document(
        titre="Étude",
        sous_titre="",
        sections=tuple(sections),
        tableaux=(),
        manifeste=_manifeste(),
    )


def test_une_affirmation_citee_dans_plusieurs_sections_ne_fait_qu_une_ligne() -> None:
    """Quatre sections nourries du même extrait donnaient quatre lignes identiques."""
    doc = _document(
        Section(titre="Structure du marché", corps="…", affirmations=(_affirmation(PASSAGE),)),
        Section(titre="Prix officiel", corps="…", affirmations=(_affirmation(PASSAGE),)),
        Section(titre="Débouchés", corps="…", affirmations=(_affirmation(PASSAGE),)),
    )

    tableau = tableau_de_provenance(doc)

    assert len(tableau.lignes) == 1


def test_la_ligne_unique_nomme_toutes_les_sections_concernees() -> None:
    """Fusionner ne doit pas perdre l'information : on dit où l'affirmation a servi."""
    doc = _document(
        Section(titre="Structure du marché", corps="…", affirmations=(_affirmation(PASSAGE),)),
        Section(titre="Prix officiel", corps="…", affirmations=(_affirmation(PASSAGE),)),
    )

    sections = tableau_de_provenance(doc).lignes[0][0]

    assert "Structure du marché" in sections
    assert "Prix officiel" in sections


def test_l_affirmation_est_ramenee_a_une_phrase_lisible() -> None:
    """Un bloc de 600 caractères dans une cellule de tableau ne se lit pas."""
    doc = _document(Section(titre="S", corps="…", affirmations=(_affirmation(PASSAGE),)))

    texte = tableau_de_provenance(doc).lignes[0][1]

    assert len(texte) <= LONGUEUR_AFFIRMATION + 1  # + le caractère de troncature
    assert texte.startswith("Depuis octobre 2012")


def test_une_affirmation_courte_n_est_pas_tronquee() -> None:
    """Contre-épreuve : sans elle, un code qui coupe tout resterait vert."""
    courte = "prix_bord_champ_fcfa_kg : 1200"
    doc = _document(Section(titre="S", corps="…", affirmations=(_affirmation(courte),)))

    assert tableau_de_provenance(doc).lignes[0][1] == courte


def test_deux_affirmations_distinctes_restent_deux_lignes() -> None:
    """La déduplication ne doit pas confondre deux sources différentes."""
    doc = _document(
        Section(
            titre="S",
            corps="…",
            affirmations=(_affirmation("Premier fait."), _affirmation("Second fait.")),
        )
    )

    assert len(tableau_de_provenance(doc).lignes) == 2


def test_le_meme_texte_de_deux_sources_reste_distinct() -> None:
    """Deux organismes qui affirment la même chose, c'est une corroboration : on la garde."""
    doc = _document(
        Section(
            titre="S",
            corps="…",
            affirmations=(_affirmation("Fait.", "ANADER"), _affirmation("Fait.", "FAO")),
        )
    )

    assert len(tableau_de_provenance(doc).lignes) == 2


def test_l_ordre_des_sections_est_conserve() -> None:
    """Une annexe doit suivre le document, pas un ordre de hachage."""
    doc = _document(
        Section(titre="Première", corps="…", affirmations=(_affirmation("A."),)),
        Section(titre="Seconde", corps="…", affirmations=(_affirmation("B."),)),
    )

    lignes = tableau_de_provenance(doc).lignes

    assert lignes[0][1] == "A."
    assert lignes[1][1] == "B."
