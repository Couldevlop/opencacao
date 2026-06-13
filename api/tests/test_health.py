"""Tests des sondes de santé /v1/ready (cas disponible et indisponible)."""

from __future__ import annotations


def test_ready_ok_quand_tout_disponible(client) -> None:
    """/ready renvoie 200 quand inférence et Redis répondent."""
    resp = client.get("/v1/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"ready": True, "inference": True, "redis": True}


def test_ready_503_quand_inference_indisponible(client, fake_inference) -> None:
    """/ready renvoie 503 si l'inférence n'est pas prête."""
    fake_inference.disponible = False
    resp = client.get("/v1/ready")
    assert resp.status_code == 503
    data = resp.json()
    assert data["ready"] is False
    assert data["inference"] is False
