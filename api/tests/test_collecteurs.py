"""Tests des collecteurs — les sources existantes rendues citables."""

from __future__ import annotations

from app.services.collecteurs import CollecteurOutil, CollecteurRag, empreinte_passage
from app.services.rag import Passage


class OutilVide:
    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        return {}


class OutilFactice:
    def __init__(self) -> None:
        self.arguments: list[dict] = []

    async def invoquer(self, **kwargs: object) -> dict[str, object]:
        self.arguments.append(dict(kwargs))
        return {"pluie_mm_7j": 42, "date": "2026-07-29", "vide": "", "absent": None}


class FauxRag:
    def __init__(self, *passages: Passage) -> None:
        self.passages = list(passages)
        self.requetes: list[str] = []

    async def passages_pour(self, question: str) -> list[Passage]:
        self.requetes.append(question)
        return self.passages


async def test_une_source_indisponible_ne_rend_aucune_affirmation():
    """Dict vide = indisponible. Le moteur en fera un constat de lacune (D4)."""
    assert await CollecteurOutil(OutilVide(), "Open-Meteo", "localite").collecter("Daloa") == ()


async def test_les_donnees_deviennent_des_affirmations_sourcees():
    affirmations = await CollecteurOutil(OutilFactice(), "Open-Meteo", "localite").collecter(
        "Daloa"
    )
    assert len(affirmations) == 1  # les valeurs vides et la date sont écartées
    assert affirmations[0].source == "Open-Meteo"
    assert affirmations[0].date == "2026-07-29"
    assert "42" in affirmations[0].texte


async def test_le_sujet_est_transmis_a_l_outil():
    outil = OutilFactice()
    await CollecteurOutil(outil, "Open-Meteo", "localite").collecter("Daloa")
    assert outil.arguments == [{"localite": "Daloa"}]


async def test_un_outil_sans_argument_est_invoque_a_vide():
    """Le prix officiel ne depend d aucune localite."""
    outil = OutilFactice()
    await CollecteurOutil(outil, "Conseil du Café-Cacao").collecter("Daloa")
    assert outil.arguments == [{}]


async def test_les_passages_rag_portent_leur_source_et_leur_empreinte():
    collecteur = CollecteurRag(FauxRag(Passage(texte="La production…", source="CNRA", score=0.8)))
    affirmations = await collecteur.collecter("production")
    assert affirmations[0].source == "CNRA"
    assert affirmations[0].methode == "rag"
    assert affirmations[0].empreinte == empreinte_passage("La production…")


async def test_un_passage_bien_classe_est_de_confiance_elevee():
    from app.models.constat import NiveauConfiance

    eleve = CollecteurRag(FauxRag(Passage(texte="a", source="CNRA", score=0.9)))
    moyen = CollecteurRag(FauxRag(Passage(texte="a", source="CNRA", score=0.4)))
    assert (await eleve.collecter("q"))[0].confiance is NiveauConfiance.ELEVEE
    assert (await moyen.collecter("q"))[0].confiance is NiveauConfiance.MOYENNE


async def test_un_passage_sans_source_est_ecarte():
    """Defense en profondeur : rien d insourcable n entre dans un document."""
    collecteur = CollecteurRag(FauxRag(Passage(texte="…", source="", score=0.9)))
    assert await collecteur.collecter("production") == ()


async def test_sans_rag_configure_aucune_affirmation():
    assert await CollecteurRag(None).collecter("production") == ()


def test_l_empreinte_est_stable_et_ne_recopie_pas_le_passage():
    """Elle identifie l extrait sans l embarquer dans le manifeste."""
    assert empreinte_passage("un passage") == empreinte_passage("un passage")
    assert empreinte_passage("un passage") != empreinte_passage("un autre")
    assert "passage" not in empreinte_passage("un passage")
