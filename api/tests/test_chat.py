"""Tests d'intégration de l'API /v1/chat (inférence mockée)."""

from __future__ import annotations

from app.models.chat import DISCLAIMER


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


def test_version(client) -> None:
    """L'endpoint version expose les métadonnées du modèle."""
    resp = client.get("/v1/version")
    assert resp.status_code == 200
    assert resp.json()["model_name"] == "opencacao-7b"


def test_headers_securite(client) -> None:
    """Les en-têtes de sécurité sont présents."""
    resp = client.get("/v1/health")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
