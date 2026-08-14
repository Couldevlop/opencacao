"""Tests de la collecte documentaire, section par section.

Cinq sections d'une étude déclarent ``rag``. Interrogées avec le seul sujet, elles
recevaient TOUTES les mêmes passages — puis on demandait au modèle d'en tirer cinq
textes différents. La répétition entre sections n'était pas un défaut de génération,
c'était un défaut de collecte.

Symétriquement, un outil (prix, météo) ne doit PAS être réinterrogé par section : il
dépend de la localité, pas du titre de la section, et le rappeler cinq fois avec les
mêmes arguments coûte cinq appels pour un seul résultat.
"""

from __future__ import annotations

import pytest

from app.application.redaction import MoteurRedaction
from app.models.rapport import Affirmation, NiveauConfiance
from app.services.gabarits import Gabarit, SectionGabarit
from tests.test_redaction import FausseInference, _contexte


class CollecteurEspion:
    """Collecteur qui note ce qu'on lui demande.

    Args:
        varie_par_section: Déclare si ce collecteur produit un résultat différent
            d'une section à l'autre. Le RAG oui, un outil non.
    """

    def __init__(self, varie_par_section: bool = False) -> None:
        self.varie_par_section = varie_par_section
        self.appels: list[tuple[str, str]] = []

    async def collecter(self, sujet: str, requete: str = "") -> tuple[Affirmation, ...]:
        self.appels.append((sujet, requete))
        return (
            Affirmation(
                texte="Un fait sourcé.",
                source="CNRA",
                date="2025-10-01",
                methode="rag",
                confiance=NiveauConfiance.MOYENNE,
                empreinte="e3b0c44298fc",
            ),
        )


def _gabarit_trois_sections(source: str) -> Gabarit:
    return Gabarit(
        identifiant="etude_filiere",
        titre="Étude de filière — {sujet}",
        sous_titre="",
        public="bailleurs",
        mention="",
        sections=(
            SectionGabarit("Contexte de la filière", (source,), "Situer le sujet."),
            SectionGabarit("Production et rendements", (source,), "Rapporter les volumes."),
            SectionGabarit("Débouchés", (source,), "Décrire la commercialisation."),
        ),
    )


def _moteur(collecteurs: dict[str, object]) -> MoteurRedaction:
    return MoteurRedaction(FausseInference(), collecteurs, _contexte())


@pytest.mark.asyncio
async def test_le_rag_est_interroge_par_section_et_non_par_document():
    espion = CollecteurEspion(varie_par_section=True)
    await _moteur({"rag": espion}).rediger(
        _gabarit_trois_sections("rag"), "la campagne 2025-2026", "appareil-a"
    )

    assert len(espion.appels) == 3
    requetes = [requete for _, requete in espion.appels]
    # Trois requêtes DISTINCTES : sans cela les trois sections reçoivent les mêmes
    # passages et le modèle doit inventer la différence.
    assert len(set(requetes)) == 3


@pytest.mark.asyncio
async def test_la_requete_documentaire_porte_le_titre_et_la_consigne():
    espion = CollecteurEspion(varie_par_section=True)
    await _moteur({"rag": espion}).rediger(
        _gabarit_trois_sections("rag"), "la campagne 2025-2026", "appareil-a"
    )

    _, premiere = espion.appels[0]
    assert "Contexte de la filière" in premiere
    assert "Situer le sujet" in premiere
    # Le sujet reste dans la requête : « production » seul chercherait dans tout le
    # corpus, campagne comprise.
    assert "la campagne 2025-2026" in premiere


@pytest.mark.asyncio
async def test_un_outil_n_est_appele_qu_une_fois_pour_tout_le_document():
    # Le prix officiel ne change pas d'une section à l'autre. Le redemander à chaque
    # section, c'est trois appels réseau pour un seul résultat.
    espion = CollecteurEspion(varie_par_section=False)
    await _moteur({"prix": espion}).rediger(_gabarit_trois_sections("prix"), "Daloa", "appareil-a")
    assert len(espion.appels) == 1


@pytest.mark.asyncio
async def test_un_outil_recoit_le_sujet_nu_jamais_le_titre_de_section():
    # L'agent Météo détecte la localité DANS ce qu'on lui passe. Lui envoyer
    # « Conditions météorologiques — Daloa » au lieu de « Daloa » brouillerait la
    # détection, qui est déjà un module à part (localites.py).
    espion = CollecteurEspion(varie_par_section=False)
    await _moteur({"meteo": espion}).rediger(
        _gabarit_trois_sections("meteo"), "Daloa", "appareil-a"
    )
    sujet, _ = espion.appels[0]
    assert sujet == "Daloa"


@pytest.mark.asyncio
async def test_les_affirmations_d_un_outil_partage_arrivent_a_chaque_section():
    # Mutualiser l'appel ne doit pas priver les sections suivantes de son résultat.
    espion = CollecteurEspion(varie_par_section=False)
    document = await _moteur({"prix": espion}).rediger(
        _gabarit_trois_sections("prix"), "Daloa", "appareil-a"
    )
    assert all(section.affirmations for section in document.sections)


def test_les_deux_natures_de_source_sont_declarees():
    """Ces deux constantes pilotent tout le mecanisme.

    Sans cette assertion, les inverser laissait la suite entierement verte : le RAG
    aurait ete mutualise (retour du defaut corrige) et les outils rappeles par section.
    """
    from app.services.collecteurs import CollecteurOutil, CollecteurRag

    assert CollecteurRag.varie_par_section is True
    assert CollecteurOutil.varie_par_section is False


@pytest.mark.asyncio
async def test_le_rag_transmet_la_requete_et_ancre_le_lexical_sur_le_sujet():
    """Le vrai collecteur, pas un espion : c est lui qui parle au RAG.

    Le canal dense se raffine par section, le canal lexical reste ancre sur le sujet.
    Allonger la requete lexicale ferait tomber le recouvrement sous son seuil et
    eteindrait la voie de secours qui rattrape les termes rares.
    """
    from app.services.collecteurs import CollecteurRag

    class RecuperateurEspion:
        def __init__(self) -> None:
            self.appels: list[tuple[str, str]] = []

        async def passages_pour(self, question: str, ancre: str = ""):
            self.appels.append((question, ancre))
            return []

    espion = RecuperateurEspion()
    await CollecteurRag(espion).collecter("Daloa", "Prix du cacao Rapporter les cours Daloa")

    question, ancre = espion.appels[0]
    assert question == "Prix du cacao Rapporter les cours Daloa"
    assert ancre == "Daloa"


@pytest.mark.asyncio
async def test_la_requete_documentaire_est_bornee():
    # Titre et consigne viennent d un gabarit, montable par ConfigMap : rien ne borne
    # leur longueur au chargement.
    from app.application.redaction import REQUETE_MAX, requete_de_section

    enorme = SectionGabarit("T" * 10_000, ("rag",), "C" * 10_000)
    assert len(requete_de_section(enorme, "Daloa")) == REQUETE_MAX


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fabrique",
    [
        pytest.param(
            lambda enregistrer: type(
                "MotCleSeul",
                (),
                {
                    "varie_par_section": True,
                    "collecter": lambda self, sujet, *, requete="": enregistrer(sujet, requete),
                },
            )(),
            id="keyword-only",
        ),
        pytest.param(
            lambda enregistrer: type(
                "Variadique",
                (),
                {
                    "varie_par_section": True,
                    "collecter": lambda self, **kwargs: enregistrer(
                        kwargs.get("sujet", ""), kwargs.get("requete", "")
                    ),
                },
            )(),
            id="kwargs",
        ),
    ],
)
async def test_les_signatures_du_style_maison_sont_appelees_correctement(fabrique):
    """`OutilPort` s ecrit deja `invoquer(self, **kwargs)` dans ce depot.

    Un collecteur calque sur ce style etait appele POSITIONNELLEMENT : TypeError, avalee
    par le `except Exception` de `_collecter`, donc section en lacune SILENCIEUSE. C est
    exactement le mode d echec que la voie de compatibilite pretend eviter.
    """
    recu: list[tuple[str, str]] = []

    async def enregistrer(sujet: str, requete: str):
        recu.append((sujet, requete))
        return ()

    document = await _moteur({"rag": fabrique(enregistrer)}).rediger(
        _gabarit_trois_sections("rag"), "Daloa", "appareil-a"
    )

    assert len(recu) == 3, "le collecteur n a pas ete appele correctement"
    assert all(sujet == "Daloa" and requete for sujet, requete in recu)
    # Et surtout : pas de lacune SILENCIEUSE due a une TypeError avalee.
    assert len(document.sections) == 3


@pytest.mark.asyncio
async def test_un_collecteur_sans_le_nouveau_parametre_reste_accepte():
    # Compatibilité : un collecteur écrit avant ce changement ne doit pas exploser.
    class Ancien:
        def __init__(self) -> None:
            self.appels = 0

        async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
            self.appels += 1
            return ()

    ancien = Ancien()
    document = await _moteur({"rag": ancien}).rediger(
        _gabarit_trois_sections("rag"), "Daloa", "appareil-a"
    )
    assert ancien.appels == 3
    # Aucune source mobilisable : les sections le disent au lieu de l'inventer (D4).
    assert all(section.lacune for section in document.sections)
