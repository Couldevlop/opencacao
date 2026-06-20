"""Tests d'intégration de l'API /v1/chat (inférence mockée)."""

from __future__ import annotations

import json

from app.application.conseil_service import ConseilService
from app.models.chat import DISCLAIMER
from app.models.domain import Langue


def _evenements(resp) -> list[dict]:
    """Parse un corps SSE en liste d'événements JSON."""
    evts: list[dict] = []
    for bloc in resp.text.split("\n\n"):
        for ligne in bloc.splitlines():
            if ligne.startswith("data:"):
                evts.append(json.loads(ligne[len("data:") :].strip()))
    return evts


def test_chat_reponse_nominale(client) -> None:
    """Une question valide renvoie une réponse avec sources et disclaimer."""
    resp = client.post(
        "/v1/chat",
        json={"question": "Comment bien sécher mes fèves de cacao ?", "canal": "web"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["disclaimer"] == DISCLAIMER
    assert data["redirection_anader"] is False
    assert "CNRA" in data["sources"]
    assert data["confiance"] == "elevee"


def test_chat_garde_fou_phyto(client) -> None:
    """Une demande de dosage phyto renvoie une redirection ANADER sans inférence."""
    resp = client.post(
        "/v1/chat",
        json={"question": "Quelle dose de fongicide appliquer ?", "canal": "sms"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["redirection_anader"] is True
    assert "ANADER" in data["reponse"]


def test_chat_multitours_accepte_historique(client) -> None:
    """Une requête avec historique (multi-tours) est acceptée et répond."""
    resp = client.post(
        "/v1/chat",
        json={
            "question": "Et pour le séchage ?",
            "canal": "web",
            "historique": [
                {"role": "user", "content": "Comment récolter le cacao ?"},
                {"role": "assistant", "content": "Récoltez les cabosses mûres."},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["reponse"]


def test_chat_injecte_contact_local_si_ville_connue(client) -> None:
    """Demande de contact + ville citée : le contact local exact est ajouté."""
    resp = client.post(
        "/v1/chat",
        json={"question": "Quel est le numéro de l'ANADER à Daloa ?", "canal": "web"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["redirection_anader"] is True
    assert "Direction Régionale Centre-Ouest" in data["reponse"]
    assert "ANADER" in data["sources"]


def test_chat_repli_siege_si_ville_inconnue(client) -> None:
    """Sans ville reconnaissable, le siège confirmé est fourni (repli fiable garanti)."""
    resp = client.post(
        "/v1/chat",
        json={"question": "Quel est le numéro de l'ANADER ?", "canal": "web"},
    )
    assert resp.status_code == 200
    reponse = resp.json()["reponse"]
    assert "Direction Régionale" not in reponse  # aucune DR locale (ville inconnue)
    assert "Siège national" in reponse  # mais le siège confirmé est donné


def test_chat_garde_fou_avec_contact_local(client) -> None:
    """Un refus phyto avec une ville connue ajoute le contact ANADER de la zone."""
    resp = client.post(
        "/v1/chat",
        json={"question": "Quelle dose de fongicide utiliser à Korhogo ?", "canal": "sms"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["redirection_anader"] is True
    assert "Direction Régionale Nord" in data["reponse"]


def test_chat_multitours_ignore_le_cache(client, fake_inference) -> None:
    """Une requête multi-tours ne lit pas le cache (réponse dépendante du contexte)."""
    body = {"question": "Comment préparer une pépinière de cacaoyer demain ?", "canal": "web"}
    client.post("/v1/chat", json=body)
    client.post("/v1/chat", json={**body, "historique": [{"role": "user", "content": "bonjour"}]})
    assert len(fake_inference.appels) == 2  # le 2e (multi-tours) ne vient pas du cache


def test_chat_utilise_le_cache(client, fake_inference) -> None:
    """La deuxième requête identique est servie par le cache (pas de 2e inférence)."""
    body = {"question": "Comment préparer une pépinière de cacaoyer ?", "canal": "web"}
    client.post("/v1/chat", json=body)
    client.post("/v1/chat", json=body)
    assert len(fake_inference.appels) == 1


def test_chat_inference_indisponible(client, fake_inference) -> None:
    """Si l'inférence échoue, l'API répond 503."""
    fake_inference.disponible = False
    resp = client.post(
        "/v1/chat",
        json={"question": "Quelle densité de plantation pour le cacaoyer ?", "canal": "web"},
    )
    assert resp.status_code == 503


def test_chat_rate_limit(client) -> None:
    """Au-delà de la limite, l'API répond 429."""
    body = {"question": "Question variée numéro", "canal": "web"}
    statuts = set()
    for i in range(25):
        resp = client.post("/v1/chat", json={**body, "question": f"Question {i} cacao ?"})
        statuts.add(resp.status_code)
    assert 429 in statuts


def test_chat_cache_ne_consomme_pas_le_quota(client, fake_inference) -> None:
    """Une question en cache, reposée au-delà du quota, reste servie (pas de 429)."""
    body = {"question": "Comment conserver les feves de cacao apres sechage ?", "canal": "web"}
    statuts = {client.post("/v1/chat", json=body).status_code for _ in range(25)}
    assert statuts == {200}  # jamais 429 : le cache ne décompte pas le quota
    assert len(fake_inference.appels) == 1  # une seule inférence (le reste = cache)


def test_chat_enregistre_une_visite(client, fake_journal) -> None:
    """Chaque interrogation enregistre une visite (canal + pays), sans IP."""
    client.post("/v1/chat", json={"question": "Comment tailler un cacaoyer ?", "canal": "web"})
    assert fake_journal.visites
    assert fake_journal.visites[0]["canal"] == "web"


def test_validation_question_trop_courte(client) -> None:
    """Une question trop courte est rejetée par la validation Pydantic (422)."""
    resp = client.post("/v1/chat", json={"question": "a"})
    assert resp.status_code == 422


def test_health(client) -> None:
    """Le liveness probe répond 200."""
    assert client.get("/v1/health").status_code == 200


# --- Journalisation & retour utilisateur (boucle d'amélioration) ---


def test_chat_renvoie_un_interaction_id(client) -> None:
    """La réponse porte un interaction_id, support du retour 👍/👎."""
    resp = client.post(
        "/v1/chat", json={"question": "Comment tailler un cacaoyer ?", "canal": "web"}
    )
    assert resp.status_code == 200
    assert resp.json()["interaction_id"]


def test_feedback_enregistre_le_vote(client, fake_journal) -> None:
    """Un retour 👍/👎 valide est accepté (204) et journalisé."""
    chat = client.post("/v1/chat", json={"question": "Comment greffer le cacaoyer ?"})
    interaction_id = chat.json()["interaction_id"]
    resp = client.post("/v1/feedback", json={"interaction_id": interaction_id, "vote": "up"})
    assert resp.status_code == 202
    assert fake_journal.feedbacks == [{"id": interaction_id, "vote": "up"}]


def test_feedback_vote_invalide_rejete(client) -> None:
    """Un vote hors {up, down} est rejeté par la validation (422)."""
    resp = client.post("/v1/feedback", json={"interaction_id": "test00000000", "vote": "peut-etre"})
    assert resp.status_code == 422


# --- Streaming (/v1/chat/stream) ---


def test_chat_stream_nominale(client) -> None:
    """Le flux SSE émet des tokens puis un événement final avec sources/disclaimer."""
    resp = client.post(
        "/v1/chat/stream",
        json={"question": "Comment bien sécher mes fèves de cacao ?", "canal": "web"},
    )
    assert resp.status_code == 200
    evts = _evenements(resp)
    tokens = "".join(e["text"] for e in evts if e["type"] == "token")
    finaux = [e for e in evts if e["type"] == "done"]
    assert tokens.strip()
    assert finaux and finaux[0]["disclaimer"] == DISCLAIMER
    assert "CNRA" in finaux[0]["sources"]
    assert finaux[0]["interaction_id"]


def test_chat_stream_garde_fou_phyto(client) -> None:
    """Une demande de dosage en entrée redirige vers l'ANADER, sans inférence."""
    resp = client.post(
        "/v1/chat/stream",
        json={"question": "Quelle dose de fongicide appliquer ?", "canal": "sms"},
    )
    evts = _evenements(resp)
    tokens = "".join(e["text"] for e in evts if e["type"] == "token")
    finaux = [e for e in evts if e["type"] == "done"]
    assert "ANADER" in tokens
    assert finaux and finaux[0]["redirection_anader"] is True


async def test_stream_bloque_un_dosage_en_sortie(fake_cache, fake_journal) -> None:
    """Si le modèle émet un dosage, il n'est JAMAIS diffusé (garde-fou de sortie)."""

    class _FluxAvecDosage:
        def __init__(self) -> None:
            self.reponse = (
                "Surveillez vos cabosses regulierement. "
                "Appliquez 2 l/ha de bouillie sur les zones atteintes."
            )

        async def generer(self, question: str, **_: object) -> str:
            return self.reponse

        async def generer_stream(self, question: str, **_: object):
            for mot in self.reponse.split(" "):
                yield mot + " "

        async def ready(self) -> bool:
            return True

    service = ConseilService(inference=_FluxAvecDosage(), cache=fake_cache, journal=fake_journal)
    evts = [
        e
        async for e in service.conseiller_stream(
            "Comment traiter la pourriture brune ?", Langue.FR, "1.2.3.4"
        )
    ]
    tokens = "".join(e["text"] for e in evts if e["type"] == "token")
    finaux = [e for e in evts if e["type"] == "done"]
    assert "l/ha" not in tokens.lower()  # le dosage n'est jamais diffusé
    assert "ANADER" in tokens  # redirection émise à la place
    assert finaux and finaux[0]["redirection_anader"] is True


async def test_rag_injecte_le_contexte_a_l_inference(fake_cache, fake_journal) -> None:
    """Quand le RAG est branché, le contexte récupéré est passé à l'inférence."""

    class _InferenceCapture:
        def __init__(self) -> None:
            self.contexte = None

        async def generer(self, question: str, **kw: object) -> str:
            self.contexte = kw.get("contexte")
            return "Réponse fondée sur le contexte. Sources : CNRA."

        async def generer_stream(self, question: str, **kw: object):
            self.contexte = kw.get("contexte")
            yield "Réponse. "

        async def ready(self) -> bool:
            return True

    class _RagStub:
        async def contexte_pour(self, question: str) -> str:
            return "[1] (source : CNRA) Récoltez les cabosses mûres."

    capture = _InferenceCapture()
    service = ConseilService(
        inference=capture, cache=fake_cache, journal=fake_journal, rag=_RagStub()
    )
    _ = [
        e
        async for e in service.conseiller_stream("Quand récolter le cacao ?", Langue.FR, "1.2.3.4")
    ]
    assert capture.contexte == "[1] (source : CNRA) Récoltez les cabosses mûres."


def test_chat_stream_indisponible(client, fake_inference) -> None:
    """Si l'inférence échoue, le flux émet un événement d'erreur 'indisponible'."""
    fake_inference.disponible = False
    resp = client.post(
        "/v1/chat/stream",
        json={"question": "Quelle densité de plantation pour le cacaoyer ?", "canal": "web"},
    )
    erreurs = [e for e in _evenements(resp) if e["type"] == "error"]
    assert erreurs and erreurs[0]["kind"] == "indisponible"


def test_chat_stream_rate_limit(client) -> None:
    """Au-delà de la limite, le flux émet un événement d'erreur 'rate_limit'."""
    erreurs: list[dict] = []
    for i in range(25):
        resp = client.post(
            "/v1/chat/stream",
            json={"question": f"Question cacao numéro {i} ?", "canal": "web"},
        )
        erreurs += [
            e for e in _evenements(resp) if e["type"] == "error" and e["kind"] == "rate_limit"
        ]
    assert erreurs


def test_version(client) -> None:
    """L'endpoint version expose les métadonnées du modèle."""
    resp = client.get("/v1/version")
    assert resp.status_code == 200
    assert resp.json()["model_name"] == "opencacao-8b"


def test_headers_securite(client) -> None:
    """Les en-têtes de sécurité sont présents."""
    resp = client.get("/v1/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
