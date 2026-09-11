"""Tests du moteur de rédaction — planifier, rédiger, assembler."""

from __future__ import annotations

import pytest

from app.application.provenance import affirmations_sans_source
from app.application.redaction import (
    MAX_AFFIRMATIONS_SECTION,
    MAX_TOKENS_SECTION,
    PLAFOND_TOKENS_SECTION,
    ContexteGeneration,
    MoteurRedaction,
    SujetRefuse,
)
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
    inference = FausseInference(
        reponse="Appliquer 2 l/ha de produit phytosanitaire sur les cabosses."
    )
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

    async def _progression(faites: int, total: int, section) -> None:
        vues.append((faites, total, section.titre))

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
        "un traitement phytosanitaire a 2 l/ha sur les cabosses",
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
    inference = FausseInference(reponse="Appliquer 2 l/ha de produit phytosanitaire.")
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(_affirmation())}, _contexte())
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.manifeste.documents_rag == ()


@pytest.mark.parametrize(
    "sujet",
    [
        "la filiere cacao a Korhogo",
        "la plantation de Ferkessedougou",
        "la transformation locale du cacao en chocolat",
    ],
)
async def test_un_sujet_d_analyse_legitime_n_est_pas_refuse(sujet):
    """Les garde-fous ont ete ecrits pour la QUESTION D UN PRODUCTEUR.

    Un producteur a Korhogo est redirige parce qu on n y cultive pas de cacao ; une
    ETUDE sur la limite nord de la ceinture cacaoyere, ou sur la valeur ajoutee
    locale, est un travail d analyse legitime — c est meme ce qu un bailleur demande.
    Refuser rendrait le bulletin regional d une DR du Nord impossible a produire.
    """
    document = await _moteur().rediger(_gabarit(), sujet, "appareil-a")
    assert sujet in document.titre


@pytest.mark.parametrize(
    "sujet,corps",
    [
        (
            "la transformation locale du cacao en chocolat",
            "La transformation locale represente une part faible de la valeur ajoutee.",
        ),
        (
            "la filiere cacao dans la direction regionale de Korhogo",
            "La ceinture cacaoyere s arrete au sud de cette zone, ou la filiere est marginale.",
        ),
    ],
)
async def test_une_etude_sur_la_transformation_ou_le_nord_produit_du_contenu(sujet, corps):
    """Exigence V3 : ces livrables doivent EXISTER, pas sortir en lacunes.

    Le sujet doit passer le garde-fou d entree, ET le corps produit doit passer celui
    de sortie. Verifier seulement le premier laisserait une etude entierement vide.
    """
    inference = FausseInference(reponse=corps)
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(_affirmation())}, _contexte())
    document = await moteur.rediger(_gabarit(), sujet, "appareil-a")

    assert document.sections[0].lacune is False
    assert document.sections[0].corps == corps
    assert sujet in document.titre


@pytest.mark.parametrize(
    "corps",
    [
        "Le rendement moyen atteint 800 kg/ha sur les parcelles suivies.",
        "La densite recommandee avoisine 1 100 plants/ha selon les sources.",
        "Les apports organiques observes se situent autour de 5 kg/ha.",
    ],
)
async def test_une_etude_peut_citer_un_rendement_chiffre(corps):
    """Un rendement en kg/ha est le chiffre le plus banal d une analyse agronomique.

    Le garde-fou de sortie a ete ecrit pour le CONSEIL, ou un faux positif ne coute
    qu une redirection. Applique tel quel a un livrable, il transformait en lacune
    toute section citant un rendement — c est-a-dire l essentiel d une etude.
    """
    inference = FausseInference(reponse=corps)
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(_affirmation())}, _contexte())
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections[0].lacune is False
    assert document.sections[0].corps == corps


@pytest.mark.parametrize(
    "corps",
    [
        "Appliquer 2 l/ha de produit phytosanitaire sur les cabosses.",
        "Diluer 50 ml dans 10 litres avant pulverisation du produit phytosanitaire.",
    ],
)
async def test_une_prescription_chiffree_reste_refusee(corps):
    """Ce qui est interdit, c est la PRESCRIPTION — pas le chiffre."""
    inference = FausseInference(reponse=corps)
    moteur = MoteurRedaction(inference, {"rag": FauxCollecteur(_affirmation())}, _contexte())
    document = await moteur.rediger(_gabarit(), "le cacao", "appareil-a")
    assert document.sections[0].lacune is True
    assert corps not in document.sections[0].corps


# --- Une section sans source déclarée porte sur le DOCUMENT, pas sur le corpus ---
#
# Écart vécu en production le 11/09/2026. Le gabarit « étude de filière » déclare une
# dernière section « Limites de la présente étude » avec `sources: []`. La règle D4 —
# une section sans source ne mobilise pas le modèle — la faisait retomber sur le
# constat de lacune générique, systématiquement, sur CHAQUE étude produite :
#
#   « Aucune source mobilisable n'a été trouvée pour cette section. Elle est laissée
#     en l'état plutôt que renseignée par estimation… »
#
# Le lecteur y voyait un échec de collecte, alors que cette section n'a par nature
# aucune source externe à mobiliser : elle parle de l'étude elle-même. Elle se rédige
# donc à partir des sections déjà écrites, exactement comme le résumé et la conclusion,
# et énonce les lacunes RÉELLEMENT rencontrées.


def _gabarit_avec_limites() -> Gabarit:
    return _gabarit(
        *(
            SectionGabarit(titre="Contexte", sources=("rag",), consigne="Situer."),
            SectionGabarit(titre="Limites de la présente étude", sources=(), consigne="Énoncer."),
        )
    )


@pytest.mark.asyncio
async def test_la_section_limites_est_redigee_et_non_declaree_en_lacune() -> None:
    """Elle porte sur l'étude, pas sur le corpus : elle n'a aucune source à manquer."""
    inference = FausseInference("Les sources mobilisées ne permettent pas d'établir X.")
    moteur = _moteur(inference, rag=FauxCollecteur(_affirmation()))

    document = await moteur.rediger(_gabarit_avec_limites(), "campagne 2025-2026", "demandeur")

    limites = document.sections[-1]
    assert limites.titre == "Limites de la présente étude"
    assert not limites.lacune, "la section méta était comptée comme une lacune de collecte"
    assert "Aucune source mobilisable" not in limites.corps


@pytest.mark.asyncio
async def test_la_section_limites_part_des_sections_ECRITES() -> None:
    """Elle ne redemande rien au corpus : son contexte est le document lui-même."""
    inference = FausseInference("Limites.")
    moteur = _moteur(inference, rag=FauxCollecteur(_affirmation()))

    await moteur.rediger(_gabarit_avec_limites(), "campagne 2025-2026", "demandeur")

    dernier = inference.appels[-1]
    assert "Contexte" in str(
        dernier.get("contexte", "")
    ), "la section méta doit recevoir les sections déjà écrites comme contexte"


@pytest.mark.asyncio
async def test_sans_aucune_section_ecrite_la_lacune_reste_honnete() -> None:
    """Si tout le document est vide, il n'y a rien dont on puisse énoncer les limites."""
    inference = FausseInference("Limites.")
    moteur = _moteur(inference, rag=FauxCollecteur())

    document = await moteur.rediger(_gabarit_avec_limites(), "campagne 2025-2026", "demandeur")

    limites = document.sections[-1]
    assert limites.lacune


# --- L'ampleur demandée est honorée, dans la limite de ce que les sources permettent -
#
# Écart de production du 11/09/2026 : « minimum 25 pages » produisait quatre pages, et
# rien ne le signalait. Le budget par section était fixe (420 tokens), quelle que soit
# la demande.
#
# Ce qu'on fait : on répartit l'ampleur demandée sur les sections et on élargit d'autant
# la base documentaire mobilisée. Ce qu'on ne fait PAS : promettre la longueur. Une
# étude est bornée par les sources, jamais par un souhait — et quand l'écart subsiste,
# le document le DIT au lieu de le combler par du remplissage.


@pytest.mark.asyncio
async def test_une_ampleur_demandee_elargit_le_budget_des_sections() -> None:
    inference = FausseInference()
    moteur = _moteur(inference, rag=FauxCollecteur(_affirmation()))

    await moteur.rediger(_gabarit(), "le cacao", "appareil-a", pages=25)

    ecriture = inference.appels[0]
    assert ecriture["max_tokens"] > MAX_TOKENS_SECTION


@pytest.mark.asyncio
async def test_sans_ampleur_le_budget_ne_bouge_pas() -> None:
    """Le comportement par défaut reste celui d'avant : rien n'est changé en silence."""
    inference = FausseInference()
    moteur = _moteur(inference, rag=FauxCollecteur(_affirmation()))

    await moteur.rediger(_gabarit(), "le cacao", "appareil-a")

    assert inference.appels[0]["max_tokens"] == MAX_TOKENS_SECTION


@pytest.mark.asyncio
async def test_le_budget_par_section_reste_borne() -> None:
    """Une section de 4 000 tokens sortirait du contexte utile et se dégraderait. On
    plafonne : l'ampleur s'obtient par le nombre de sections, pas par des pavés."""
    inference = FausseInference()
    moteur = _moteur(inference, rag=FauxCollecteur(_affirmation()))

    await moteur.rediger(_gabarit(), "le cacao", "appareil-a", pages=60)

    assert inference.appels[0]["max_tokens"] <= PLAFOND_TOKENS_SECTION


@pytest.mark.asyncio
async def test_l_ampleur_elargit_aussi_la_base_documentaire() -> None:
    """Allonger le budget sans élargir les sources ferait délayer le modèle : plus de
    mots pour les mêmes faits, c'est exactement le remplissage qu'on refuse."""
    inference = FausseInference()
    collecteur = FauxCollecteur(*[_affirmation() for _ in range(40)])
    moteur = _moteur(inference, rag=collecteur)

    await moteur.rediger(_gabarit(), "le cacao", "appareil-a", pages=25)

    lignes = str(inference.appels[0].get("contexte", "")).splitlines()
    assert len(lignes) > MAX_AFFIRMATIONS_SECTION
