"""Tests du service des rapports — exécution, flux d'événements, export."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application.redaction import ContexteGeneration, MoteurRedaction
from app.core.rapports_store import EtatRapport, RapportStore
from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation
from app.services.gabarits import GabaritInconnu
from app.services.rapports import RapportIntrouvable, ServiceRapports

DEVICE = "appareil-a"


class FausseInference:
    """Port d'inférence contrôlé par le test (aucun réseau)."""

    async def generer(self, question: str, **_: object) -> str:
        return "Un paragraphe analytique documenté."

    def generer_stream(self, *_: object, **__: object):
        raise NotImplementedError

    async def ready(self) -> bool:
        return True


class InferenceCassee:
    """Inférence indisponible."""

    async def generer(self, question: str, **_: object) -> str:
        raise RuntimeError("inférence indisponible")

    def generer_stream(self, *_: object, **__: object):
        raise NotImplementedError

    async def ready(self) -> bool:
        return False


class FauxCollecteur:
    """Collecteur rendant toujours une affirmation sourcée."""

    async def collecter(self, sujet: str) -> tuple[Affirmation, ...]:
        return (
            Affirmation(
                texte="La production avoisine 2,2 millions de tonnes.",
                source="CNRA",
                date="2025-10-01",
                methode="rag",
                confiance=NiveauConfiance.MOYENNE,
                empreinte="e3b0c44298fc",
            ),
        )


@pytest.fixture
async def store(tmp_path: Path) -> RapportStore:
    depot = RapportStore(tmp_path / "rapports.db")
    await depot.initialiser()
    return depot


def _service(store: RapportStore, inference=None) -> ServiceRapports:
    contexte = ContexteGeneration("opencacao-8b", "1.1.0", "0.6.75", "cpu")
    collecteurs = {
        nom: FauxCollecteur()
        for nom in ("rag", "prix", "meteo", "satellite", "parcelle", "constats")
    }
    return ServiceRapports(
        store,
        lambda: MoteurRedaction(inference or FausseInference(), collecteurs, contexte),
    )


# ------------------------------------------------------------------ creation


async def test_creer_refuse_un_gabarit_inconnu(store: RapportStore):
    """On valide tot : inutile de persister un job impossible."""
    with pytest.raises(GabaritInconnu):
        await _service(store).creer("gabarit-fantome", "le cacao", DEVICE)


async def test_creer_rend_un_job_en_attente(store: RapportStore):
    rapport = await _service(store).creer("bulletin_regional", "Daloa", DEVICE)
    assert rapport.etat is EtatRapport.EN_ATTENTE


async def test_lister_et_obtenir_cloisonnent_par_appareil(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    assert await service.obtenir(rapport.identifiant, "appareil-b") is None
    assert await service.lister("appareil-b") == []
    assert len(await service.lister(DEVICE)) == 1


# ------------------------------------------------------------------ execution


async def test_le_premier_evenement_arrive_avant_toute_generation(store: RapportStore):
    """Critere d acceptation : premier octet en moins d une seconde."""
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    flux = service.executer(rapport.identifiant, DEVICE)
    premier = await anext(flux)
    assert premier["type"] == "progress"
    await flux.aclose()


async def test_un_evenement_par_section_puis_un_evenement_final(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    evenements = [evenement async for evenement in service.executer(rapport.identifiant, DEVICE)]
    types = [evenement["type"] for evenement in evenements]
    assert types[0] == "progress"
    assert types.count("section") == 3
    assert types[-1] == "final"


async def test_le_rapport_est_termine_et_persiste(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    async for _ in service.executer(rapport.identifiant, DEVICE):
        pass
    relu = await store.obtenir(rapport.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.TERMINE
    assert relu.markdown.startswith("# Bulletin régional")


async def test_un_rapport_d_un_autre_appareil_est_introuvable(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    with pytest.raises(RapportIntrouvable):
        async for _ in service.executer(rapport.identifiant, "appareil-b"):
            pass


async def test_une_inference_qui_echoue_marque_le_job_echoue(store: RapportStore):
    """Un echec doit etre PROPRE : le client ne doit pas attendre indefiniment."""
    service = _service(store, InferenceCassee())
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    evenements = [evenement async for evenement in service.executer(rapport.identifiant, DEVICE)]
    assert evenements[-1]["type"] == "error"
    relu = await store.obtenir(rapport.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.ECHOUE


async def test_un_sujet_refuse_marque_le_job_echoue(store: RapportStore):
    """Le garde-fou du moteur doit remonter proprement, pas en trace."""
    service = _service(store)
    rapport = await service.creer("etude_filiere", "la culture de l anacarde", DEVICE)
    evenements = [evenement async for evenement in service.executer(rapport.identifiant, DEVICE)]
    assert evenements[-1]["type"] == "error"
    relu = await store.obtenir(rapport.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.ECHOUE


async def test_le_message_d_erreur_ne_fuit_pas_les_details_techniques(store: RapportStore):
    """Le detail va au journal, pas au client."""
    service = _service(store, InferenceCassee())
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    evenements = [evenement async for evenement in service.executer(rapport.identifiant, DEVICE)]
    assert "inférence indisponible" not in evenements[-1]["message"]


# ------------------------------------------------------------------ export


@pytest.mark.parametrize(
    "format_demande,extension,debut",
    [
        ("md", "md", b"# "),
        ("docx", "docx", b"PK"),
        ("xlsx", "xlsx", b"PK"),
        ("pptx", "pptx", b"PK"),
    ],
)
async def test_les_quatre_formats_sont_exportables(
    store: RapportStore, format_demande, extension, debut
):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    async for _ in service.executer(rapport.identifiant, DEVICE):
        pass
    octets, nom, type_mime = await service.exporter(rapport.identifiant, DEVICE, format_demande)
    assert octets.startswith(debut)
    assert nom.endswith(f".{extension}")
    assert type_mime


async def test_un_format_inconnu_est_refuse(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    async for _ in service.executer(rapport.identifiant, DEVICE):
        pass
    with pytest.raises(ValueError):
        await service.exporter(rapport.identifiant, DEVICE, "pdf")


async def test_exporter_un_rapport_non_termine_est_refuse(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    with pytest.raises(RapportIntrouvable):
        await service.exporter(rapport.identifiant, DEVICE, "md")


async def test_exporter_le_rapport_d_un_autre_appareil_est_refuse(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    async for _ in service.executer(rapport.identifiant, DEVICE):
        pass
    with pytest.raises(RapportIntrouvable):
        await service.exporter(rapport.identifiant, "appareil-b", "md")


async def test_un_format_binaire_apres_redemarrage_est_refuse(store: RapportStore):
    """Seul le Markdown est persiste : on le DIT plutot que de regenerer un autre document."""
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    async for _ in service.executer(rapport.identifiant, DEVICE):
        pass

    apres_redemarrage = _service(store)
    octets, _, _ = await apres_redemarrage.exporter(rapport.identifiant, DEVICE, "md")
    assert octets.startswith(b"# ")
    with pytest.raises(RapportIntrouvable):
        await apres_redemarrage.exporter(rapport.identifiant, DEVICE, "docx")


async def test_le_nom_de_fichier_ne_reprend_pas_le_sujet(store: RapportStore):
    """Le sujet vient du client : il n a rien a faire dans un en-tete HTTP."""
    service = _service(store)
    rapport = await service.creer("bulletin_regional", 'Daloa"; rm -rf /', DEVICE)
    async for _ in service.executer(rapport.identifiant, DEVICE):
        pass
    _, nom, _ = await service.exporter(rapport.identifiant, DEVICE, "md")
    assert '"' not in nom
    assert "rm -rf" not in nom


async def test_rejouer_un_rapport_termine_ne_le_detruit_pas(store: RapportStore):
    """Un GET ne doit RIEN detruire.

    Mesure avant correctif : rejouer un job TERMINE alors que l inference etait tombee
    le faisait basculer en ECHOUE, et l export renvoyait 404 — alors que le markdown
    etait intact en base. Un rechargement d onglet suffisait.
    """
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    async for _ in service.executer(rapport.identifiant, DEVICE):
        pass

    casse = _service(store, InferenceCassee())
    evenements = [evenement async for evenement in casse.executer(rapport.identifiant, DEVICE)]

    assert evenements[-1]["type"] == "error"
    relu = await store.obtenir(rapport.identifiant, DEVICE)
    assert relu is not None
    assert relu.etat is EtatRapport.TERMINE  # intact
    assert relu.markdown.startswith("# Bulletin régional")


async def test_deux_flux_simultanes_ne_generent_qu_une_fois(store: RapportStore):
    """Deux clients sur le meme lien : un seul genere, l autre est econduit."""
    import asyncio

    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)

    async def _consommer() -> list[dict]:
        return [evenement async for evenement in service.executer(rapport.identifiant, DEVICE)]

    premier, second = await asyncio.gather(_consommer(), _consommer())
    finaux = [e["type"] for flux in (premier, second) for e in flux if e["type"] == "final"]
    assert len(finaux) == 1


async def test_un_rapport_deja_termine_n_est_pas_regenere(store: RapportStore):
    service = _service(store)
    rapport = await service.creer("bulletin_regional", "Daloa", DEVICE)
    async for _ in service.executer(rapport.identifiant, DEVICE):
        pass
    evenements = [evenement async for evenement in service.executer(rapport.identifiant, DEVICE)]
    assert evenements[-1]["type"] == "error"
    assert "déjà" in evenements[-1]["message"]


async def test_la_memoire_des_documents_est_bornee(store: RapportStore):
    """Sans borne, le pod (1 Gio, partage avec l index RAG) finit par tomber."""
    from app.services.rapports import MAX_DOCUMENTS_EN_MEMOIRE

    service = _service(store)
    for indice in range(MAX_DOCUMENTS_EN_MEMOIRE + 5):
        rapport = await service.creer("bulletin_regional", f"zone {indice}", DEVICE)
        async for _ in service.executer(rapport.identifiant, DEVICE):
            pass
    assert len(service._documents) == MAX_DOCUMENTS_EN_MEMOIRE


async def test_un_document_evince_se_comporte_comme_apres_un_redemarrage(store: RapportStore):
    """L eviction rend l export binaire indisponible — cas deja gere et deja teste."""
    from app.services.rapports import MAX_DOCUMENTS_EN_MEMOIRE

    service = _service(store)
    premier = await service.creer("bulletin_regional", "premier", DEVICE)
    async for _ in service.executer(premier.identifiant, DEVICE):
        pass
    for indice in range(MAX_DOCUMENTS_EN_MEMOIRE):
        suivant = await service.creer("bulletin_regional", f"zone {indice}", DEVICE)
        async for _ in service.executer(suivant.identifiant, DEVICE):
            pass

    octets, _, _ = await service.exporter(premier.identifiant, DEVICE, "md")
    assert octets.startswith(b"# ")  # le Markdown reste servi depuis la base
    with pytest.raises(RapportIntrouvable):
        await service.exporter(premier.identifiant, DEVICE, "docx")
