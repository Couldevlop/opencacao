"""Fenêtre GPU : fermeture nocturne, réveil du matin, rappel d'oubli.

Le cluster ne peut pas DÉMARRER le pod loué (aucune clé RunPod) : il ne peut que
l'adopter dès qu'il répond. Toute la valeur tient donc dans ce qu'on refuse de faire —
ne jamais basculer vers un tunnel muet, ne jamais éteindre ce qui répond encore, et ne
jamais envoyer un email qui n'apprend rien.
"""

from __future__ import annotations

import pytest

from app.exploitation import fenetre

TUNNEL = "http://100.75.130.125:8000"


class _Cluster:
    """Cluster factice : enregistre les effets, dans l'ordre où ils surviennent."""

    def __init__(self, donnees: dict[str, str] | None = None) -> None:
        self.donnees = donnees or {}
        self.patchs: list[dict[str, str]] = []
        self.echelles: list[tuple[str, int]] = []
        self.restarts: list[str] = []

    async def lire_configmap(self, nom: str) -> dict[str, str]:
        return dict(self.donnees)

    async def patch_configmap(self, nom: str, data: dict[str, str]) -> None:
        self.patchs.append(dict(data))
        self.donnees.update(data)

    async def mettre_a_l_echelle(self, deploiement: str, repliques: int) -> None:
        self.echelles.append((deploiement, repliques))

    async def rollout_restart(self, deploiement: str) -> None:
        self.restarts.append(deploiement)


class _Http:
    """Client HTTP factice : réponses programmées par préfixe d'URL."""

    def __init__(self, reponses: dict[str, int] | None = None) -> None:
        self.reponses = reponses or {}
        self.appels: list[str] = []

    async def get(self, url: str, headers: dict | None = None) -> object:
        self.appels.append(url)
        for prefixe, code in self.reponses.items():
            if url.startswith(prefixe):
                return _Reponse(code)
        raise RuntimeError("injoignable")


class _Reponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def json(self) -> dict:
        return {"inference": self.status_code == 200}


class _Alertes:
    """Collecte les alertes émises (sujet, texte)."""

    def __init__(self) -> None:
        self.envoyees: list[tuple[str, str]] = []

    async def __call__(self, sujet: str, texte: str) -> bool:
        self.envoyees.append((sujet, texte))
        return True


async def _sans_attendre(_duree: float) -> None:
    """Remplace l'attente entre deux sondes : les tests ne dorment pas."""
    return None


def _reglages() -> fenetre.Reglages:
    return fenetre.Reglages(
        configmap="api-config",
        deploiement_cpu="inference",
        deploiement_api="api",
        url_interne="http://inference:8000",
        base_api="http://api:8080",
        hote="opencacao.openlabconsulting.com",
        timeout_s=10,
        cout_gpu_usd_h=0.49,
        lien_console="https://console.runpod.io",
        capacites_jour={"VISION_ENABLED": "true", "RAPPORTS_ENABLED": "true"},
    )


# --- Fermeture nocturne ---


@pytest.mark.asyncio
async def test_la_fermeture_memorise_le_tunnel_avant_de_l_effacer() -> None:
    """Sans cette sauvegarde, on ne saurait plus le matin où était le GPU."""
    cluster = _Cluster({"PROFIL_MATERIEL": "gpu", "INFERENCE_URL": TUNNEL})
    http = _Http({"http://api:8080": 200})
    await fenetre.fermer(cluster, http, _reglages(), _Alertes())
    assert cluster.patchs[0][fenetre.CLE_URL_GPU] == TUNNEL
    # ...et l'effacement ne vient qu'APRÈS.
    assert cluster.patchs[1]["INFERENCE_URL"] == "http://inference:8000"


@pytest.mark.asyncio
async def test_la_fermeture_ramene_le_service_sur_cpu() -> None:
    """Profil, délestage et redémarrage : le repli complet de la sentinelle."""
    cluster = _Cluster({"PROFIL_MATERIEL": "gpu", "INFERENCE_URL": TUNNEL})
    await fenetre.fermer(cluster, _Http({"http://api:8080": 200}), _reglages(), _Alertes())
    assert cluster.donnees["PROFIL_MATERIEL"] == "cpu"
    assert cluster.donnees["VISION_ENABLED"] == "false"
    assert ("inference", 1) in cluster.echelles
    assert cluster.restarts == ["api"]


@pytest.mark.asyncio
async def test_la_fermeture_previent_qu_il_reste_un_pod_a_arreter() -> None:
    """L'email est le seul levier d'économie : sans clé, c'est un humain qui clique."""
    alertes = _Alertes()
    cluster = _Cluster({"PROFIL_MATERIEL": "gpu", "INFERENCE_URL": TUNNEL})
    await fenetre.fermer(cluster, _Http({"http://api:8080": 200}), _reglages(), alertes)
    assert len(alertes.envoyees) == 1
    assert "console.runpod.io" in alertes.envoyees[0][1]


@pytest.mark.asyncio
async def test_la_fermeture_ne_fait_rien_si_on_est_deja_sur_cpu() -> None:
    """Rejouée, elle ne doit ni patcher, ni redémarrer, ni écrire un email de trop."""
    alertes = _Alertes()
    cluster = _Cluster({"PROFIL_MATERIEL": "cpu", "INFERENCE_URL": "http://inference:8000"})
    await fenetre.fermer(cluster, _Http({"http://api:8080": 200}), _reglages(), alertes)
    assert cluster.patchs == []
    assert cluster.restarts == []
    assert alertes.envoyees == []


@pytest.mark.asyncio
async def test_la_fermeture_alerte_si_le_service_ne_repond_plus_apres_le_repli() -> None:
    """Un repli qui laisse le service muet doit se dire, pas se taire jusqu'au matin."""
    alertes = _Alertes()
    cluster = _Cluster({"PROFIL_MATERIEL": "gpu", "INFERENCE_URL": TUNNEL})
    # `dormir` neutralisé : une suite de tests ne doit jamais attendre pour de vrai.
    await fenetre.fermer(cluster, _Http({}), _reglages(), alertes, _sans_attendre)
    assert any("CPU" in sujet or "cpu" in sujet for sujet, _ in alertes.envoyees)
    assert len(alertes.envoyees) == 1


# --- Réveil du matin ---


@pytest.mark.asyncio
async def test_le_reveil_bascule_sur_gpu_des_que_le_tunnel_repond() -> None:
    """C'est tout ce que le cluster peut faire : adopter un pod qu'un humain a démarré."""
    cluster = _Cluster({"PROFIL_MATERIEL": "cpu", fenetre.CLE_URL_GPU: TUNNEL})
    http = _Http({TUNNEL: 200, "http://api:8080": 200})
    await fenetre.ouvrir(cluster, http, _reglages(), _Alertes())
    assert cluster.donnees["PROFIL_MATERIEL"] == "gpu"
    assert cluster.donnees["INFERENCE_URL"] == TUNNEL
    assert cluster.restarts == ["api"]


@pytest.mark.asyncio
async def test_le_reveil_restaure_les_fonctions_delestees_la_nuit() -> None:
    """Sans cela, vision et rapports resteraient éteints à jamais dès la 1re nuit."""
    cluster = _Cluster({"PROFIL_MATERIEL": "cpu", fenetre.CLE_URL_GPU: TUNNEL})
    http = _Http({TUNNEL: 200, "http://api:8080": 200})
    await fenetre.ouvrir(cluster, http, _reglages(), _Alertes())
    assert cluster.donnees["VISION_ENABLED"] == "true"
    assert cluster.donnees["RAPPORTS_ENABLED"] == "true"
    assert cluster.donnees["REPLI_CPU"] == "false"


@pytest.mark.asyncio
async def test_le_reveil_ne_bascule_JAMAIS_vers_un_tunnel_muet() -> None:
    """Le test qui compte : basculer vers une adresse morte rendrait le service muet."""
    alertes = _Alertes()
    cluster = _Cluster({"PROFIL_MATERIEL": "cpu", fenetre.CLE_URL_GPU: TUNNEL})
    await fenetre.ouvrir(cluster, _Http({"http://api:8080": 200}), _reglages(), alertes)
    assert cluster.patchs == []  # l'effet de bord, pas le message
    assert cluster.restarts == []
    assert len(alertes.envoyees) == 1


@pytest.mark.asyncio
async def test_le_reveil_se_tait_quand_on_est_deja_sur_gpu() -> None:
    """Il tourne toutes les dix minutes : un email par passage serait vite ignoré."""
    alertes = _Alertes()
    cluster = _Cluster({"PROFIL_MATERIEL": "gpu", "INFERENCE_URL": TUNNEL})
    await fenetre.ouvrir(cluster, _Http({TUNNEL: 200}), _reglages(), alertes)
    assert cluster.patchs == []
    assert alertes.envoyees == []


@pytest.mark.asyncio
async def test_le_reveil_sans_adresse_memorisee_le_dit_au_lieu_de_deviner() -> None:
    """Aucune URL de tunnel connue : on n'invente pas d'adresse, on prévient."""
    alertes = _Alertes()
    cluster = _Cluster({"PROFIL_MATERIEL": "cpu"})
    await fenetre.ouvrir(cluster, _Http({}), _reglages(), alertes)
    assert cluster.patchs == []
    assert len(alertes.envoyees) == 1


# --- Rappel : le pod oublié ---


@pytest.mark.asyncio
async def test_le_rappel_signale_un_pod_encore_allume_et_son_cout() -> None:
    """Oublier une nuit coûte quelques dollars ; oublier une semaine en coûte cinquante."""
    alertes = _Alertes()
    cluster = _Cluster({"PROFIL_MATERIEL": "cpu", fenetre.CLE_URL_GPU: TUNNEL})
    await fenetre.rappeler(cluster, _Http({TUNNEL: 200}), _reglages(), alertes)
    assert len(alertes.envoyees) == 1
    assert "0.49" in alertes.envoyees[0][1] or "0,49" in alertes.envoyees[0][1]


@pytest.mark.asyncio
async def test_le_rappel_reste_muet_quand_le_pod_est_bien_eteint() -> None:
    """Le silence est la récompense : un rappel systématique cesserait d'être lu."""
    alertes = _Alertes()
    cluster = _Cluster({"PROFIL_MATERIEL": "cpu", fenetre.CLE_URL_GPU: TUNNEL})
    await fenetre.rappeler(cluster, _Http({}), _reglages(), alertes)
    assert alertes.envoyees == []


@pytest.mark.asyncio
async def test_le_rappel_se_tait_aussi_quand_le_service_tourne_sur_gpu() -> None:
    """Le pod répond, mais c'est normal : la fenêtre GPU est ouverte."""
    alertes = _Alertes()
    cluster = _Cluster({"PROFIL_MATERIEL": "gpu", "INFERENCE_URL": TUNNEL})
    await fenetre.rappeler(cluster, _Http({TUNNEL: 200}), _reglages(), alertes)
    assert alertes.envoyees == []


# --- Réglages et point d'entrée ---


def test_les_reglages_viennent_de_l_environnement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le CronJob règle le coût horaire et le lien console sans reconstruire l'image."""
    monkeypatch.setenv("COUT_GPU_USD_H", "0.27")
    monkeypatch.setenv("LIEN_CONSOLE", "https://console.runpod.io/pods/abc")
    monkeypatch.setenv("JOUR_VISION_ENABLED", "false")
    reglages = fenetre.reglages_depuis_env()
    assert reglages.cout_gpu_usd_h == 0.27
    assert reglages.lien_console.endswith("/abc")
    # Ce que la nuit délestera, le matin ne le rallume que si on le lui demande.
    assert reglages.capacites_jour["VISION_ENABLED"] == "false"
    assert reglages.capacites_jour["RAPPORTS_ENABLED"] == "true"


@pytest.mark.asyncio
async def test_un_verbe_inconnu_ne_touche_a_rien() -> None:
    """Une faute de frappe dans le manifeste ne doit jamais basculer le service."""
    assert await fenetre.main("ferme") is False


@pytest.mark.asyncio
async def test_hors_cluster_le_verbe_refuse_au_lieu_d_agir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lancé sur un poste de développement, il s'arrête au lieu de deviner un cluster."""
    from app.curation.k8s import ClusterClient, ClusterIndisponible

    def absent(cls):
        raise ClusterIndisponible("jeton ServiceAccount absent")

    monkeypatch.setattr(ClusterClient, "from_serviceaccount", classmethod(absent))
    assert await fenetre.main("fermer") is False


@pytest.mark.asyncio
async def test_le_verbe_execute_puis_ferme_ses_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un CronJob laisse un pod derrière lui : rien ne doit rester ouvert."""
    from app.curation.k8s import ClusterClient

    ferme: list[str] = []

    class _ClusterFerme(_Cluster):
        async def close(self) -> None:
            ferme.append("cluster")

    monkeypatch.setattr(
        ClusterClient, "from_serviceaccount", classmethod(lambda cls: _ClusterFerme())
    )
    assert await fenetre.main("rappeler") is True
    assert ferme == ["cluster"]


# --- Anti-matraquage : le réveil répété doit rester silencieux ---


@pytest.mark.asyncio
async def test_le_reveil_repete_ne_previent_pas_du_pod_absent() -> None:
    """48 passages par jour : seul le premier annonce, les autres se taisent."""
    alertes = _Alertes()
    reglages = fenetre.Reglages(**{**_reglages().__dict__, "prevenir_si_absent": False})
    cluster = _Cluster({"PROFIL_MATERIEL": "cpu", fenetre.CLE_URL_GPU: TUNNEL})
    await fenetre.ouvrir(cluster, _Http({}), reglages, alertes)
    assert alertes.envoyees == []
    assert cluster.patchs == []  # et il ne bascule toujours pas


@pytest.mark.asyncio
async def test_le_reveil_repete_reste_muet_sans_adresse_memorisee() -> None:
    """Même silence quand aucune adresse n'est connue : une annonce a suffi."""
    alertes = _Alertes()
    reglages = fenetre.Reglages(**{**_reglages().__dict__, "prevenir_si_absent": False})
    await fenetre.ouvrir(_Cluster({"PROFIL_MATERIEL": "cpu"}), _Http({}), reglages, alertes)
    assert alertes.envoyees == []


def test_l_annonce_unique_est_reglable_par_l_environnement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deux CronJobs, un seul bavard : c'est cette variable qui les distingue."""
    monkeypatch.setenv("PREVENIR_SI_ABSENT", "false")
    assert fenetre.reglages_depuis_env().prevenir_si_absent is False


# --- Doctrine D1 : l'inférence n'est jamais exposée publiquement ---


@pytest.mark.parametrize(
    "url",
    ["https://abc123-8000.proxy.runpod.net", "https://truc.ngrok.io", "http://x.ngrok-free.app"],
)
@pytest.mark.asyncio
async def test_le_reveil_refuse_de_restaurer_un_point_de_terminaison_public(url: str) -> None:
    """`profil.sh` refuse déjà ces adresses ; une automatisation ne doit pas les rouvrir.

    Sans ce garde, il aurait suffi qu'une adresse publique ait été forcée une fois pour
    que le réveil la remette en service chaque matin, en silence.
    """
    alertes = _Alertes()
    cluster = _Cluster({"PROFIL_MATERIEL": "cpu", fenetre.CLE_URL_GPU: url})
    await fenetre.ouvrir(cluster, _Http({url: 200}), _reglages(), alertes)
    assert cluster.patchs == []  # l'effet de bord : rien n'a basculé
    assert len(alertes.envoyees) == 1
    assert "D1" in alertes.envoyees[0][1]


@pytest.mark.asyncio
async def test_une_adresse_privee_de_tunnel_reste_acceptee() -> None:
    """Le garde ne doit pas fermer la porte au cas nominal."""
    cluster = _Cluster({"PROFIL_MATERIEL": "cpu", fenetre.CLE_URL_GPU: TUNNEL})
    await fenetre.ouvrir(
        cluster, _Http({TUNNEL: 200, "http://api:8080": 200}), _reglages(), _Alertes()
    )
    assert cluster.donnees["PROFIL_MATERIEL"] == "gpu"


# --- Faux négatif du 19/08 : sonder pendant que l'API redémarre ---


class _HttpProgressif:
    """Injoignable pendant N tentatives, puis répond — comme une API qui redémarre."""

    def __init__(self, echecs: int) -> None:
        self.restants = echecs
        self.sondes = 0

    async def get(self, url: str, headers: dict | None = None) -> object:
        self.sondes += 1
        if self.restants > 0:
            self.restants -= 1
            raise RuntimeError("connexion refusée")
        return _Reponse(200)


@pytest.mark.asyncio
async def test_la_fermeture_attend_que_l_api_ait_fini_de_redemarrer() -> None:
    """Vécu en production le 19/08 : la sonde tombait pendant le redémarrage.

    La fermeture redémarre l'API puis vérifie ``/v1/ready``. Sondée une seule fois,
    quinze secondes après, elle concluait que le service était muet et envoyait
    l'alerte — alors que le repli s'était parfaitement déroulé.
    """
    alertes = _Alertes()
    sommeils: list[float] = []

    async def dormir(duree: float) -> None:
        sommeils.append(duree)

    http = _HttpProgressif(echecs=3)
    cluster = _Cluster({"PROFIL_MATERIEL": "gpu", "INFERENCE_URL": TUNNEL})
    await fenetre.fermer(cluster, http, _reglages(), alertes, dormir)

    assert sommeils, "la fermeture doit patienter entre deux sondes"
    assert len(alertes.envoyees) == 1
    assert "ne répond pas" not in alertes.envoyees[0][0]


@pytest.mark.asyncio
async def test_la_fermeture_finit_par_alerter_si_le_service_ne_revient_jamais() -> None:
    """L'attente ne doit pas devenir un silence : au bout du compte, on alerte."""
    alertes = _Alertes()

    async def dormir(duree: float) -> None:
        return None

    http = _HttpProgressif(echecs=10_000)
    cluster = _Cluster({"PROFIL_MATERIEL": "gpu", "INFERENCE_URL": TUNNEL})
    await fenetre.fermer(cluster, http, _reglages(), alertes, dormir)

    assert http.sondes > 1, "une seule sonde ne suffit pas à conclure"
    assert "ne répond pas" in alertes.envoyees[0][0]
