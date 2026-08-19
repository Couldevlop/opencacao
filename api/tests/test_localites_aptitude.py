"""Une localité inconnue ne doit JAMAIS être présumée cacaoyère.

Écart constaté par Waopron le 19/08/2026, en production :

    « je veux faire une plantation de cacao à Ouangolo, que me conseilles-tu ? »
    → « À Ouangolo, comme dans toute la zone forestière du Sud (Gagnoa, Daloa,
       Soubré…), le cacaoyer peut bien pousser grâce au climat adapté. »

Ouangolodougou est à l'extrême nord du pays. Le modèle n'a pas seulement omis de
corriger : il a **affirmé** l'inverse de la réalité, avec assurance, sur une décision
d'investissement. Un producteur qui plante là perd sa mise.

La cause est structurelle. Le code portait une **liste de refus** de quinze villes du
Nord ; toute localité absente de cette liste était donc implicitement traitée comme
cacaoyère. « Ouangolo », la forme courte que les gens emploient réellement, n'y
figurait pas.

On inverse le défaut, en trois états et non deux — parce que deux ne suffisent pas :
les Directions Régionales Centre et Centre-Est sont **mixtes** (Katiola et Dabakala
sont refusées de longue date, quand Bongouanou et Daoukro sont bien cacaoyères).

* ``CACAO``      la localité est dans une DR cacaoyère : on répond.
* ``NORD``       savane du Nord : on corrige en nommant la localité.
* ``INDETERMINE``  on ne sait pas : on ne l'affirme surtout pas, on le dit et on
                   renvoie vers l'ANADER, qui connaît le terrain.

**Limite assumée.** Un village absent du découpage ANADER (« Zambakro », « Kotobi »)
n'est pas détecté comme lieu : le reconnaître exigerait de la reconnaissance d'entités
nommées, que ce projet n'embarque pas. Ces cas relèvent de la consigne donnée au
modèle, pas de cette fonction. Ce qui est couvert ici, c'est l'ensemble des localités
que le dépôt connaît — soixante zones et leurs formes courtes usuelles.
"""

from __future__ import annotations

import pytest

from app.services.localites import Aptitude, aptitude_cacao


@pytest.mark.parametrize(
    "texte",
    [
        "je veux planter du cacao à Soubré",
        "ma plantation est à Gagnoa",
        "je suis installé vers Daloa",
        "on cultive à San Pedro",
        "ma parcelle se trouve à Abengourou",
        "je suis à Duékoué",
    ],
)
def test_les_zones_cacaoyeres_sont_reconnues(texte: str) -> None:
    """La boucle du cacao doit passer sans friction : c'est le cas courant."""
    verdict = aptitude_cacao(texte)

    assert verdict is not None
    assert verdict[0] is Aptitude.CACAO


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("je veux planter du cacao à Ouangolodougou", "Ouangolodougou"),
        # La forme COURTE, celle qui a produit l'écart du 19/08.
        ("je veux faire une plantation de cacao à Ouangolo", "Ouangolodougou"),
        ("je suis à Korhogo", "Korhogo"),
        ("ma plantation est à Katiola", "Katiola"),
        ("je suis à Ferké", "Ferké"),
    ],
)
def test_les_localites_du_nord_sont_corrigees_et_nommees(texte: str, attendu: str) -> None:
    """Nommer la localité est ce qui rend la correction crédible : « cette zone » ne
    convainc personne, « Ouangolodougou » si."""
    verdict = aptitude_cacao(texte)

    assert verdict is not None
    assert verdict[0] is Aptitude.NORD
    assert verdict[1] == attendu


@pytest.mark.parametrize(
    "texte",
    [
        # Zones de TRANSITION : ni franchement cacaoyères, ni savane du Nord. Les DR
        # Centre et Centre-Est sont mixtes, c'est précisément pourquoi une liste
        # binaire ne peut pas trancher.
        "je veux planter du cacao à Bouaké",
        "ma plantation est à Dimbokro",
        "je suis à Séguéla",
    ],
)
def test_une_localite_non_repertoriee_reste_indeterminee(texte: str) -> None:
    """Le cœur du correctif. Sans cet état, tout ce qui n'est pas explicitement refusé
    était présumé cacaoyère — et le modèle l'affirmait."""
    verdict = aptitude_cacao(texte)

    assert verdict is not None
    assert verdict[0] is Aptitude.INDETERMINE


def test_sans_localite_citee_aucun_verdict() -> None:
    """Contre-épreuve : une question sans lieu ne doit rien déclencher, sinon chaque
    conseil général se verrait affublé d'une mise en garde géographique."""
    assert aptitude_cacao("comment tailler un cacaoyer ?") is None


def test_la_boucle_du_cacao_vient_du_decoupage_anader() -> None:
    """La liste n'est pas écrite à la main : elle est dérivée de
    `contacts_zones.yaml`, seule source de vérité du dépôt sur les zones. Une liste
    saisie à part dériverait de l'annuaire au premier redécoupage."""
    from app.services.localites import localites_cacao

    zones = localites_cacao()

    assert "soubre" in zones
    assert "gagnoa" in zones
    assert "korhogo" not in zones, "une zone du Nord ne peut pas être cacaoyère"
    assert len(zones) >= 30, "les cinq DR cacaoyères totalisent 34 zones"
