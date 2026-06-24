"""Cas d'usage : authentification par lien magique (D2).

Orchestration pure au-dessus des ports (dépôt d'auth + notifier). Génère un jeton à
usage unique, l'enregistre haché, fabrique le lien et le fait acheminer ; puis vérifie
un jeton présenté et renvoie l'identité de compte (créée à la volée la première fois).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from app.core.auth_store import hacher_token
from app.core.logging import get_logger
from app.domain.ports import AuthStorePort, LienNotifierPort
from app.models.auth import IdentiteResponse

logger = get_logger(__name__)


class AuthService:
    """Cas d'usage central de l'authentification par lien magique."""

    def __init__(
        self,
        store: AuthStorePort,
        notifier: LienNotifierPort,
        ttl_minutes: int = 20,
    ) -> None:
        """Initialise le service.

        Args:
            store: Dépôt durable des comptes et liens.
            notifier: Canal d'acheminement du lien (console ou SMTP).
            ttl_minutes: Durée de validité d'un lien magique.
        """
        self._store = store
        self._notifier = notifier
        self._ttl = ttl_minutes

    async def demander_lien(self, email: str, base_url: str) -> None:
        """Génère un lien magique pour l'email et le fait acheminer.

        Ne révèle jamais si l'email est « connu » (anti-énumération) : le routeur
        répond toujours 202. Le jeton est stocké haché ; seul le lien (avec le jeton
        en clair) part vers l'utilisateur.

        Args:
            email: Email destinataire (déjà validé/normalisé).
            base_url: Origine publique pour fabriquer le lien (sans slash final).
        """
        token = secrets.token_urlsafe(32)
        expire_le = datetime.now(UTC) + timedelta(minutes=self._ttl)
        await self._store.creer_lien(email, hacher_token(token), expire_le)
        lien = f"{base_url.rstrip('/')}/?auth={token}"
        await self._notifier.envoyer_lien(email, lien)
        logger.info("lien_magique_demande", email=email)

    async def verifier(self, token: str) -> IdentiteResponse | None:
        """Vérifie un jeton et renvoie l'identité de compte, ou None si invalide.

        Le lien est consommé (usage unique). À la première vérification réussie d'un
        email, un identifiant de compte opaque et stable est créé.
        """
        email = await self._store.consommer_lien(hacher_token(token))
        if email is None:
            return None
        account_id = await self._store.compte_pour(email)
        logger.info("connexion_reussie", email=email)
        return IdentiteResponse(account_id=account_id, email=email)
