"""Tests des gabarits déclaratifs de livrables."""

from __future__ import annotations

import pytest

from app.services.gabarits import (
    SOURCES_CONNUES,
    GabaritInconnu,
    GabaritInvalide,
    charger_gabarit,
    lire_gabarit,
    lister_gabarits,
    vider_cache,
)


@pytest.fixture(autouse=True)
def cache_neuf():
    """Les gabarits sont mis en cache : un test qui deplace le dossier doit l oublier."""
    vider_cache()
    yield
    vider_cache()


def test_les_trois_gabarits_de_la_spec_sont_livres():
    assert set(lister_gabarits()) == {"etude_filiere", "dossier_parcelle", "bulletin_regional"}


def test_sans_dossier_de_gabarits_la_liste_est_vide(monkeypatch, tmp_path):
    """Deploiement incomplet : on rend une liste vide, on ne leve pas au demarrage."""
    from app.services import gabarits as module

    monkeypatch.setattr(module, "_DOSSIER", tmp_path / "absent")
    assert module.lister_gabarits() == ()
    with pytest.raises(GabaritInconnu):
        module.charger_gabarit("etude_filiere")


def test_un_gabarit_inconnu_est_refuse():
    with pytest.raises(GabaritInconnu):
        charger_gabarit("gabarit-qui-n-existe-pas")


@pytest.mark.parametrize(
    "identifiant",
    ["../../../etc/passwd", "../sources_agro", "etude_filiere/../../secret", "a\x00b"],
)
def test_un_identifiant_hors_liste_blanche_est_refuse(identifiant):
    """L identifiant vient d une requete HTTP : jamais de chemin assemble."""
    with pytest.raises(GabaritInconnu):
        charger_gabarit(identifiant)


def test_l_etude_de_filiere_a_des_sections_ordonnees():
    """L ordre EST le plan du document : un tri ou une deduplication le casserait."""
    gabarit = charger_gabarit("etude_filiere")
    assert gabarit.identifiant == "etude_filiere"
    assert [section.titre for section in gabarit.sections] == [
        "Contexte de la filière",
        "État des connaissances agronomiques",
        "Conditions climatiques observées",
        "Marché et prix",
        "Pression sur le couvert forestier",
        "Limites de la présente étude",
    ]


def test_chaque_section_declare_des_sources_connues():
    """Une source inconnue serait silencieusement ignoree a la redaction."""
    for identifiant in lister_gabarits():
        for section in charger_gabarit(identifiant).sections:
            assert set(section.sources) <= SOURCES_CONNUES


def test_le_dossier_de_parcelle_porte_la_mention_preparatoire():
    """D5 : ce dossier n est PAS une declaration de conformite."""
    mention = charger_gabarit("dossier_parcelle").mention.lower()
    assert "préparatoire" in mention
    assert "ne constitue" in mention


def test_les_autres_gabarits_n_ont_pas_de_mention():
    """La mention D5 est propre au dossier de parcelle, pas un ornement global."""
    assert charger_gabarit("etude_filiere").mention == ""
    assert charger_gabarit("bulletin_regional").mention == ""


def test_un_gabarit_sans_section_est_invalide():
    with pytest.raises(GabaritInvalide):
        lire_gabarit({"id": "vide", "titre": "T", "sections": []})


def test_un_gabarit_sans_titre_est_invalide():
    with pytest.raises(GabaritInvalide):
        lire_gabarit({"id": "x", "sections": [{"titre": "S", "sources": ["rag"]}]})


def test_un_gabarit_dont_une_section_n_a_pas_de_titre_est_invalide():
    """Le modele n emet jamais un titre : s il manque, personne ne le fournira."""
    with pytest.raises(GabaritInvalide):
        lire_gabarit({"id": "x", "titre": "T", "sections": [{"sources": ["rag"]}]})


def test_un_gabarit_declarant_une_source_inconnue_est_invalide():
    with pytest.raises(GabaritInvalide):
        lire_gabarit(
            {"id": "x", "titre": "T", "sections": [{"titre": "S", "sources": ["horoscope"]}]}
        )


def test_une_section_sans_source_est_valide():
    """« Limites de l etude » ne mobilise rien : c est licite, et ce sera une lacune."""
    gabarit = lire_gabarit({"id": "x", "titre": "T", "sections": [{"titre": "Limites"}]})
    assert gabarit.sections[0].sources == ()


def test_le_titre_accepte_un_champ_a_substituer():
    """« Étude de filière — {sujet} » : la substitution est faite par le moteur."""
    assert "{sujet}" in charger_gabarit("etude_filiere").titre


@pytest.mark.parametrize("identifiant", ["etude_filiere", "dossier_parcelle", "bulletin_regional"])
def test_les_titres_livres_se_substituent_sans_surprise(identifiant):
    """Un champ inattendu casserait en KeyError au rendu, tres loin du chargement."""
    gabarit = charger_gabarit(identifiant)
    assert gabarit.titre.format(sujet="X")
    assert gabarit.sous_titre.format(sujet="X") or gabarit.sous_titre == ""


@pytest.mark.parametrize(
    "titre",
    [
        "{sujet.__class__.__mro__}",
        "{sujet[0]}",
        "{0}",
        "{}",
        "{autre}",
        "{sujet:>1000000000}",
    ],
)
def test_un_champ_de_format_hors_liste_est_refuse(titre):
    """CWE-134 : la marche d attributs et le DoS par spec de format se ferment ici."""
    with pytest.raises(GabaritInvalide):
        lire_gabarit({"id": "x", "titre": titre, "sections": [{"titre": "S"}]})


def test_des_accolades_desequilibrees_sont_refusees():
    with pytest.raises(GabaritInvalide):
        lire_gabarit({"id": "x", "titre": "50 % {sujet", "sections": [{"titre": "S"}]})


def test_le_sous_titre_est_verifie_comme_le_titre():
    with pytest.raises(GabaritInvalide):
        lire_gabarit(
            {
                "id": "x",
                "titre": "T",
                "sous_titre": "{sujet.__class__}",
                "sections": [{"titre": "S"}],
            }
        )


@pytest.mark.parametrize(
    "charge",
    [
        ["une", "liste"],
        "une chaine",
        42,
        {"titre": "T", "sections": "abc"},
        {"titre": "T", "sections": {"un": {"titre": "S"}}},
        {"titre": "T", "sections": ["texte"]},
        {"titre": "T", "sections": [None]},
        {"titre": "T", "sections": 5},
        {"titre": {"a": 1}, "sections": [{"titre": "S"}]},
        {"titre": "T", "sections": [{"titre": "S", "sources": "rag"}]},
        {"titre": "T", "sections": [{"titre": "S", "sources": 3}]},
    ],
)
def test_une_charge_mal_typee_donne_une_erreur_metier(charge):
    """Sans cela, une faute de frappe dans un YAML remonte en 500 avec trace."""
    with pytest.raises(GabaritInvalide):
        lire_gabarit(charge)


def test_un_lien_pointant_hors_du_dossier_est_refuse(monkeypatch, tmp_path):
    """Un lien depose dans le dossier serait suivi hors de celui-ci : on confine."""
    from app.services import gabarits as module

    dossier = tmp_path / "gabarits"
    dossier.mkdir()
    dehors = tmp_path / "hors_dossier.yaml"
    dehors.write_text("id: x\ntitre: T\nsections:\n  - titre: S\n", encoding="utf-8")
    try:
        (dossier / "evade.yaml").symlink_to(dehors)
    except (OSError, NotImplementedError):  # lien symbolique interdit sur ce poste
        pytest.skip("creation de lien symbolique non autorisee")

    monkeypatch.setattr(module, "_DOSSIER", dossier)
    module.vider_cache()
    assert "evade" in module.lister_gabarits()
    with pytest.raises(GabaritInconnu):
        module.charger_gabarit("evade")


def test_un_lien_restant_dans_le_dossier_est_accepte(monkeypatch, tmp_path):
    """Un montage ConfigMap est fait de liens internes : ne pas les rejeter en bloc."""
    from app.services import gabarits as module

    dossier = tmp_path / "gabarits"
    dossier.mkdir()
    (dossier / "reel.yaml").write_text("id: x\ntitre: T\nsections:\n  - titre: S\n", "utf-8")
    try:
        (dossier / "alias.yaml").symlink_to(dossier / "reel.yaml")
    except (OSError, NotImplementedError):
        pytest.skip("creation de lien symbolique non autorisee")

    monkeypatch.setattr(module, "_DOSSIER", dossier)
    module.vider_cache()
    assert module.charger_gabarit("alias").titre == "T"


def test_un_yaml_illisible_donne_une_erreur_metier(monkeypatch, tmp_path):
    from app.services import gabarits as module

    (tmp_path / "casse.yaml").write_text("titre: [non ferme\n", encoding="utf-8")
    monkeypatch.setattr(module, "_DOSSIER", tmp_path)
    with pytest.raises(GabaritInvalide):
        module.charger_gabarit("casse")


def test_le_nom_de_fichier_fait_foi_sur_la_cle_id(monkeypatch, tmp_path):
    """Un id usurpe rendrait le manifeste de provenance faux, donc non rejouable."""
    from app.services import gabarits as module

    (tmp_path / "vrai_nom.yaml").write_text(
        "id: usurpe\ntitre: T\nsections:\n  - titre: S\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "_DOSSIER", tmp_path)
    assert module.charger_gabarit("vrai_nom").identifiant == "vrai_nom"


def test_un_dossier_de_parcelle_sans_mention_est_refuse(monkeypatch, tmp_path):
    """D5 garanti par le CHARGEUR, pas seulement par un test du depot."""
    from app.services import gabarits as module

    (tmp_path / "dossier_parcelle.yaml").write_text(
        "id: dossier_parcelle\ntitre: T\nsections:\n  - titre: S\n", encoding="utf-8"
    )
    monkeypatch.setattr(module, "_DOSSIER", tmp_path)
    with pytest.raises(GabaritInvalide):
        module.charger_gabarit("dossier_parcelle")


def test_les_gabarits_livres_ne_portent_aucun_dosage_ni_nom_de_maladie():
    """Un gabarit est une consigne au modele : il ne doit rien lui souffler d interdit."""
    from app.services import guardrails

    for identifiant in lister_gabarits():
        gabarit = charger_gabarit(identifiant)
        for section in gabarit.sections:
            assert guardrails.contient_diagnostic(section.consigne) is None
