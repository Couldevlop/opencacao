"""La photo dans le chat : ce que le service doit garantir, tour par tour.

Le contrat est celui, déjà validé, du constat de parcelle — **décrire, jamais nommer
une maladie, jamais prescrire**. On ouvre une porte d'entrée supplémentaire ; les
garde-fous de sortie restent les mêmes, et ces tests le vérifient plutôt que de le
supposer.
"""

from __future__ import annotations

import base64

import pytest

from app.application.constat_chat import ServiceConstatChat
from app.models.parcelle import ImageRequest

# En-tête PNG minimal valide : 8 octets de signature + IHDR annonçant 640×480.
_IHDR = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\r"
    + b"IHDR"
    + (640).to_bytes(4, "big")
    + (480).to_bytes(4, "big")
    + b"\x08\x02\x00\x00\x00"
)
IMAGE_NETTE = ImageRequest(
    contenu_base64=base64.b64encode(_IHDR).decode(),
    largeur=640,
    hauteur=480,
    score_nettete=90.0,
    luminance_moyenne=140.0,
)
IMAGE_FLOUE = IMAGE_NETTE.model_copy(update={"score_nettete": 10.0})


class ConstatFactice:
    """Double du service de constat visuel : on teste l'aiguillage, pas le modèle."""

    def __init__(self, texte: str | None) -> None:
        self._texte = texte
        self.appels: list[int] = []
        self.dernier_contexte = None

    async def analyser(self, images, contexte):  # noqa: ANN001, ANN201
        self.appels.append(len(images))
        self.dernier_contexte = contexte
        if self._texte is None:
            return None
        return type(
            "ConstatFactice",
            (),
            {"texte": self._texte, "confiance": "moyenne", "facteurs_contexte": ()},
        )()


@pytest.mark.asyncio
async def test_une_photo_nette_produit_un_constat() -> None:
    visuel = ConstatFactice("Je vois des taches brunes sur une cabosse.")
    service = ServiceConstatChat(visuel)

    resultat = await service.analyser("Qu'est-ce que c'est ?", [IMAGE_NETTE], "")

    assert "taches brunes" in resultat.texte
    assert visuel.appels == [1]


@pytest.mark.asyncio
async def test_une_photo_floue_rend_un_conseil_de_reprise_pas_une_erreur() -> None:
    """Un producteur en plantation renverrait sinon cinq fois la même photo floue."""
    visuel = ConstatFactice("jamais appelé")
    service = ServiceConstatChat(visuel)

    resultat = await service.analyser("Qu'est-ce que c'est ?", [IMAGE_FLOUE], "")

    assert visuel.appels == []
    assert resultat.conseil_reprise
    assert not resultat.texte


@pytest.mark.asyncio
async def test_la_vision_indisponible_ne_fabrique_aucune_description() -> None:
    """Souveraineté : pas de modèle, pas de constat — jamais d'invention."""
    visuel = ConstatFactice(None)
    service = ServiceConstatChat(visuel)

    resultat = await service.analyser("Qu'est-ce que c'est ?", [IMAGE_NETTE], "")

    assert resultat.indisponible
    assert "ANADER" in resultat.texte
    # Aucune description : le texte servi est une consigne, pas un constat inventé.
    assert "je vois" not in resultat.texte.lower()
    # Et il ne promet pas un rattachement de parcelle qui n'existe pas dans le chat.
    assert "parcelle" not in resultat.texte.lower()


@pytest.mark.asyncio
async def test_les_garde_fous_metier_passent_AVANT_la_vision() -> None:
    """Une photo ne doit jamais devenir un passe-droit : la question est évaluée."""
    visuel = ConstatFactice("jamais appelé")
    service = ServiceConstatChat(visuel)

    resultat = await service.analyser("Quelle dose de fongicide appliquer ?", [IMAGE_NETTE], "")

    assert visuel.appels == []
    assert resultat.refus
    assert "ANADER" in resultat.texte


@pytest.mark.asyncio
async def test_le_mot_photo_ne_declenche_plus_le_refus_quand_l_image_est_la() -> None:
    """C'est tout l'objet du chantier : la règle lexicale ne doit pas fermer la porte
    qu'on vient d'ouvrir. La protection est reportée sur la sortie de la cascade."""
    visuel = ConstatFactice("Je vois un jaunissement du limbe.")
    service = ServiceConstatChat(visuel)

    resultat = await service.analyser(
        "Regarde la photo de ma cabosse et dis-moi la maladie.", [IMAGE_NETTE], ""
    )

    assert not resultat.refus
    assert "jaunissement" in resultat.texte


@pytest.mark.asyncio
async def test_la_localite_du_fil_est_transmise_au_contexte() -> None:
    """Le constat vaut par son contexte : la ville déjà citée ne se redemande pas."""
    visuel = ConstatFactice("Je vois des taches.")
    service = ServiceConstatChat(visuel)

    await service.analyser("Qu'est-ce que c'est ?", [IMAGE_NETTE], "je suis à Soubré")

    assert visuel.dernier_contexte is not None
    assert visuel.dernier_contexte.localite == "Soubré"


@pytest.mark.asyncio
async def test_sans_image_le_service_ne_promet_rien() -> None:
    visuel = ConstatFactice("jamais appelé")
    service = ServiceConstatChat(visuel)

    resultat = await service.analyser("Qu'est-ce que c'est ?", [], "")

    assert visuel.appels == []
    assert resultat.indisponible


@pytest.mark.asyncio
async def test_une_photo_illisible_ne_leve_pas_la_regle_d_image() -> None:
    """Relevé en revue de sécurité : annoncer « une image est analysée » avant de le
    savoir lèverait le garde-fou sur la foi d'un fichier illisible. Sans conséquence
    ici, mais une affirmation fausse dans un garde-fou finit par devenir un trou."""
    visuel = ConstatFactice("jamais appelé")
    service = ServiceConstatChat(visuel)

    resultat = await service.analyser("Regarde la photo et dis-moi la maladie.", [IMAGE_FLOUE], "")

    assert visuel.appels == []
    assert resultat.refus
    assert "ANADER" in resultat.texte


@pytest.mark.asyncio
async def test_une_photo_trop_lourde_est_refusee_avant_le_modele() -> None:
    """Le champ base64 n'est pas borné par le schéma : la porte du chat doit refuser
    comme le fait déjà le dépôt de capture."""
    from app.services.vision import recevabilite

    enorme = IMAGE_NETTE.model_copy(
        update={
            "contenu_base64": base64.b64encode(
                _IHDR + b"\x00" * (recevabilite.TAILLE_MAX_OCTETS + 1)
            ).decode()
        }
    )
    visuel = ConstatFactice("jamais appelé")
    service = ServiceConstatChat(visuel)

    resultat = await service.analyser("Qu'est-ce que c'est ?", [enorme], "")

    assert visuel.appels == []
    assert resultat.conseil_reprise
