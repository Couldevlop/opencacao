"""Tests d'intégration des endpoints de sessions et du chat persistant (V2)."""

from __future__ import annotations

import json


def _evenements(resp) -> list[dict]:
    """Parse un corps SSE en liste d'événements JSON."""
    evts: list[dict] = []
    for bloc in resp.text.split("\n\n"):
        for ligne in bloc.splitlines():
            if ligne.startswith("data:"):
                evts.append(json.loads(ligne[len("data:") :].strip()))
    return evts


# --- CRUD des sessions (/v1/sessions) ---


def test_creer_session_par_defaut(client) -> None:
    """POST /v1/sessions crée une session avec un titre par défaut et un id."""
    resp = client.post("/v1/sessions")
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"]
    assert data["titre"] == "Nouvelle conversation"
    assert data["langue"] == "fr"


def test_creer_session_avec_titre(client) -> None:
    """Le corps optionnel permet de fixer le titre et le canal."""
    resp = client.post("/v1/sessions", json={"titre": "Séchage", "canal": "whatsapp"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["titre"] == "Séchage"
    assert data["canal"] == "whatsapp"


def test_lister_sessions_tri_par_activite(client) -> None:
    """GET /v1/sessions renvoie les sessions, la plus récemment active en tête."""
    a = client.post("/v1/sessions", json={"titre": "A"}).json()
    client.post("/v1/sessions", json={"titre": "B"})
    # On active A : il doit repasser en tête.
    client.post(
        "/v1/chat", json={"question": "Comment tailler le cacaoyer ?", "session_id": a["id"]}
    )

    titres = [s["titre"] for s in client.get("/v1/sessions").json()]
    assert titres[0] == "A"
    assert set(titres) == {"A", "B"}


def test_obtenir_session_avec_messages(client) -> None:
    """GET /v1/sessions/{id} renvoie la session et ses messages persistés."""
    sid = client.post("/v1/sessions").json()["id"]
    client.post("/v1/chat", json={"question": "Comment sécher mes fèves ?", "session_id": sid})

    detail = client.get(f"/v1/sessions/{sid}").json()
    assert detail["session"]["id"] == sid
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "Comment sécher mes fèves ?"


def test_obtenir_session_inconnue_404(client) -> None:
    assert client.get("/v1/sessions/inexistant").status_code == 404


def test_supprimer_session(client) -> None:
    """DELETE supprime la session (puis 404 sur un second appel)."""
    sid = client.post("/v1/sessions").json()["id"]
    assert client.delete(f"/v1/sessions/{sid}").status_code == 204
    assert client.get(f"/v1/sessions/{sid}").status_code == 404
    assert client.delete(f"/v1/sessions/{sid}").status_code == 404


def test_creer_session_rate_limit(client) -> None:
    """La création massive de sessions finit par être plafonnée (429, anti-abus)."""
    statuts = {client.post("/v1/sessions").status_code for _ in range(25)}
    assert 429 in statuts


# --- Chat avec mémoire serveur ---


def test_chat_persiste_les_deux_messages(client) -> None:
    """Un tour de chat avec session_id persiste la question et la réponse."""
    sid = client.post("/v1/sessions").json()["id"]
    resp = client.post(
        "/v1/chat",
        json={"question": "Comment bien sécher mes fèves de cacao ?", "session_id": sid},
    )
    assert resp.status_code == 200
    assert resp.json()["session_id"] == sid

    messages = client.get(f"/v1/sessions/{sid}").json()["messages"]
    assert len(messages) == 2
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"]


def test_chat_memoire_serveur_sur_deux_tours(client, fake_inference) -> None:
    """Au 2e tour, l'historique serveur suffit : pas besoin de le renvoyer côté client."""
    sid = client.post("/v1/sessions").json()["id"]
    # 1er tour : symptôme vague -> clarification (aucune inférence).
    client.post(
        "/v1/chat",
        json={"question": "Mes feuilles de cacaoyer jaunissent, que faire ?", "session_id": sid},
    )
    assert fake_inference.appels == []
    # 2e tour : on répond, SANS renvoyer d'historique — le serveur le reconstitue.
    resp = client.post(
        "/v1/chat",
        json={
            "question": "Sur les feuilles, depuis deux semaines, je suis à Daloa",
            "session_id": sid,
        },
    )
    assert resp.status_code == 200
    assert len(fake_inference.appels) == 1  # le contexte serveur a débloqué la réponse

    messages = client.get(f"/v1/sessions/{sid}").json()["messages"]
    assert len(messages) == 4  # 2 tours complets persistés


def test_chat_session_inconnue_404(client) -> None:
    """Un session_id inconnu sur /v1/chat renvoie 404."""
    resp = client.post(
        "/v1/chat",
        json={"question": "Comment tailler un cacaoyer ?", "session_id": "inexistant"},
    )
    assert resp.status_code == 404


def test_chat_sans_session_reste_sans_etat(client) -> None:
    """Sans session_id, aucune session n'est créée (rétrocompatibilité V1)."""
    resp = client.post("/v1/chat", json={"question": "Comment tailler un cacaoyer ?"})
    assert resp.status_code == 200
    assert resp.json()["session_id"] is None
    assert client.get("/v1/sessions").json() == []


def test_chat_stream_persiste_et_renvoie_session_id(client) -> None:
    """Le flux SSE avec session_id émet le session_id dans 'done' et persiste le tour."""
    sid = client.post("/v1/sessions").json()["id"]
    resp = client.post(
        "/v1/chat/stream",
        json={"question": "Comment bien sécher mes fèves de cacao ?", "session_id": sid},
    )
    assert resp.status_code == 200
    finaux = [e for e in _evenements(resp) if e["type"] == "done"]
    assert finaux and finaux[0]["session_id"] == sid

    messages = client.get(f"/v1/sessions/{sid}").json()["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "user"


def test_chat_stream_session_inconnue_emet_une_erreur(client) -> None:
    """Le flux signale une session inconnue par un événement d'erreur."""
    resp = client.post(
        "/v1/chat/stream",
        json={"question": "Comment tailler un cacaoyer ?", "session_id": "inexistant"},
    )
    erreurs = [e for e in _evenements(resp) if e["type"] == "error"]
    assert erreurs and erreurs[0]["kind"] == "session_inconnue"


# --- Mémoire conversationnelle : titre auto (B3) et ré-ancrage garde-fous (B4) ---


def test_chat_titre_auto_depuis_la_premiere_question(client) -> None:
    """Le premier tour renomme la session par défaut depuis la question (B3)."""
    sid = client.post("/v1/sessions").json()["id"]
    client.post(
        "/v1/chat",
        json={"question": "Comment bien sécher mes fèves de cacao ?", "session_id": sid},
    )
    titre = client.get(f"/v1/sessions/{sid}").json()["session"]["titre"]
    assert titre == "Comment bien sécher mes fèves de cacao"


# --- Identité par appareil (D1), renommage (C3), recherche (C5) ---


def _dev(id_appareil: str) -> dict[str, str]:
    """En-tête d'identité d'appareil pour cloisonner les conversations."""
    return {"X-Device-Id": id_appareil}


def test_sessions_cloisonnees_par_appareil(client) -> None:
    """Un appareil ne liste que ses propres conversations (D1)."""
    client.post("/v1/sessions", json={"titre": "Chez A"}, headers=_dev("A"))
    client.post("/v1/sessions", json={"titre": "Chez B"}, headers=_dev("B"))

    titres_a = [s["titre"] for s in client.get("/v1/sessions", headers=_dev("A")).json()]
    assert titres_a == ["Chez A"]
    titres_b = [s["titre"] for s in client.get("/v1/sessions", headers=_dev("B")).json()]
    assert titres_b == ["Chez B"]


def test_acces_session_autre_appareil_404(client) -> None:
    """Lire/supprimer la conversation d'un autre appareil renvoie 404 (D1)."""
    sid = client.post("/v1/sessions", headers=_dev("A")).json()["id"]
    assert client.get(f"/v1/sessions/{sid}", headers=_dev("B")).status_code == 404
    assert client.delete(f"/v1/sessions/{sid}", headers=_dev("B")).status_code == 404
    assert client.get(f"/v1/sessions/{sid}", headers=_dev("A")).status_code == 200


def test_renommer_session(client) -> None:
    """PATCH renomme la conversation et renvoie ses métadonnées à jour (C3)."""
    sid = client.post("/v1/sessions", headers=_dev("A")).json()["id"]
    resp = client.patch(f"/v1/sessions/{sid}", json={"titre": "Séchage à Daloa"}, headers=_dev("A"))
    assert resp.status_code == 200
    assert resp.json()["titre"] == "Séchage à Daloa"
    assert client.get(f"/v1/sessions/{sid}", headers=_dev("A")).json()["session"]["titre"] == (
        "Séchage à Daloa"
    )


def test_renommer_session_autre_appareil_404(client) -> None:
    sid = client.post("/v1/sessions", headers=_dev("A")).json()["id"]
    resp = client.patch(f"/v1/sessions/{sid}", json={"titre": "X"}, headers=_dev("B"))
    assert resp.status_code == 404


def test_recherche_sessions(client) -> None:
    """GET /v1/sessions/recherche trouve par titre, scopé par appareil (C5)."""
    client.post("/v1/sessions", json={"titre": "Séchage des fèves"}, headers=_dev("A"))
    client.post("/v1/sessions", json={"titre": "Taille du cacaoyer"}, headers=_dev("A"))
    client.post("/v1/sessions", json={"titre": "Séchage chez B"}, headers=_dev("B"))

    res = client.get("/v1/sessions/recherche", params={"q": "séchage"}, headers=_dev("A")).json()
    assert [s["titre"] for s in res] == ["Séchage des fèves"]
    # « recherche » n'est pas confondu avec un id de session.
    autre = client.get("/v1/sessions/recherche", params={"q": "taille"}, headers=_dev("A"))
    assert autre.status_code == 200


def test_chat_garde_fou_dose_ancre_sur_le_fil(client, fake_inference) -> None:
    """Une demande de dosage étalée sur deux tours est refusée (B4).

    Tour 1 : le producteur évoque un fongicide (aucune dose -> traité normalement).
    Tour 2 : « et quelle dose ? », isolée, ne porte aucun terme phytosanitaire ; mais
    ancrée au tour précédent, elle redevient une demande de dosage -> refus ANADER,
    sans jamais atteindre le modèle.
    """
    sid = client.post("/v1/sessions").json()["id"]
    client.post(
        "/v1/chat",
        json={
            "question": "Comment lutter contre la pourriture brune avec un fongicide ?",
            "session_id": sid,
        },
    )
    resp = client.post("/v1/chat", json={"question": "Et quelle dose faut-il ?", "session_id": sid})
    assert resp.status_code == 200
    data = resp.json()
    assert data["redirection_anader"] is True
    assert "ANADER" in data["reponse"]
    # Le garde-fou a court-circuité l'inférence : aucun appel au modèle.
    assert fake_inference.appels == []
