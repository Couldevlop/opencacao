"""Tests du moteur de rédaction — planifier, rédiger, assembler."""

from __future__ import annotations

import pytest

from app.application.provenance import affirmations_sans_source
from app.application.redaction import ContexteGeneration, MoteurRedaction, SujetRefuse
from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation
from app.services.gabarits import Gabarit, SectionGabarit
from app.services.prompts_redaction import SYSTEM_PROMPT_REDACTION


class FausseInference:
    """Port d'inférence contrôlé par le test (aucun réseau)."""

    def __init__(self, reponse: str = "Un paragraphe analytique documenté.") -> None:
        self.reponse = reponse
        self.appels: list[dict] = []

    async def generer(self, question: str, **options: object) -> str:
        self.appels.append({"question": question, **options})
        return self.reponse

    def generer_stream(self, *_: object, **__: object):
        raise NotImplementedError

    async def ready(self) -> bool:
        return True


class FauxCollecteur:
    """Collecteur de source contrôlé par le test."""

    def __init__(self, *affirmations: Affirmation) -> None:
        self.affirmations = affirmations
        self.sujets: list[str] = []

    async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
        self.sujets.append(sujet)
        return self.affirmations


def _affirmation(source: str = "CNRA") -> Affirmation:
    return Affirmation(
        texte="La production avoisine 2,2 millions de tonnes.",
        source=source,
        date="2025-10-01",
        methode="rag",
        confiance=NiveauConfiance.MOYENNE,
        empreinte="e3b0c44298fc",
    )


def _gabarit(*sections: SectionGabarit, mention: str = "") -> Gabarit:
    return Gabarit(
        identifiant="etude_filiere",
        titre="Étude de filière — {sujet}",
        sous_titre="Analyse documentée",
        public="bailleurs",
        mention=mention,
        sections=sections or (SectionGabarit("Contexte", ("rag",), "Situer le sujet."),),
    )


def _contexte() -> ContexteGeneration:
    return ContexteGeneration(
        modele="opencacao-8b",
        version_modele="1.1.0",
        version_app="0.6.75",
        profil_materiel="cpu",
    )


def _moteur(inference=None, **collecteurs) -> MoteurRedaction:
    return MoteurRedaction(
        inference or FausseInference(),
        collecteurs or {"rag": FauxCollecteur(_affirmation())},
        _contexte(),
    )


# ------------------------------------------------------------------ assemblage


async def test_le_document_porte_une_section_par_section_du_gabarit():
    gabarit = _gabarit(
        SectionGabarit("Contexte", ("rag",), ""),
        SectionGabarit("Marché", ("rag",), ""),
    )
    document = await _moteur().rediger(gabarit, "le cacao", "appareil-a")
    assert [section.titre for section in document.sections] == ["Contexte", "Marché"]


async def test_le_sujet_est_substitue_dans_le_titre():
    document = await _moteur().rediger(_gabarit(), "la campagne 2025-2026", "appareil-a")
    assert document.titre == "Étude de filière — la campagne 2025-2026"


async def test_les_affirmations_collectees_remontent_dans_la_section():
    document = await _moteur().rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections[0].affirmations == (_affirmation(),)


async def test_le_sujet_est_transmis_aux_collecteurs():
    collecteur = FauxCollecteur(_affirmation())
    await _moteur(rag=collecteur).rediger(_gabarit(), "le cacao", "appareil-a")
    assert collecteur.sujets == ["le cacao"]


async def test_la_mention_du_gabarit_est_reportee_sur_le_document():
    """D5 : la mention « dossier preparatoire » ne peut pas se perdre en route."""
    gabarit = _gabarit(mention="Document préparatoire.")
    document = await _moteur().rediger(gabarit, "la parcelle p1", "appareil-a")
    assert document.mention == "Document préparatoire."


# ------------------------------------------------------- D4, constat de lacune


async def test_une_section_sans_source_disponible_rend_un_constat_de_lacune():
    """D4 : on dit ce qui manque, on n estime pas."""
    document = await _moteur(rag=FauxCollecteur()).rediger(_gabarit(), "le cacao", "appareil-a")
    section = document.sections[0]
    assert section.lacune is True
    assert section.affirmations == ()
    assert "source" in section.corps.lower()


async def test_une_section_en_lacune_n_appelle_pas_le_modele():
    """Generer sans source, c est exactement la fabrication qu on interdit (v0.6.48)."""
    inference = FausseInference()
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur()}, _contexte())
    await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert inference.appels == []


async def test_une_section_sans_source_declaree_rend_aussi_une_lacune():
    """« Limites de l etude » ne declare aucune source : rien a affirmer."""
    gabarit = _gabarit(SectionGabarit("Limites", (), "Enoncer les limites."))
    document = await _moteur().rediger(gabarit, "le cacao", "appareil-a")
    assert document.sections[0].lacune is True


async def test_un_collecteur_qui_echoue_degrade_en_lacune():
    """Un outil indisponible ne doit pas faire tomber tout le document."""

    class CollecteurCasse:
        async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
            raise RuntimeError("source injoignable")

    document = await _moteur(rag=CollecteurCasse()).rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections[0].lacune is True


async def test_une_source_declaree_sans_collecteur_ne_leve_pas():
    """Gabarit valide mais collecteur absent du cablage : lacune, pas exception."""
    document = await MoteurRedaction(FausseInference(), {}, _contexte()).rediger(
        _gabarit(), "le cacao", "appareil-a"
    )
    assert document.sections[0].lacune is True


async def test_une_lacune_n_empeche_pas_les_autres_sections():
    """Un document reste utile meme si une seule de ses sources manque."""
    gabarit = _gabarit(
        SectionGabarit("Contexte", ("rag",), ""),
        SectionGabarit("Marché", ("prix",), ""),
    )
    moteur = _moteur(rag=FauxCollecteur(_affirmation()), prix=FauxCollecteur())
    document = await moteur.rediger(gabarit, "le cacao", "appareil-a")
    assert [section.lacune for section in document.sections] == [False, True]


# ------------------------------------------------------------- garde-fous


async def test_le_registre_analytique_est_impose_au_modele():
    inference = FausseInference()
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(_affirmation())}, _contexte())
    await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert inference.appels[0]["system_prompt"] == SYSTEM_PROMPT_REDACTION


async def test_une_section_qui_prescrit_un_dosage_est_refusee():
    """Un document d etude ne prescrit pas plus qu un conseil au producteur."""
    inference = FausseInference(reponse="Appliquer 2 l/ha de bouillie sur les cabosses.")
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(_affirmation())}, _contexte())
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    section = document.sections[0]
    assert section.lacune is True
    assert "2 l/ha" not in section.corps


async def test_une_section_qui_nomme_une_maladie_reste_acceptee():
    """Arbitrage Waopron du 29/07/2026 : citer une maladie sourcee n est pas diagnostiquer.

    contient_diagnostic est le verrou D3 du constat visuel — nommer une atteinte
    depuis une PHOTO. Une etude qui rapporte, sources a l appui, qu une maladie
    affecte la filiere enonce un fait documente.
    """
    inference = FausseInference(reponse="La pourriture brune affecte une part de la production.")
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(_affirmation())}, _contexte())
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections[0].lacune is False
    assert "pourriture brune" in document.sections[0].corps


async def test_une_affirmation_non_sourcee_par_un_collecteur_est_ecartee():
    """Defense en profondeur : le moteur ne laisse pas passer une source vide."""
    moteur = _moteur(rag=FauxCollecteur(_affirmation(source=""), _affirmation()))
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections[0].affirmations == (_affirmation(),)


async def test_aucune_affirmation_ne_sort_sans_source():
    """Critere d acceptation de la spec, verifie sur un document reellement produit."""
    document = await _moteur().rediger(_gabarit(), "le cacao", "appareil-a")
    assert affirmations_sans_source(document) == ()


# ------------------------------------------------------------- manifeste


async def test_le_manifeste_recense_les_sources_mobilisees():
    document = await _moteur().rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.manifeste.documents_rag == (("CNRA", "e3b0c44298fc"),)


async def test_le_manifeste_ne_repete_pas_deux_fois_la_meme_source():
    """Deux sections citant le CNRA ne doivent pas le lister deux fois."""
    gabarit = _gabarit(
        SectionGabarit("Contexte", ("rag",), ""),
        SectionGabarit("Marché", ("rag",), ""),
    )
    document = await _moteur().rediger(gabarit, "le cacao", "appareil-a")
    assert document.manifeste.documents_rag == (("CNRA", "e3b0c44298fc"),)


async def test_le_manifeste_porte_le_profil_et_une_empreinte_de_demandeur():
    document = await _moteur().rediger(_gabarit(), "le cacao", "appareil-b")
    assert document.manifeste.profil_materiel == "cpu"
    assert document.manifeste.empreinte_demandeur
    assert "appareil-b" not in document.manifeste.empreinte_demandeur


# ------------------------------------------------------------- progression


async def test_la_progression_est_notifiee_section_par_section():
    """C est ce qui alimente le flux SSE : un evenement par section."""
    vues: list[tuple[int, int, str]] = []

    async def _progression(faites: int, total: int, titre: str) -> None:
        vues.append((faites, total, titre))

    gabarit = _gabarit(
        SectionGabarit("Contexte", ("rag",), ""),
        SectionGabarit("Marché", ("rag",), ""),
    )
    await _moteur().rediger(gabarit, "le cacao", "appareil-a", progression=_progression)
    assert vues == [(1, 2, "Contexte"), (2, 2, "Marché")]


async def test_sans_rappel_de_progression_la_redaction_aboutit():
    document = await _moteur().rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections


@pytest.mark.parametrize("sujet", ["{sujet}", "{0}", "100 % {", "}"])
async def test_un_sujet_hostile_ne_casse_pas_la_substitution(sujet):
    """Le sujet vient d une requete HTTP : il ne doit pas etre interprete comme format."""
    document = await _moteur().rediger(_gabarit(), sujet, "appareil-a")
    assert sujet in document.titre


# ------------------------------------------------- garde-fou sur le sujet


@pytest.mark.parametrize(
    "sujet",
    [
        "la lutte anti-mirides a 2 l/ha de bouillie bordelaise",
        "la culture de l anacarde en Cote d Ivoire",
        "la production de manioc",
    ],
)
async def test_un_sujet_qui_franchit_un_garde_fou_est_refuse(sujet):
    """Le sujet atterrit dans le TITRE sans passer par le modele : il echapperait
    a tout garde-fou de sortie. Un livrable OpenCacao ne porte ni dosage ni
    filiere etrangere dans son titre — regles non negociables (CLAUDE.md)."""
    with pytest.raises(SujetRefuse):
        await _moteur().rediger(_gabarit(), sujet, "appareil-a")


async def test_un_sujet_refuse_n_appelle_pas_le_modele():
    inference = FausseInference()
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(_affirmation())}, _contexte())
    with pytest.raises(SujetRefuse):
        await moteur.rediger(_gabarit(), "la culture de l anacarde", "appareil-a")
    assert inference.appels == []


async def test_un_sujet_cacao_legitime_passe():
    document = await _moteur().rediger(_gabarit(), "la campagne cacao 2025-2026", "appareil-a")
    assert document.sections


# ------------------------------------------------- registre et bornes


async def test_le_tour_utilisateur_n_oriente_pas_vers_l_anader():
    """L en-tete de contexte par defaut dit « oriente vers l ANADER » — ce que le
    prompt systeme interdit. Le tour utilisateur etant plus proche de la generation,
    c est lui que le modele suivrait."""
    inference = FausseInference()
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(_affirmation())}, _contexte())
    await moteur.rediger(_gabarit(), "le cacao", "appareil-a")

    from app.services.prompts import build_messages

    messages = build_messages(
        inference.appels[0]["question"],
        inference.appels[0]["contexte"],
        system_prompt=inference.appels[0]["system_prompt"],
        entete_contexte=inference.appels[0]["entete_contexte"],
        libelle_question=inference.appels[0]["libelle_question"],
    )
    assert "ANADER" not in messages[-1]["content"]
    assert "Question :" not in messages[-1]["content"]


async def test_le_contexte_injecte_est_borne_en_nombre():
    """Un collecteur verbeux ferait preremplir des milliers de tokens par section."""
    from app.application.redaction import MAX_AFFIRMATIONS_SECTION

    nombreuses = tuple(_affirmation(f"Source {index}") for index in range(40))
    inference = FausseInference()
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(*nombreuses)}, _contexte())
    await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert inference.appels[0]["contexte"].count("\n") + 1 == MAX_AFFIRMATIONS_SECTION


async def test_une_affirmation_trop_longue_est_tronquee():
    from app.application.redaction import MAX_CARACTERES_AFFIRMATION

    longue = Affirmation(
        texte="x" * 5000,
        source="CNRA",
        date="",
        methode="rag",
        confiance=NiveauConfiance.MOYENNE,
    )
    inference = FausseInference()
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(longue)}, _contexte())
    await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert len(inference.appels[0]["contexte"]) < MAX_CARACTERES_AFFIRMATION + 100


async def test_une_source_vide_de_sens_est_ecartee_par_le_moteur():
    """Le moteur doit appliquer la MEME regle que affirmations_sans_source, sinon
    une source « n/a » entre au manifeste et fait echouer le critere d acceptation."""
    moteur = _moteur(rag=FauxCollecteur(_affirmation(source="n/a"), _affirmation()))
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections[0].affirmations == (_affirmation(),)


async def test_les_affirmations_d_une_section_refusee_ne_vont_pas_au_manifeste():
    """Une section ecartee par le garde-fou ne doit pas laisser ses sources derriere."""
    inference = FausseInference(reponse="Appliquer 2 l/ha de bouillie.")
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(_affirmation())}, _contexte())
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.manifeste.documents_rag == ()
