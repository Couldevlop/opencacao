"""Client minimal vers l'API server Kubernetes (redémarrage progressif d'un déploiement).

La console doit pouvoir relancer l'API après reconstruction de l'index RAG, pour
que le nouveau modèle d'index soit chargé — sans coupure (rolling restart). On
parle directement à l'API server in-cluster avec le jeton du ServiceAccount monté,
ce qui évite toute dépendance supplémentaire (``httpx`` est déjà présent).

Le redémarrage est un *patch* de l'annotation
``kubectl.kubernetes.io/restartedAt`` du template de pods — exactement ce que fait
``kubectl rollout restart``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# Emplacements standard des informations du ServiceAccount monté dans le pod.
_SA = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_HOTE_DEFAUT = "https://kubernetes.default.svc"


class ClusterIndisponible(Exception):
    """Levée si le redémarrage du déploiement échoue (RBAC, réseau, etc.)."""


class ClusterClient:
    """Patch un déploiement via l'API server, authentifié par ServiceAccount."""

    def __init__(
        self,
        hote: str,
        namespace: str,
        token: str,
        verify: str | bool,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialise le client.

        Args:
            hote: URL de base de l'API server.
            namespace: Namespace des déploiements à patcher.
            token: Jeton bearer du ServiceAccount.
            verify: Chemin du CA (ou booléen) pour la vérification TLS.
            client: Client httpx injectable (tests).
        """
        self._hote = hote.rstrip("/")
        self._namespace = namespace
        self._token = token
        self._verify = verify
        # Création paresseuse : on ne charge le CA qu'au premier appel réel.
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        """Retourne le client HTTP, en le créant à la demande."""
        if self._client is None:
            self._client = httpx.AsyncClient(verify=self._verify, timeout=15.0)
        return self._client

    @classmethod
    def from_serviceaccount(cls, sa: Path = _SA, hote: str = _HOTE_DEFAUT) -> ClusterClient:
        """Construit le client depuis le ServiceAccount monté dans le pod.

        Args:
            sa: Répertoire du ServiceAccount monté.
            hote: URL de l'API server.

        Returns:
            Un client prêt à patcher des déploiements.

        Raises:
            ClusterIndisponible: Si le jeton du ServiceAccount est absent.
        """
        jeton = sa / "token"
        if not jeton.exists():
            raise ClusterIndisponible("jeton ServiceAccount absent (hors cluster ?)")
        namespace = (sa / "namespace").read_text(encoding="utf-8").strip()
        ca = sa / "ca.crt"
        return cls(
            hote=hote,
            namespace=namespace,
            token=jeton.read_text(encoding="utf-8").strip(),
            verify=str(ca) if ca.exists() else True,
        )

    async def rollout_restart(self, deployment: str) -> None:
        """Déclenche un redémarrage progressif du déploiement (rolling restart).

        Args:
            deployment: Nom du déploiement à relancer (ex. ``"api"``).

        Raises:
            ClusterIndisponible: Si l'API server refuse ou est injoignable.
        """
        url = f"{self._hote}/apis/apps/v1/namespaces/{self._namespace}" f"/deployments/{deployment}"
        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": datetime.now(UTC).isoformat()
                        }
                    }
                }
            }
        }
        try:
            reponse = await self._http().patch(
                url,
                content=json.dumps(patch),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/strategic-merge-patch+json",
                },
            )
            reponse.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("rollout_restart_echec", deployment=deployment, error=str(exc))
            raise ClusterIndisponible(f"redémarrage de {deployment} impossible") from exc
        logger.info("rollout_restart", deployment=deployment)

    async def close(self) -> None:
        """Ferme le client HTTP sous-jacent (s'il a été créé)."""
        if self._client is not None:
            await self._client.aclose()
