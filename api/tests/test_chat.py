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


def test_validation_question_trop_courte(client) -> None:
    """Une question trop courte est rejetée par la validation Pydantic (422)."""
    resp = client.post("/v1/chat", json={"question": "a"})
    assert resp.status_code == 422


def test_health(client) -> None:
    """Le liveness probe répond 200."""
    assert client.get("/v1/health").status_code == 200


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


async def test_stream_bloque_un_dosage_en_sortie(fake_cache) -> None:
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

    service = ConseilService(inference=_FluxAvecDosage(), cache=fake_cache)
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
