"""La route /v1/chat/photo — contrat HTTP de la photo dans le fil.

Elle existe à part de `/chat/stream` pour une raison de latence : l'analyse d'une image
tient un budget de plusieurs dizaines de secondes, et Cloudflare coupe vers 100 s. Le
premier octet doit donc partir immédiatement, avant même que la vision ne réponde.
"""

from __future__ import annotations

import base64
import json

_IHDR = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\r"
    + b"IHDR"
    + (640).to_bytes(4, "big")
    + (480).to_bytes(4, "big")
    + b"\x08\x02\x00\x00\x00"
)
PHOTO = {
    "contenu_base64": base64.b64encode(_IHDR).decode(),
    "largeur": 640,
    "hauteur": 480,
    "score_nettete": 90.0,
    "luminance_moyenne": 140.0,
}


# L'analyse d'image est quotée PAR APPAREIL, comme le parcours parcelle : sans
# en-tête, la route refuse. Le navigateur l'envoie sur tous ses appels.
ENTETES = {"X-Device-Id": "appareil-photo"}


def _evenements(resp) -> list[dict]:
    evts: list[dict] = []
    for bloc in resp.text.split("\n\n"):
        for ligne in bloc.splitlines():
            if ligne.startswith("data:"):
                evts.append(json.loads(ligne[len("data:") :].strip()))
    return evts


def test_le_premier_evenement_part_avant_toute_analyse(client) -> None:
    """Sans ce premier octet, le proxy coupe et la démonstration s'arrête."""
    resp = client.post(
        "/v1/chat/photo",
        json={"question": "Qu'est-ce que c'est sur ma cabosse ?", "images": [PHOTO]},
        headers=ENTETES,
    )

    assert resp.status_code == 200
    evts = _evenements(resp)
    assert evts[0]["type"] == "progress"
    assert evts[-1]["type"] == "done"


def test_la_photo_n_est_pas_un_passe_droit(client) -> None:
    """Un dosage reste refusé, image jointe ou non."""
    resp = client.post(
        "/v1/chat/photo",
        json={"question": "Quelle dose de fongicide appliquer ?", "images": [PHOTO]},
        headers=ENTETES,
    )

    textes = " ".join(e.get("text", "") for e in _evenements(resp))
    assert "ANADER" in textes


def test_sans_vision_aucune_description_n_est_inventee(client) -> None:
    """Le profil de test n'a pas de VLM : on doit le dire, pas le simuler."""
    resp = client.post(
        "/v1/chat/photo",
        json={"question": "Qu'est-ce que c'est sur ma cabosse ?", "images": [PHOTO]},
        headers=ENTETES,
    )

    textes = " ".join(e.get("text", "") for e in _evenements(resp))
    assert "ANADER" in textes
    assert "je vois" not in textes.lower()


def test_une_photo_floue_rend_un_conseil_de_reprise(client) -> None:
    """Le producteur doit savoir quoi refaire, pas recevoir une erreur."""
    floue = {**PHOTO, "score_nettete": 10.0}
    resp = client.post(
        "/v1/chat/photo",
        json={"question": "Qu'est-ce que c'est ?", "images": [floue]},
        headers=ENTETES,
    )

    textes = " ".join(e.get("text", "") for e in _evenements(resp))
    assert textes.strip()
    assert "je vois" not in textes.lower()


def test_le_chat_textuel_reste_inchange(client) -> None:
    """Contre-épreuve : la route texte ne doit pas exiger d'image."""
    resp = client.post(
        "/v1/chat",
        json={"question": "Comment bien sécher mes fèves de cacao ?", "canal": "web"},
    )

    assert resp.status_code == 200


def test_sans_identifiant_d_appareil_la_route_refuse(client) -> None:
    """Le quota d'analyses se compte par appareil : sans en-tête, il serait contournable
    en repartant de zéro à chaque requête."""
    resp = client.post(
        "/v1/chat/photo",
        json={"question": "Qu'est-ce que c'est ?", "images": [PHOTO]},
    )

    assert resp.status_code == 400
