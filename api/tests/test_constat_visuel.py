"""Tests de l'étage 5 — assemblage du constat visuel."""

from __future__ import annotations

import pytest

from app.application.constat_visuel import ServiceConstatVisuel
from app.application.fusion_contextuelle import ContexteParcelle
from app.models.constat import NiveauConfiance, Organe

IMAGES = ((b"\xff\xd8 image", "a" * 64),)


class FauxVision:
    """Port de vision contrôlé par le test (aucun réseau)."""

    def __init__(self, texte: str | None = "Cabosses tachetées de brun sur un tiers.") -> None:
        self.texte = texte
        self.consignes: list[str] = []

    async def decrire(self, images, consigne):
        self.consignes.append(consigne)
        return self.texte

    async def disponible(self) -> bool:
        return self.texte is not None


class FausseInference:
    """Port d'inférence contrôlé par le test."""

    def __init__(
        self,
        reponse: str = (
            "Vos cabosses présentent des taches brunes. Montrez ces photos à votre agent ANADER."
        ),
    ) -> None:
        self.reponse = reponse
        self.appels: list[str] = []

    async def generer(self, question: str, **_: object) -> str:
        self.appels.append(question)
        return self.reponse

    def generer_stream(self, *_: object, **__: object):
        raise NotImplementedError

    async def ready(self) -> bool:
        return True


def _contexte() -> ContexteParcelle:
    return ContexteParcelle(
        pluie_mm_14j=120.0,
        saison="grande saison des pluies",
        localite="Daloa",
        alertes_deforestation=0,
    )


async def test_le_constat_est_produit_avec_ses_observations():
    service = ServiceConstatVisuel(FauxVision(), FausseInference())
    constat = await service.analyser(IMAGES, _contexte())
    assert constat is not None
    assert len(constat.observations) == 1
    assert constat.observations[0].empreinte_image == "a" * 64
    assert constat.texte


async def test_sans_vision_disponible_aucun_constat_n_est_invente():
    """Anti-fabrication : None, pas une description imaginée (v0.6.48)."""
    service = ServiceConstatVisuel(FauxVision(texte=None), FausseInference())
    assert await service.analyser(IMAGES, _contexte()) is None


async def test_sans_image_aucun_constat_et_le_vlm_n_est_pas_appele():
    """Rien à observer : on ne sollicite pas le modèle et on ne rend rien."""
    vision = FauxVision()
    inference = FausseInference()
    assert await ServiceConstatVisuel(vision, inference).analyser((), _contexte()) is None
    assert vision.consignes == []
    assert inference.appels == []


async def test_une_description_qui_nomme_une_maladie_est_rejetee():
    """Le VLM lui-même est contrôlé : on ne rédige pas sur une description compromise."""
    vision = FauxVision(texte="Cabosses atteintes de pourriture brune.")
    inference = FausseInference()
    assert await ServiceConstatVisuel(vision, inference).analyser(IMAGES, _contexte()) is None
    assert inference.appels == []


async def test_un_constat_qui_nomme_une_maladie_est_rejete():
    """D3 : on ne reecrit pas une sortie compromise, on la refuse."""
    inference = FausseInference(reponse="C'est la pourriture brune, traitez vite.")
    service = ServiceConstatVisuel(FauxVision(), inference)
    assert await service.analyser(IMAGES, _contexte()) is None


async def test_un_constat_qui_donne_un_produit_est_rejete():
    inference = FausseInference(reponse="Appliquez un fongicide cuprique sur les cabosses.")
    service = ServiceConstatVisuel(FauxVision(), inference)
    assert await service.analyser(IMAGES, _contexte()) is None


async def test_un_constat_qui_chiffre_un_apport_est_rejete():
    """Aucun produit nommé ici : c'est le garde-fou de sortie existant qui bloque."""
    inference = FausseInference(
        reponse=(
            "Arrosez les jeunes plants avec 10 l par pied, puis montrez ces photos "
            "à votre agent ANADER."
        )
    )
    service = ServiceConstatVisuel(FauxVision(), inference)
    assert await service.analyser(IMAGES, _contexte()) is None


async def test_la_consigne_de_description_est_bien_transmise_au_vlm():
    vision = FauxVision()
    await ServiceConstatVisuel(vision, FausseInference()).analyser(IMAGES, _contexte())
    assert "ne nomme JAMAIS une maladie" in vision.consignes[0]


async def test_les_facteurs_de_contexte_remontent_dans_le_constat():
    service = ServiceConstatVisuel(FauxVision(), FausseInference())
    constat = await service.analyser(IMAGES, _contexte())
    assert constat is not None
    assert any("Daloa" in f for f in constat.facteurs_contexte)


async def test_le_temps_sec_degrade_la_confiance_du_constat():
    service = ServiceConstatVisuel(
        FauxVision(texte="Cabosses portant des taches brunes sur un tiers."), FausseInference()
    )
    sec = ContexteParcelle(
        pluie_mm_14j=1.0, saison="saison sèche", localite="Daloa", alertes_deforestation=0
    )
    constat = await service.analyser(IMAGES, sec)
    assert constat is not None
    assert constat.confiance is NiveauConfiance.FAIBLE
    assert constat.observations[0].confiance is NiveauConfiance.FAIBLE


@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("Cabosse mûre, surface régulière.", Organe.CABOSSE),
        ("Feuilles vert clair, quelques taches.", Organe.FEUILLE),
        ("Tronc portant des chancres humides.", Organe.TRONC),
        ("Vue d'ensemble de la plantation, ombrage dense.", Organe.VUE_ENSEMBLE),
        ("Image difficile à interpréter.", Organe.INDETERMINE),
    ],
)
async def test_l_organe_est_deduit_de_la_description(texte, attendu):
    service = ServiceConstatVisuel(FauxVision(texte=texte), FausseInference())
    constat = await service.analyser(IMAGES, _contexte())
    assert constat is not None
    assert constat.observations[0].organe is attendu
