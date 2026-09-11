"""Gardes de débit partagées par les routes qui déclenchent une analyse d'image.

Ces deux gardes vivaient dans ``routers/parcelles.py``, seule route à téléverser des
images. La photo dans le chat en ouvre une seconde : les recopier aurait laissé les
deux versions diverger, et c'est précisément le genre d'écart qui rouvre un trou —
une porte protégée, l'autre non.

Le budget d'analyse visuelle est compté **par appareil** et non par IP : derrière un
partage de connexion (cybercafé, point d'accès de coopérative), une IP porte plusieurs
producteurs légitimes. OWASP API4.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from app.domain.ports import CachePort

TROP_DE_REQUETES = "Trop de requêtes, veuillez réessayer dans une minute."

# Une génération de vision suivie d'une génération de conseil occupe l'inférence des
# dizaines de secondes : partager le budget d'un simple GET laisserait une poignée de
# requêtes la saturer.
CONSTATS_PAR_FENETRE = 3
CONSTATS_FENETRE_S = 60
TROP_D_ANALYSES = "Trop d'analyses d'images demandées. Patientez une minute avant la suivante."


async def garde_debit(cache: CachePort, client_ip: str) -> None:
    """Applique le rate-limit par IP.

    Args:
        cache: Port de cache portant le compteur de débit.
        client_ip: Adresse IP cliente déterminée par la dépendance dédiée.

    Raises:
        HTTPException: 429 si la limite est dépassée.
    """
    if await cache.hit_rate_limit(client_ip):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=TROP_DE_REQUETES)


async def garde_analyse(cache: CachePort, device_id: str) -> None:
    """Applique le quota d'analyses visuelles, par appareil.

    Args:
        cache: Port de cache portant les compteurs.
        device_id: Identifiant anonyme de l'appareil appelant.

    Raises:
        HTTPException: 429 si le quota d'analyses est dépassé.
    """
    if await cache.hit_quota(f"constat:{device_id}", CONSTATS_PAR_FENETRE, CONSTATS_FENETRE_S):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=TROP_D_ANALYSES)
