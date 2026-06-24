"""Tests d'intégration de l'authentification par lien magique (D2)."""

from __future__ import annotations


def _token_du_lien(fake_notifier) -> str:
    """Extrait le jeton du dernier lien capturé par le notifier."""
    assert fake_notifier.envois, "aucun lien envoyé"
    _, lien = fake_notifier.envois[-1]
    return lien.split("auth=")[1]


def test_demander_puis_verifier(auth_client, fake_notifier) -> None:
    """Flux complet : demande du lien (202) → vérification (identité) → usage unique."""
    resp = auth_client.post("/v1/auth/request", json={"email": "Paysan@Cacao.CI"})
    assert resp.status_code == 202

    token = _token_du_lien(fake_notifier)
    verif = auth_client.post("/v1/auth/verify", json={"token": token})
    assert verif.status_code == 200
    data = verif.json()
    assert data["email"] == "paysan@cacao.ci"  # normalisé en minuscules
    assert data["account_id"].startswith("acct_")

    # Le lien ne sert qu'une fois.
    assert auth_client.post("/v1/auth/verify", json={"token": token}).status_code == 400


def test_compte_stable_entre_connexions(auth_client, fake_notifier) -> None:
    """Deux connexions du même email donnent le même identifiant de compte."""
    auth_client.post("/v1/auth/request", json={"email": "a@cacao.ci"})
    id1 = auth_client.post("/v1/auth/verify", json={"token": _token_du_lien(fake_notifier)}).json()[
        "account_id"
    ]
    auth_client.post("/v1/auth/request", json={"email": "a@cacao.ci"})
    id2 = auth_client.post("/v1/auth/verify", json={"token": _token_du_lien(fake_notifier)}).json()[
        "account_id"
    ]
    assert id1 == id2


def test_verifier_jeton_invalide(auth_client) -> None:
    assert auth_client.post("/v1/auth/verify", json={"token": "x" * 20}).status_code == 400


def test_demander_email_invalide(auth_client) -> None:
    assert auth_client.post("/v1/auth/request", json={"email": "pas-un-email"}).status_code == 422


def test_compte_scope_les_conversations(auth_client, fake_notifier) -> None:
    """L'identifiant de compte sert de proprietaire : il isole les conversations (D1)."""
    auth_client.post("/v1/auth/request", json={"email": "x@cacao.ci"})
    account = auth_client.post(
        "/v1/auth/verify", json={"token": _token_du_lien(fake_notifier)}
    ).json()["account_id"]

    auth_client.post("/v1/sessions", json={"titre": "Mienne"}, headers={"X-Device-Id": account})
    a_moi = auth_client.get("/v1/sessions", headers={"X-Device-Id": account}).json()
    assert [s["titre"] for s in a_moi] == ["Mienne"]
    # Un autre identifiant ne voit pas la conversation du compte.
    assert auth_client.get("/v1/sessions", headers={"X-Device-Id": "autre"}).json() == []


def test_auth_desactivee_renvoie_404(client) -> None:
    """Sans AUTH_ENABLED (client par défaut), les routes d'auth sont absentes (404)."""
    assert client.post("/v1/auth/request", json={"email": "a@cacao.ci"}).status_code == 404
    assert client.post("/v1/auth/verify", json={"token": "x" * 20}).status_code == 404
