"""Tests du client cluster (rollout restart via l'API server)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.curation.k8s import ClusterClient, ClusterIndisponible


def _client(handler) -> ClusterClient:
    transport = httpx.MockTransport(handler)
    return ClusterClient(
        hote="https://kube",
        namespace="opencacao",
        token="jeton",
        verify=False,
        client=httpx.AsyncClient(transport=transport),
    )


async def test_rollout_restart_ok() -> None:
    vu: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        vu["url"] = str(request.url)
        vu["auth"] = request.headers.get("authorization")
        vu["ct"] = request.headers.get("content-type")
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    await client.rollout_restart("api")
    await client.close()
    assert vu["url"].endswith("/namespaces/opencacao/deployments/api")
    assert vu["auth"] == "Bearer jeton"
    assert "strategic-merge-patch" in vu["ct"]


async def test_rollout_restart_refus_leve_indisponible() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "interdit"})

    client = _client(handler)
    with pytest.raises(ClusterIndisponible):
        await client.rollout_restart("api")
    await client.close()


async def test_from_serviceaccount_lit_le_token(tmp_path: Path) -> None:
    (tmp_path / "token").write_text("jeton-sa", encoding="utf-8")
    (tmp_path / "namespace").write_text("opencacao", encoding="utf-8")
    (tmp_path / "ca.crt").write_text("---CA---", encoding="utf-8")
    client = ClusterClient.from_serviceaccount(sa=tmp_path, hote="https://kube")
    assert client._namespace == "opencacao"
    assert client._token == "jeton-sa"
    assert client._verify == str(tmp_path / "ca.crt")


async def test_from_serviceaccount_sans_token(tmp_path: Path) -> None:
    with pytest.raises(ClusterIndisponible):
        ClusterClient.from_serviceaccount(sa=tmp_path)


async def test_from_serviceaccount_sans_ca(tmp_path: Path) -> None:
    (tmp_path / "token").write_text("t", encoding="utf-8")
    (tmp_path / "namespace").write_text("ns", encoding="utf-8")
    client = ClusterClient.from_serviceaccount(sa=tmp_path)
    assert client._verify is True
