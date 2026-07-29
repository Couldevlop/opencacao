"""Tests du client HTTP du modèle de vision local (aucun appel réseau réel)."""

from __future__ import annotations

import httpx
import pytest

from app.services.vision.vlm import ClientVLM

IMAGE = b"\xff\xd8\xff\xe0 fausse image jpeg"


def _client(transport: httpx.MockTransport) -> ClientVLM:
    vlm = ClientVLM(base_url="http://vision:8000", modele="qwen3-vl")
    vlm._client = httpx.AsyncClient(transport=transport, base_url="http://vision:8000")
    return vlm


async def test_decrire_retourne_le_texte_du_modele():
    def repondre(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Cabosse mûre, surface régulière."}}]},
        )

    texte = await _client(httpx.MockTransport(repondre)).decrire((IMAGE,), "décris")
    assert texte == "Cabosse mûre, surface régulière."


async def test_les_images_partent_en_data_uri_base64():
    """Le VLM est servi en interne : on lui passe les octets, pas une URL publique."""
    vues: dict[str, object] = {}

    def repondre(requete: httpx.Request) -> httpx.Response:
        vues["corps"] = requete.content.decode("utf-8")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    await _client(httpx.MockTransport(repondre)).decrire((IMAGE,), "décris")
    assert "data:image/jpeg;base64," in str(vues["corps"])
    assert "http://" not in str(vues["corps"]).split("data:image")[0][-200:]


async def test_une_erreur_http_degrade_sans_lever():
    def repondre(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    assert await _client(httpx.MockTransport(repondre)).decrire((IMAGE,), "décris") is None


async def test_un_timeout_degrade_sans_lever():
    def repondre(requete: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("trop lent")

    assert await _client(httpx.MockTransport(repondre)).decrire((IMAGE,), "décris") is None


async def test_une_reponse_malformee_degrade_sans_lever():
    def repondre(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pas": "ce qu'on attend"})

    assert await _client(httpx.MockTransport(repondre)).decrire((IMAGE,), "décris") is None


async def test_sans_image_on_n_appelle_meme_pas_le_modele():
    appels = {"n": 0}

    def repondre(requete: httpx.Request) -> httpx.Response:
        appels["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    assert await _client(httpx.MockTransport(repondre)).decrire((), "décris") is None
    assert appels["n"] == 0


async def test_disponible_suit_la_sonde_de_sante():
    def sain(requete: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    def malade(requete: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refus")

    assert await _client(httpx.MockTransport(sain)).disponible() is True
    assert await _client(httpx.MockTransport(malade)).disponible() is False


async def test_fermer_le_client_libere_la_connexion():
    """Le client est fermé au shutdown de l'application, comme celui d'inférence."""
    vlm = _client(httpx.MockTransport(lambda requete: httpx.Response(200, json={})))
    await vlm.close()
    assert vlm._client.is_closed


@pytest.mark.parametrize("nombre", [1, 3, 12])
async def test_toutes_les_images_sont_transmises(nombre):
    vues: dict[str, int] = {}

    def repondre(requete: httpx.Request) -> httpx.Response:
        vues["n"] = requete.content.decode("utf-8").count("data:image/jpeg;base64,")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    await _client(httpx.MockTransport(repondre)).decrire(tuple([IMAGE] * nombre), "décris")
    assert vues["n"] == nombre
