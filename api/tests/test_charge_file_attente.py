"""Charge simulée sur l'atelier — critère d'acceptation C4 §9.6.

« Sous charge simulée, la file annonce une position et **aucune requête ne meurt en
silence**. »

La position et le refus sont chacun couverts isolément ailleurs. Ce qui manquait est
la propriété d'ensemble sous concurrence réelle : quand douze demandes arrivent sur un
profil qui n'en sert qu'une à la fois, chacune doit repartir avec une issue nette —
servie, ou refusée lisiblement. Aucune ne doit se terminer sans rien avoir dit, ni
rester suspendue.

Le pire scénario n'est pas la lenteur, c'est la lenteur muette : c'est elle qui produit
une page blanche de quarante secondes devant huit cents personnes.
"""

from __future__ import annotations

import asyncio

import pytest

from app.application.file_attente import FileAttente
from app.application.redaction import ContexteGeneration, MoteurRedaction
from app.core.rapports_store import RapportStore
from app.models.constat import NiveauConfiance
from app.models.rapport import Affirmation
from app.services.rapports import ServiceRapports

DEVICE = "appareil-charge"

# Douze demandes pour une place et huit rangs d'attente : de quoi exercer les deux
# issues — servie et refusée — dans la même exécution.
DEMANDES = 12
PLACES = 1
ATTENTE_MAX = 8


class InferenceLente:
    """Inférence retenue jusqu'à un signal, pour que la contention soit certaine.

    Faire dormir la génération un temps fixe rendrait le test tributaire de
    l'ordonnancement : selon les tours de boucle, les douze demandes pourraient
    défiler sans jamais se croiser, et l'absence d'annonce ferait échouer un test
    pourtant vert la fois d'avant. On RETIENT la première génération le temps que tout
    le monde soit arrivé.

    Args:
        depart: Événement libérant les générations.
    """

    def __init__(self, depart: asyncio.Event) -> None:
        self._depart = depart

    async def generer(self, question: str, **_: object) -> str:
        await self._depart.wait()
        return "Un paragraphe analytique documenté."

    def generer_stream(self, *_: object, **__: object):
        raise NotImplementedError

    async def ready(self) -> bool:
        return True


class CollecteurFixe:
    """Source qui répond toujours, pour que rien ne bascule en lacune."""

    varie_par_section = True

    async def collecter(self, sujet: str, requete: str = "") -> tuple[Affirmation, ...]:
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


def _service(store: RapportStore, file: FileAttente, depart: asyncio.Event) -> ServiceRapports:
    contexte = ContexteGeneration("opencacao-8b", "1.1.0", "0.6.75", "cpu")
    collecteurs = dict.fromkeys(
        ("rag", "prix", "meteo", "satellite", "parcelle", "constats"), CollecteurFixe()
    )
    service = ServiceRapports(
        store,
        lambda: MoteurRedaction(InferenceLente(depart), collecteurs, contexte),
    )
    # Une seule file pour tous les services : c'est ce qui simule la contention d'un
    # unique processus d'inférence.
    service._file = file
    return service


async def _consommer(service: ServiceRapports, identifiant: str) -> list[dict]:
    """Consomme un flux entier et rend tous ses événements."""
    return [evenement async for evenement in service.executer(identifiant, DEVICE)]


@pytest.fixture(scope="module")
def charge(tmp_path_factory) -> dict:
    """Joue la charge UNE fois et rend ses résultats à tous les tests du module.

    Une fois, parce qu'un test ne doit pas dépendre d'une exécution différente de son
    voisin : les cinq assertions qui suivent portent sur la MÊME charge, donc décrivent
    un état cohérent plutôt que cinq tirages.
    """

    async def _jouer() -> dict:
        store = RapportStore(tmp_path_factory.mktemp("charge") / "rapports.db")
        await store.initialiser()
        depart = asyncio.Event()
        file = FileAttente(places=PLACES, attente_max=ATTENTE_MAX, duree_moyenne_s=40.0)
        services = [_service(store, file, depart) for _ in range(DEMANDES)]
        jobs = [
            await service.creer("bulletin_regional", f"Zone {rang}", DEVICE)
            for rang, service in enumerate(services)
        ]
        taches = [
            asyncio.create_task(_consommer(service, job.identifiant))
            for service, job in zip(services, jobs, strict=True)
        ]
        # Laisser tout le monde arriver — la file ne se forme qu'une fois les douze
        # demandes présentes. Les accès disque passent par des threads, donc quelques
        # tours de boucle ne suffiraient pas.
        await asyncio.sleep(0.2)
        depart.set()
        return {"flux": await asyncio.gather(*taches), "store": store}

    return asyncio.run(_jouer())


@pytest.fixture
def resultats(charge: dict) -> list[list[dict]]:
    """Les flux de la charge."""
    return charge["flux"]


async def test_aucune_demande_ne_meurt_en_silence(resultats: list[list[dict]]):
    """Le coeur du critere : douze demandes, douze issues nettes."""
    muettes = [rang for rang, flux in enumerate(resultats) if not flux]
    assert not muettes, f"flux sans le moindre evenement : {muettes}"

    # Une issue NETTE : le dernier evenement conclut, dans un sens ou dans l autre.
    # Un flux qui s arrete sur « progress » laisse le client devant une page qui tourne.
    fins = [flux[-1]["type"] for flux in resultats]
    assert set(fins) <= {"final", "error"}, f"issues indefinies : {set(fins)}"
    assert len(fins) == DEMANDES


async def test_chaque_demande_parle_avant_de_faire_attendre(resultats: list[list[dict]]):
    """Le premier octet part avant la generation — sans quoi l edge coupe (524)."""
    premiers = {flux[0]["type"] for flux in resultats}
    # « progress » (je commence) ou « attente » (tu es dans la file) : dans les deux
    # cas le client sait qu il est pris en charge. Jamais un silence.
    assert premiers <= {"progress", "attente"}, f"premiers evenements inattendus : {premiers}"


async def test_les_demandes_en_attente_recoivent_une_position_utilisable(
    resultats: list[list[dict]],
):
    """Une attente annoncee est toleree ; une attente muette est un echec public."""
    annonces = [
        evenement for flux in resultats for evenement in flux if evenement["type"] == "attente"
    ]
    assert annonces, "aucune annonce d attente alors que douze demandes se disputent une place"

    for annonce in annonces:
        # Jamais au-delà du plafond : annoncer « position 9 » quand on refuse à partir
        # de 9, c'est promettre une place qui sera refusée à la ligne suivante.
        assert 1 <= annonce["position"] <= ATTENTE_MAX
        # Une estimation grossiere permet de decider si l on attend ; une absence
        # d estimation ne permet rien.
        assert annonce["attente_s"] > 0
        assert annonce["message"]


async def test_les_refus_sont_lisibles_et_comptabilises(resultats: list[list[dict]]):
    """Refuser franchement vaut mieux qu accumuler derriere un edge qui coupera."""
    servies = [flux for flux in resultats if flux[-1]["type"] == "final"]
    refusees = [flux for flux in resultats if flux[-1]["type"] == "error"]

    # Rien ne se perd : chaque demande est dans exactement un des deux camps.
    assert len(servies) + len(refusees) == DEMANDES

    for flux in refusees:
        assert flux[-1].get("message"), "un refus sans message est un silence deguise"


async def test_la_charge_ne_corrompt_pas_l_etat_des_jobs(charge: dict):
    """Douze flux concurrents sur une meme base : aucun job ne reste en suspens."""
    jobs = await charge["store"].lister(DEVICE)
    assert len(jobs) == DEMANDES
    # Aucun job ne doit rester « en cours » : un job fige ainsi serait repris par
    # personne et ferait attendre son client indefiniment.
    en_suspens = [job.identifiant for job in jobs if job.etat.value == "en_cours"]
    assert not en_suspens, f"jobs restes en cours apres la charge : {en_suspens}"
