"""Client HTTP du modèle de vision local (API compatible OpenAI).

Le service ``vision/`` n'est **jamais exposé publiquement** : l'API le consomme en
interne, comme elle le fait déjà d'``inference/``. Les images lui sont passées en
``data:`` URI base64 — jamais une URL que le modèle irait chercher lui-même, ce qui
ouvrirait une porte SSRF et sortirait du périmètre souverain.

**Dégradation systématique** : toute panne (HTTP, réseau, réponse malformée) rend
``None``. L'appelant traduira en consigne d'indisponibilité ; rien n'est inventé.
"""

from __future__ import annotations

import base64

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Un constat descriptif est court : on borne la génération pour ne pas laisser le
# modèle divaguer, et pour tenir la latence sur une image de plantation.
MAX_TOKENS_DESCRIPTION = 220

# Température basse : on veut une description reproductible, pas de la créativité.
TEMPERATURE_DESCRIPTION = 0.2


class ClientVLM:
    """Adaptateur ``VisionPort`` vers un modèle de vision servi localement."""

    def __init__(self, base_url: str, modele: str, timeout_s: float = 60.0) -> None:
        """Initialise le client.

        Args:
            base_url: URL interne du service de vision.
            modele: Nom du modèle transmis à l'API.
            timeout_s: Délai maximal d'une requête, en secondes.
        """
        self._modele = modele
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s)

    @classmethod
    def from_settings(cls, settings: Settings) -> ClientVLM:
        """Construit le client à partir des paramètres applicatifs."""
        return cls(
            base_url=settings.vision_url,
            modele=settings.vision_modele,
            timeout_s=settings.vision_timeout_s,
        )

    @staticmethod
    def _en_data_uri(image: bytes) -> str:
        """Encode une image en ``data:`` URI base64."""
        return "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")

    def _corps(self, images: tuple[bytes, ...], consigne: str) -> dict[str, object]:
        """Construit le corps de requête compatible OpenAI (contenu multimodal)."""
        contenu: list[dict[str, object]] = [{"type": "text", "text": consigne}]
        contenu += [
            {"type": "image_url", "image_url": {"url": self._en_data_uri(image)}}
            for image in images
        ]
        return {
            "model": self._modele,
            "messages": [{"role": "user", "content": contenu}],
            "max_tokens": MAX_TOKENS_DESCRIPTION,
            "temperature": TEMPERATURE_DESCRIPTION,
        }

    async def decrire(self, images: tuple[bytes, ...], consigne: str) -> str | None:
        """Décrit les images, ou retourne ``None`` si la vision est indisponible.

        Args:
            images: Octets des images à décrire (JPEG ou PNG).
            consigne: Consigne stricte encadrant la description.

        Returns:
            Le texte descriptif, ou ``None`` en cas d'absence d'image ou de panne.
        """
        if not images:
            return None
        try:
            reponse = await self._client.post(
                "/v1/chat/completions", json=self._corps(images, consigne)
            )
            reponse.raise_for_status()
            charge = reponse.json()
            texte = charge["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("vision_indisponible", error=str(exc))
            return None
        texte = str(texte).strip()
        return texte or None

    async def disponible(self) -> bool:
        """Sonde la disponibilité du service de vision."""
        try:
            reponse = await self._client.get("/v1/models")
            return reponse.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        """Ferme le client HTTP."""
        await self._client.aclose()
