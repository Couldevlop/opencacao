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
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# Emplacements standard des informations du ServiceAccount monté dans le pod.
_SA = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_HOTE_DEFAUT = "https://kubernetes.default.svc"

# Un nom d'objet Kubernetes est un label DNS-1123 : alphanumérique minuscule et tirets,
# 253 caractères au plus. On le vérifie AVANT de l'insérer dans un chemin d'API.
# Défense en profondeur : les noms viennent de variables d'environnement, et une valeur
# empoisonnée (« ../secrets/opencacao-auth ») fabriquerait sinon une requête vers une
# ressource que le RBAC n'a jamais eu l'intention d'exposer. Le RBAC refuserait
# probablement — « probablement » n'est pas un contrôle de sécurité.
_NOM_VALIDE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")


class ClusterIndisponible(Exception):
    """Levée si le redémarrage du déploiement échoue (RBAC, réseau, etc.)."""


def valider_nom(nom: str) -> str:
    """Vérifie qu'un nom de ressource est un label DNS-1123, et le renvoie.

    Args:
        nom: Nom d'objet Kubernetes à insérer dans un chemin d'API.

    Returns:
        Le nom, inchangé, s'il est valide.

    Raises:
        ClusterIndisponible: Si le nom pourrait fabriquer un chemin d'API arbitraire.
    """
    if not nom or len(nom) > 253 or not _NOM_VALIDE.match(nom):
        logger.error("nom_de_ressource_refuse", longueur=len(nom))
        raise ClusterIndisponible("nom de ressource invalide")
    return nom


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

    @property
    def namespace(self) -> str:
        """Namespace courant (celui du ServiceAccount monté)."""
        return self._namespace

    async def get_json(self, chemin: str) -> dict:
        """Lit une ressource de l'API server (GET) et renvoie le JSON décodé.

        Args:
            chemin: Chemin d'API absolu (ex.
                ``/apis/batch/v1/namespaces/opencacao/cronjobs/enrichissement-rag``).

        Returns:
            Le corps JSON de la ressource.

        Raises:
            ClusterIndisponible: Si l'API server refuse ou est injoignable.
        """
        try:
            reponse = await self._http().get(
                f"{self._hote}{chemin}",
                headers={"Authorization": f"Bearer {self._token}"},
            )
            reponse.raise_for_status()
            return reponse.json()
        except httpx.HTTPError as exc:
            logger.error("get_json_echec", chemin=chemin, error=str(exc))
            raise ClusterIndisponible(f"lecture de {chemin} impossible") from exc

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
        deployment = valider_nom(deployment)
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

    async def lire_configmap(self, nom: str) -> dict[str, str]:
        """Lit la section ``data`` d'une ConfigMap.

        Args:
            nom: Nom de la ConfigMap (ex. ``"api-config"``).

        Returns:
            Les clés de la ConfigMap ; dictionnaire vide si elle n'en porte aucune.

        Raises:
            ClusterIndisponible: Si l'API server refuse ou est injoignable.
        """
        nom = valider_nom(nom)
        objet = await self.get_json(f"/api/v1/namespaces/{self._namespace}/configmaps/{nom}")
        return objet.get("data", {})

    async def _patch(self, url: str, patch: dict, quoi: str) -> None:
        """Applique un patch de FUSION et lève une exception claire en cas de refus.

        Le type ``merge-patch+json`` est délibéré : il ne touche QUE les clés fournies.
        Un remplacement effacerait les quarante autres clés de la ConfigMap — dont
        ``AUTH_EMAIL_FROM``, qu'un incident de juillet a déjà appris à ne pas perdre.

        Args:
            url: URL absolue de la ressource.
            patch: Corps du patch.
            quoi: Description courte, pour le message d'erreur et les journaux.

        Raises:
            ClusterIndisponible: Si l'API server refuse ou est injoignable.
        """
        try:
            reponse = await self._http().patch(
                url,
                content=json.dumps(patch),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/merge-patch+json",
                },
            )
            reponse.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("patch_echec", quoi=quoi, error=str(exc))
            raise ClusterIndisponible(f"{quoi} impossible") from exc
        logger.info("patch_applique", quoi=quoi)

    async def patch_configmap(self, nom: str, data: dict[str, str]) -> None:
        """Fusionne des clés dans une ConfigMap, sans toucher aux autres.

        Args:
            nom: Nom de la ConfigMap.
            data: Clés à écrire ou remplacer.

        Raises:
            ClusterIndisponible: Si l'API server refuse ou est injoignable.
        """
        nom = valider_nom(nom)
        await self._patch(
            f"{self._hote}/api/v1/namespaces/{self._namespace}/configmaps/{nom}",
            {"data": data},
            f"patch de la ConfigMap {nom}",
        )

    async def lire_cronjob(self, nom: str) -> dict:
        """Lit un travail planifié.

        Args:
            nom: Nom du CronJob (validé comme label DNS-1123).

        Returns:
            Le corps JSON de la ressource.

        Raises:
            ValueError: Si le nom n'est pas un label valide.
            ClusterIndisponible: Si l'API server refuse ou est injoignable.
        """
        nom = valider_nom(nom)
        return await self.get_json(f"/apis/batch/v1/namespaces/{self._namespace}/cronjobs/{nom}")

    async def patch_cronjob(self, nom: str, patch: dict) -> None:
        """Fusionne un patch dans un travail planifié (suspension, horaire).

        Args:
            nom: Nom du CronJob (validé comme label DNS-1123).
            patch: Fragment à fusionner, p. ex. ``{"spec": {"suspend": True}}``.

        Raises:
            ValueError: Si le nom n'est pas un label valide.
            ClusterIndisponible: Si l'API server refuse ou est injoignable.
        """
        nom = valider_nom(nom)
        await self._patch(
            f"{self._hote}/apis/batch/v1/namespaces/{self._namespace}/cronjobs/{nom}",
            patch,
            f"patch du CronJob {nom}",
        )

    async def mettre_a_l_echelle(self, deployment: str, repliques: int) -> None:
        """Fixe le nombre de répliques d'un déploiement.

        Args:
            deployment: Nom du déploiement.
            repliques: Nombre de répliques voulu.

        Raises:
            ClusterIndisponible: Si l'API server refuse ou est injoignable.
        """
        deployment = valider_nom(deployment)
        await self._patch(
            f"{self._hote}/apis/apps/v1/namespaces/{self._namespace}/deployments/{deployment}",
            {"spec": {"replicas": repliques}},
            f"mise à l'échelle de {deployment} à {repliques}",
        )
