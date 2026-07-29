"""Tests de l'étage 4 — croisement du constat visuel et du contexte de la parcelle."""

from __future__ import annotations

from app.application.fusion_contextuelle import ContexteParcelle, fusionner
from app.models.constat import NiveauConfiance

POURRITURE = "Cabosses présentant des taches brunes étendues et un début de pourriture."
SAIN = "Cabosses de couleur homogène, surface régulière, aucune tache."


def _contexte(**surcharges) -> ContexteParcelle:
    defauts = {
        "pluie_mm_14j": 120.0,
        "saison": "grande saison des pluies",
        "localite": "Daloa",
        "alertes_deforestation": 0,
    }
    return ContexteParcelle(**{**defauts, **surcharges})


def test_humidite_prolongee_conforte_une_observation_de_pourriture():
    fusion = fusionner(POURRITURE, NiveauConfiance.MOYENNE, _contexte(pluie_mm_14j=150.0))
    assert fusion.confiance is NiveauConfiance.MOYENNE
    assert any("plui" in f.lower() for f in fusion.facteurs)


def test_trois_semaines_seches_degradent_une_observation_de_pourriture():
    """C'est le cœur de l'étage 4 : le contexte contredit l'image, on descend d'un cran."""
    fusion = fusionner(POURRITURE, NiveauConfiance.ELEVEE, _contexte(pluie_mm_14j=1.0))
    assert fusion.confiance is NiveauConfiance.MOYENNE
    assert any("sec" in f.lower() for f in fusion.facteurs)


def test_la_degradation_a_un_plancher():
    fusion = fusionner(POURRITURE, NiveauConfiance.FAIBLE, _contexte(pluie_mm_14j=0.0))
    assert fusion.confiance is NiveauConfiance.FAIBLE


def test_une_observation_saine_n_est_pas_degradee_par_la_secheresse():
    fusion = fusionner(SAIN, NiveauConfiance.ELEVEE, _contexte(pluie_mm_14j=1.0))
    assert fusion.confiance is NiveauConfiance.ELEVEE


def test_meteo_absente_degrade_la_confiance_sans_inventer():
    """Souveraineté : sans donnée, on ne conforte rien — on l'écrit."""
    fusion = fusionner(POURRITURE, NiveauConfiance.ELEVEE, _contexte(pluie_mm_14j=None))
    assert fusion.confiance is NiveauConfiance.MOYENNE
    assert any("indisponible" in f.lower() for f in fusion.facteurs)


def test_la_localite_figure_toujours_dans_les_facteurs():
    fusion = fusionner(SAIN, NiveauConfiance.MOYENNE, _contexte(localite="Soubré"))
    assert any("Soubré" in f for f in fusion.facteurs)


def test_les_facteurs_sont_lisibles_par_un_producteur():
    """Pas de jargon ni de code : ces phrases finissent dans le constat."""
    fusion = fusionner(POURRITURE, NiveauConfiance.MOYENNE, _contexte())
    for facteur in fusion.facteurs:
        assert facteur[0].isupper() or facteur[0].isdigit()
        assert "_" not in facteur


def test_sans_saison_connue_aucune_periode_n_est_affichee():
    """Souveraineté : une saison inconnue ne produit pas un facteur vide ou inventé."""
    fusion = fusionner(SAIN, NiveauConfiance.MOYENNE, _contexte(saison=""))
    assert not any("période" in f.lower() for f in fusion.facteurs)
    assert fusion.facteurs == ("Parcelle située à Daloa.",)


def test_une_pluie_intermediaire_ne_conforte_ni_ne_degrade():
    """Entre 5 et 60 mm, la météo ne tranche pas : rien n'est affirmé sur la pluie."""
    fusion = fusionner(POURRITURE, NiveauConfiance.ELEVEE, _contexte(pluie_mm_14j=30.0))
    assert fusion.confiance is NiveauConfiance.ELEVEE
    assert not any("mm de pluie" in f for f in fusion.facteurs)


def test_aucun_facteur_ne_nomme_une_maladie():
    """D3 : la fusion explique un contexte, elle ne conclut jamais à une maladie."""
    fusion = fusionner(POURRITURE, NiveauConfiance.ELEVEE, _contexte(pluie_mm_14j=150.0))
    joint = " ".join(fusion.facteurs).lower()
    for interdit in ("pourriture brune", "phytophthora", "swollen shoot", "mirides"):
        assert interdit not in joint
