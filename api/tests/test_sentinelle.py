"""Sentinelle de profil matériel — repli automatique vers le CPU (spec V3 §4.5, M6).

Ce que ces tests protègent, et pourquoi chacun existe :

* **Ne jamais replier hors du profil GPU.** La sentinelle ne doit pas se mettre à
  bousculer un cluster qui sert déjà en CPU : ce serait redémarrer l'API en boucle
  pendant une maintenance délibérée.
* **Ne jamais replier sur une panne d'API.** Si l'API elle-même ne répond plus, ce
  n'est pas un incident d'inférence ; basculer le profil n'y changerait rien et
  ferait perdre la session GPU pour une mauvaise raison.
* **Replier seulement après N échecs consécutifs.** Une sonde isolée qui rate
  (redémarrage progressif, hoquet réseau) ne doit pas coûter la démonstration.
* **L'ordre des effets.** Le CPU remonte AVANT que l'URL ne change — même doctrine
  que ``deploy/scripts/profil.sh`` : on ne coupe pas ce qui répond encore.

Aucun test n'ouvre de socket : la sonde et le cluster sont injectés.
"""

from __future__ import annotations

import pytest

from app.exploitation.sentinelle import Action, Sonde, decider

SEUIL = 3


def _sonde(api_vivante: bool = True, inference_ok: bool = True) -> Sonde:
    return Sonde(api_vivante=api_vivante, inference_ok=inference_ok)


def test_tout_va_bien_remet_le_compteur_a_zero() -> None:
    """Une sonde saine efface l'historique : deux hoquets espacés ne s'additionnent pas."""
    decision = decider(profil="gpu", sonde=_sonde(), echecs=2, seuil=SEUIL)

    assert decision.action is Action.RIEN
    assert decision.echecs == 0


def test_profil_cpu_ne_replie_jamais() -> None:
    """En CPU il n'y a rien à replier : la sentinelle regarde, elle n'agit pas."""
    decision = decider(
        profil="cpu", sonde=_sonde(inference_ok=False), echecs=SEUIL + 5, seuil=SEUIL
    )

    assert decision.action is Action.RIEN
    assert decision.echecs == 0


def test_api_muette_n_entraine_pas_de_repli() -> None:
    """API injoignable = incident d'API, pas d'inférence. Replier n'y changerait rien."""
    decision = decider(
        profil="gpu",
        sonde=_sonde(api_vivante=False, inference_ok=False),
        echecs=SEUIL - 1,
        seuil=SEUIL,
    )

    assert decision.action is Action.ALERTE_API
    assert decision.echecs == 0


def test_un_echec_isole_ne_suffit_pas() -> None:
    """Le premier échec compte, mais ne déclenche rien."""
    decision = decider(profil="gpu", sonde=_sonde(inference_ok=False), echecs=0, seuil=SEUIL)

    assert decision.action is Action.RIEN
    assert decision.echecs == 1


def test_avant_dernier_echec_ne_suffit_pas() -> None:
    """Au seuil moins un, on attend encore : la bordure est du bon côté."""
    decision = decider(
        profil="gpu", sonde=_sonde(inference_ok=False), echecs=SEUIL - 2, seuil=SEUIL
    )

    assert decision.action is Action.RIEN
    assert decision.echecs == SEUIL - 1


def test_le_seuil_atteint_declenche_le_repli() -> None:
    """Trois échecs consécutifs : l'inférence est perdue, on rentre au CPU."""
    decision = decider(
        profil="gpu", sonde=_sonde(inference_ok=False), echecs=SEUIL - 1, seuil=SEUIL
    )

    assert decision.action is Action.REPLI


def test_le_repli_remet_le_compteur_a_zero() -> None:
    """Sinon un repli qui échoue (RBAC) serait retenté à chaque cycle, sans répit."""
    decision = decider(
        profil="gpu", sonde=_sonde(inference_ok=False), echecs=SEUIL - 1, seuil=SEUIL
    )

    assert decision.echecs == 0


@pytest.mark.parametrize("profil", ["cpu", "", "?"])
def test_seul_le_profil_gpu_autorise_une_action(profil: str) -> None:
    """Une ConfigMap illisible ou vide ne doit pas être interprétée comme « GPU »."""
    decision = decider(profil=profil, sonde=_sonde(inference_ok=False), echecs=99, seuil=SEUIL)

    assert decision.action is Action.RIEN


# --------------------------------------------------------------------------------
# La sonde — ce que la sentinelle observe, sans jamais parler au modèle.
# --------------------------------------------------------------------------------


class _Http:
    """Client HTTP factice : répond selon un plan, et retient ce qu'on lui a demandé."""

    def __init__(self, plan: dict[str, object]) -> None:
        self.plan = plan
        self.appels: list[str] = []

    async def get(self, url: str, headers: dict | None = None) -> object:
        self.appels.append(url)
        reponse = self.plan[url.rsplit("/v1/", 1)[1]]
        if isinstance(reponse, Exception):
            raise reponse
        return reponse


class _Reponse:
    def __init__(self, code: int, corps: object = None) -> None:
        self.status_code = code
        self._corps = corps

    def json(self) -> object:
        if isinstance(self._corps, Exception):
            raise self._corps
        return self._corps


async def test_sonde_service_sain() -> None:
    """API debout et inférence déclarée disponible."""
    from app.exploitation.sentinelle import sonder

    http = _Http(
        {"health": _Reponse(200, {"status": "ok"}), "ready": _Reponse(200, {"inference": True})}
    )

    sonde = await sonder(http, "http://api:8080", "opencacao.example")

    assert sonde == Sonde(api_vivante=True, inference_ok=True)


async def test_sonde_inference_perdue_mais_api_debout() -> None:
    """Le cas qui déclenche le repli : /v1/ready répond 503 en disant pourquoi."""
    from app.exploitation.sentinelle import sonder

    http = _Http(
        {"health": _Reponse(200), "ready": _Reponse(503, {"inference": False, "redis": True})}
    )

    sonde = await sonder(http, "http://api:8080", "opencacao.example")

    assert sonde == Sonde(api_vivante=True, inference_ok=False)


async def test_sonde_ready_qui_expire_compte_comme_inference_perdue() -> None:
    """Tunnel qui absorbe les paquets : /v1/ready pend, /v1/health répond. C'est une panne."""
    from app.exploitation.sentinelle import sonder

    http = _Http({"health": _Reponse(200), "ready": TimeoutError("délai dépassé")})

    sonde = await sonder(http, "http://api:8080", "opencacao.example")

    assert sonde == Sonde(api_vivante=True, inference_ok=False)


async def test_sonde_api_muette_n_interroge_pas_ready() -> None:
    """Inutile — et surtout : le résultat ne doit pas ressembler à une panne d'inférence."""
    from app.exploitation.sentinelle import sonder

    http = _Http({"health": ConnectionError("refusé"), "ready": _Reponse(200, {"inference": True})})

    sonde = await sonder(http, "http://api:8080", "opencacao.example")

    assert sonde == Sonde(api_vivante=False, inference_ok=False)
    assert not any("ready" in url for url in http.appels)


async def test_sonde_corps_illisible_vaut_panne() -> None:
    """Un /v1/ready qui ne rend pas le JSON attendu ne prouve pas que l'inférence va bien."""
    from app.exploitation.sentinelle import sonder

    http = _Http({"health": _Reponse(200), "ready": _Reponse(200, ValueError("pas du JSON"))})

    sonde = await sonder(http, "http://api:8080", "opencacao.example")

    assert sonde.inference_ok is False


async def test_sonde_porte_l_entete_host() -> None:
    """TrustedHostMiddleware : une sonde sans Host valide serait rejetée, donc fausse."""
    from app.exploitation.sentinelle import sonder

    vus: list[dict] = []

    class _HttpEntetes(_Http):
        async def get(self, url: str, headers: dict | None = None) -> object:
            vus.append(headers or {})
            return await super().get(url, headers)

    http = _HttpEntetes({"health": _Reponse(200), "ready": _Reponse(200, {"inference": True})})

    await sonder(http, "http://api:8080", "opencacao.example")

    assert all(entetes.get("Host") == "opencacao.example" for entetes in vus)


# --------------------------------------------------------------------------------
# Le repli — l'ordre des effets, et le délestage.
# --------------------------------------------------------------------------------


class _ClusterFactice:
    """Cluster factice qui retient l'ordre exact des effets appliqués."""

    def __init__(self, echec: Exception | None = None) -> None:
        self.journal: list[tuple[str, object]] = []
        self.echec = echec

    async def mettre_a_l_echelle(self, deploiement: str, repliques: int) -> None:
        if self.echec:
            raise self.echec
        self.journal.append(("echelle", (deploiement, repliques)))

    async def patch_configmap(self, nom: str, data: dict[str, str]) -> None:
        self.journal.append(("configmap", (nom, data)))

    async def rollout_restart(self, deploiement: str) -> None:
        self.journal.append(("restart", deploiement))


def _reglages():
    from app.exploitation.sentinelle import Reglages

    return Reglages(
        configmap="api-config",
        deploiement_cpu="inference",
        deploiement_api="api",
        url_interne="http://inference:8000",
        base_api="http://api:8080",
        hote="opencacao.example",
        intervalle_s=1,
        seuil=3,
        timeout_s=5,
    )


async def test_repli_remonte_le_cpu_avant_de_changer_l_url() -> None:
    """Doctrine de profil.sh : on ne coupe pas ce qui répond encore avant d'avoir un relais."""
    from app.exploitation.sentinelle import replier

    cluster = _ClusterFactice()

    await replier(cluster, _reglages())

    verbes = [verbe for verbe, _ in cluster.journal]
    assert verbes == ["echelle", "configmap", "restart"]


async def test_repli_remonte_le_deploiement_cpu_a_une_replique() -> None:
    from app.exploitation.sentinelle import replier

    cluster = _ClusterFactice()

    await replier(cluster, _reglages())

    assert cluster.journal[0] == ("echelle", ("inference", 1))


async def test_repli_ramene_l_inference_dans_le_cluster() -> None:
    """Oublier INFERENCE_URL laisserait l'API parler à un pod loué déjà détruit."""
    from app.exploitation.sentinelle import replier

    cluster = _ClusterFactice()

    await replier(cluster, _reglages())

    _, (_, data) = cluster.journal[1]
    assert data["INFERENCE_URL"] == "http://inference:8000"
    assert data["PROFIL_MATERIEL"] == "cpu"
    assert data["INFERENCE_BACKEND"] == "llama-cpp"


async def test_repli_deleste_les_fonctions_qui_noieraient_le_cpu() -> None:
    """Une étude ou une parcelle sur CPU monopolise l'inférence : le chat mourrait avec."""
    from app.exploitation.sentinelle import replier

    cluster = _ClusterFactice()

    await replier(cluster, _reglages())

    _, (_, data) = cluster.journal[1]
    assert data["RAPPORTS_ENABLED"] == "false"
    assert data["PARCELLES_ENABLED"] == "false"
    assert data["VISION_ENABLED"] == "false"


async def test_repli_leve_le_drapeau_qui_fait_parler_l_interface() -> None:
    """Sans ce drapeau, le web dirait « bientôt » là où il faut dire « service de secours »."""
    from app.exploitation.sentinelle import replier

    cluster = _ClusterFactice()

    await replier(cluster, _reglages())

    _, (_, data) = cluster.journal[1]
    assert data["REPLI_CPU"] == "true"


async def test_repli_redemarre_l_api_en_dernier() -> None:
    """Redémarrer avant d'avoir patché relirait l'ancienne ConfigMap."""
    from app.exploitation.sentinelle import replier

    cluster = _ClusterFactice()

    await replier(cluster, _reglages())

    assert cluster.journal[-1] == ("restart", "api")


# --------------------------------------------------------------------------------
# Le cycle complet — lecture du cluster, décision, effets, alerte.
# --------------------------------------------------------------------------------


class _ClusterComplet(_ClusterFactice):
    """Cluster factice qui sait aussi rendre une ConfigMap."""

    def __init__(self, profil: str = "gpu", lecture_echoue: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.profil = profil
        self.lecture_echoue = lecture_echoue

    async def lire_configmap(self, nom: str) -> dict[str, str]:
        if self.lecture_echoue:
            raise RuntimeError("API server injoignable")
        return {"PROFIL_MATERIEL": self.profil}


class _Alertes:
    """Collecte les alertes au lieu de les envoyer."""

    def __init__(self) -> None:
        self.envoyees: list[tuple[str, str]] = []

    async def __call__(self, sujet: str, texte: str) -> None:
        self.envoyees.append((sujet, texte))


def _http_sain() -> _Http:
    return _Http({"health": _Reponse(200), "ready": _Reponse(200, {"inference": True})})


def _http_inference_perdue() -> _Http:
    return _Http({"health": _Reponse(200), "ready": _Reponse(503, {"inference": False})})


async def test_cycle_configmap_illisible_n_agit_pas() -> None:
    """Ne pas savoir dans quel profil on est n'autorise rien : l'inaction est le défaut sûr."""
    from app.exploitation.sentinelle import Etat, un_cycle

    cluster = _ClusterComplet(lecture_echoue=True)
    alertes = _Alertes()

    etat = await un_cycle(
        Etat(echecs=2),
        cluster=cluster,
        http=_http_inference_perdue(),
        reglages=_reglages(),
        alerter=alertes,
    )

    assert cluster.journal == []
    assert etat.echecs == 2


async def test_cycle_sain_ne_touche_a_rien() -> None:
    from app.exploitation.sentinelle import Etat, un_cycle

    cluster = _ClusterComplet()
    alertes = _Alertes()

    etat = await un_cycle(
        Etat(echecs=2), cluster=cluster, http=_http_sain(), reglages=_reglages(), alerter=alertes
    )

    assert cluster.journal == []
    assert alertes.envoyees == []
    assert etat.echecs == 0


async def test_cycle_replie_au_seuil_et_previent() -> None:
    """Le troisième échec consécutif exécute le repli ET envoie l'email."""
    from app.exploitation.sentinelle import Etat, un_cycle

    cluster = _ClusterComplet()
    alertes = _Alertes()

    await un_cycle(
        Etat(echecs=2),
        cluster=cluster,
        http=_http_inference_perdue(),
        reglages=_reglages(),
        alerter=alertes,
    )

    assert [verbe for verbe, _ in cluster.journal] == ["echelle", "configmap", "restart"]
    assert len(alertes.envoyees) == 1


async def test_cycle_repli_en_echec_previent_sans_planter() -> None:
    """RBAC refusé : la boucle doit survivre et le dire, pas mourir en silence."""
    from app.exploitation.sentinelle import Etat, un_cycle

    cluster = _ClusterComplet(echec=PermissionError("forbidden"))
    alertes = _Alertes()

    etat = await un_cycle(
        Etat(echecs=2),
        cluster=cluster,
        http=_http_inference_perdue(),
        reglages=_reglages(),
        alerter=alertes,
    )

    assert etat.echecs == 0
    assert len(alertes.envoyees) == 1
    # L'objet du message est ce qu'on lit dans la boîte : c'est lui qui doit crier.
    assert "échec" in alertes.envoyees[0][0].lower()


async def test_cycle_api_muette_n_alerte_qu_une_fois() -> None:
    """Une panne d'API dure des minutes : une alerte par cycle serait une tempête."""
    from app.exploitation.sentinelle import Etat, un_cycle

    cluster = _ClusterComplet()
    alertes = _Alertes()
    http = _Http({"health": ConnectionError("refusé"), "ready": _Reponse(200)})

    etat = await un_cycle(Etat(), cluster=cluster, http=http, reglages=_reglages(), alerter=alertes)
    etat = await un_cycle(etat, cluster=cluster, http=http, reglages=_reglages(), alerter=alertes)

    assert len(alertes.envoyees) == 1
    assert cluster.journal == []


async def test_cycle_retour_a_la_normale_rearme_l_alerte() -> None:
    """Sinon une seconde panne, plus tard, passerait inaperçue."""
    from app.exploitation.sentinelle import Etat, un_cycle

    cluster = _ClusterComplet()
    alertes = _Alertes()
    muette = _Http({"health": ConnectionError("refusé"), "ready": _Reponse(200)})

    etat = await un_cycle(
        Etat(), cluster=cluster, http=muette, reglages=_reglages(), alerter=alertes
    )
    etat = await un_cycle(
        etat, cluster=cluster, http=_http_sain(), reglages=_reglages(), alerter=alertes
    )
    await un_cycle(etat, cluster=cluster, http=muette, reglages=_reglages(), alerter=alertes)

    assert len(alertes.envoyees) == 2


async def test_cycle_profil_cpu_n_agit_jamais() -> None:
    """Le service tourne déjà sur CPU : il n'y a rien à replier, et tout à casser."""
    from app.exploitation.sentinelle import Etat, un_cycle

    cluster = _ClusterComplet(profil="cpu")
    alertes = _Alertes()

    await un_cycle(
        Etat(echecs=99),
        cluster=cluster,
        http=_http_inference_perdue(),
        reglages=_reglages(),
        alerter=alertes,
    )

    assert cluster.journal == []


async def test_boucle_enchaine_les_cycles_et_respecte_l_intervalle() -> None:
    """La boucle est mince, mais elle doit dormir entre deux cycles — sinon elle martèle."""
    from app.exploitation.sentinelle import boucle

    cluster = _ClusterComplet()
    alertes = _Alertes()
    sommeils: list[float] = []

    async def dormir(secondes: float) -> None:
        sommeils.append(secondes)
        if len(sommeils) == 3:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        await boucle(
            cluster=cluster,
            http=_http_sain(),
            reglages=_reglages(),
            alerter=alertes,
            dormir=dormir,
        )

    assert sommeils == [1, 1, 1]


# --------------------------------------------------------------------------------
# Le client cluster — les trois seuls pouvoirs accordés à la sentinelle.
# --------------------------------------------------------------------------------


def _client_k8s(handler):
    import httpx

    from app.curation.k8s import ClusterClient

    return ClusterClient(
        hote="https://kube",
        namespace="opencacao",
        token="jeton",
        verify=False,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_lire_configmap_rend_la_section_data() -> None:
    import httpx

    def handler(requete: httpx.Request) -> httpx.Response:
        assert requete.url.path == "/api/v1/namespaces/opencacao/configmaps/api-config"
        return httpx.Response(200, json={"data": {"PROFIL_MATERIEL": "gpu"}})

    client = _client_k8s(handler)

    assert await client.lire_configmap("api-config") == {"PROFIL_MATERIEL": "gpu"}
    await client.close()


async def test_patch_configmap_fusionne_les_seules_cles_donnees() -> None:
    """Un patch de fusion, pas un remplacement : les 40 autres clés doivent survivre."""
    import json

    import httpx

    vus: list[dict] = []

    def handler(requete: httpx.Request) -> httpx.Response:
        vus.append(
            {
                "methode": requete.method,
                "chemin": requete.url.path,
                "type": requete.headers.get("Content-Type"),
                "corps": json.loads(requete.content),
            }
        )
        return httpx.Response(200, json={})

    client = _client_k8s(handler)
    await client.patch_configmap("api-config", {"PROFIL_MATERIEL": "cpu"})
    await client.close()

    assert vus[0]["methode"] == "PATCH"
    assert vus[0]["chemin"] == "/api/v1/namespaces/opencacao/configmaps/api-config"
    assert "merge-patch" in vus[0]["type"]
    assert vus[0]["corps"] == {"data": {"PROFIL_MATERIEL": "cpu"}}


async def test_patch_configmap_signale_un_refus() -> None:
    """Un 403 doit remonter comme une panne, pas passer pour un succès silencieux."""
    import httpx

    from app.curation.k8s import ClusterIndisponible

    client = _client_k8s(lambda requete: httpx.Response(403, json={"message": "forbidden"}))

    with pytest.raises(ClusterIndisponible):
        await client.patch_configmap("api-config", {"PROFIL_MATERIEL": "cpu"})
    await client.close()


async def test_mise_a_l_echelle_patche_les_repliques() -> None:
    import json

    import httpx

    vus: list[dict] = []

    def handler(requete: httpx.Request) -> httpx.Response:
        vus.append({"chemin": requete.url.path, "corps": json.loads(requete.content)})
        return httpx.Response(200, json={})

    client = _client_k8s(handler)
    await client.mettre_a_l_echelle("inference", 1)
    await client.close()

    assert vus[0]["chemin"] == "/apis/apps/v1/namespaces/opencacao/deployments/inference"
    assert vus[0]["corps"] == {"spec": {"replicas": 1}}


async def test_mise_a_l_echelle_signale_un_refus() -> None:
    import httpx

    from app.curation.k8s import ClusterIndisponible

    client = _client_k8s(lambda requete: httpx.Response(403, json={}))

    with pytest.raises(ClusterIndisponible):
        await client.mettre_a_l_echelle("inference", 1)
    await client.close()


async def test_lire_configmap_sans_section_data() -> None:
    """Une ConfigMap vide n'est pas une erreur : elle ne déclare simplement aucun profil."""
    import httpx

    client = _client_k8s(lambda requete: httpx.Response(200, json={}))

    assert await client.lire_configmap("api-config") == {}
    await client.close()


@pytest.mark.parametrize(
    "nom",
    [
        "../secrets/opencacao-auth",  # évasion de chemin vers un Secret
        "api-config/../../x",
        "api config",  # espace : chemin fabriqué
        "",  # vide : viserait la collection entière
        "A" * 254,  # au-delà de la limite DNS-1123
        "-api-config",  # ne commence pas par un caractère alphanumérique
    ],
)
async def test_un_nom_de_ressource_invalide_est_refuse(nom: str) -> None:
    """Défense en profondeur : une variable d'environnement empoisonnée ne doit pas
    permettre de viser une autre ressource que celles prévues — un Secret, par exemple.
    Le serveur factice répond 200 à tout : seul un refus côté client peut faire passer
    ce test."""
    import httpx

    from app.curation.k8s import ClusterIndisponible

    client = _client_k8s(lambda requete: httpx.Response(200, json={}))

    with pytest.raises(ClusterIndisponible):
        await client.patch_configmap(nom, {"PROFIL_MATERIEL": "cpu"})
    with pytest.raises(ClusterIndisponible):
        await client.mettre_a_l_echelle(nom, 1)
    with pytest.raises(ClusterIndisponible):
        await client.lire_configmap(nom)
    await client.close()


async def test_un_nom_de_ressource_valide_passe() -> None:
    """Le garde-fou ne doit pas refuser les noms légitimes du dépôt."""
    import httpx

    client = _client_k8s(lambda requete: httpx.Response(200, json={"data": {}}))

    for nom in ("api-config", "inference", "inference-gpu", "api"):
        assert await client.lire_configmap(nom) == {}
    await client.close()


# --------------------------------------------------------------------------------
# Le point d'entrée — ce que le pod exécute réellement.
# --------------------------------------------------------------------------------


async def test_reglages_depuis_environnement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les valeurs par défaut doivent être sûres, et l'environnement doit primer."""
    from app.exploitation.sentinelle import reglages_depuis_env

    monkeypatch.setenv("SENTINELLE_SEUIL", "5")
    monkeypatch.setenv("SENTINELLE_INTERVALLE_S", "30")
    monkeypatch.setenv("API_BASE_INTERNE", "http://api:9999")

    reglages = reglages_depuis_env()

    assert reglages.seuil == 5
    assert reglages.intervalle_s == 30
    assert reglages.base_api == "http://api:9999"
    assert reglages.configmap == "api-config"
    assert reglages.deploiement_cpu == "inference"
    assert reglages.url_interne == "http://inference:8000"


async def test_reglages_valeurs_par_defaut(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans rien dans l'environnement, la sentinelle reste utilisable et prudente."""
    from app.exploitation.sentinelle import reglages_depuis_env

    for cle in ("SENTINELLE_SEUIL", "SENTINELLE_INTERVALLE_S", "API_BASE_INTERNE"):
        monkeypatch.delenv(cle, raising=False)

    reglages = reglages_depuis_env()

    assert reglages.seuil >= 2, "un seul échec ne doit jamais suffire"
    assert reglages.intervalle_s >= 5, "marteler l'API serait une charge, pas une sonde"


async def test_main_hors_cluster_s_arrete_proprement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lancée hors du cluster (poste de développement), elle refuse au lieu de boucler."""
    from app.curation.k8s import ClusterClient, ClusterIndisponible
    from app.exploitation import sentinelle

    def absent(cls):
        raise ClusterIndisponible("jeton ServiceAccount absent")

    monkeypatch.setattr(ClusterClient, "from_serviceaccount", classmethod(absent))

    assert await sentinelle.main() is False


async def test_main_lance_la_boucle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Et ferme ses clients à la sortie : un pod qui redémarre ne doit rien laisser ouvert."""
    from app.curation.k8s import ClusterClient
    from app.exploitation import sentinelle

    ferme: list[str] = []

    class _ClusterFerme(_ClusterComplet):
        async def close(self) -> None:
            ferme.append("cluster")

    monkeypatch.setattr(
        ClusterClient, "from_serviceaccount", classmethod(lambda cls: _ClusterFerme())
    )

    async def fausse_boucle(**kwargs) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(sentinelle, "boucle", fausse_boucle)

    assert await sentinelle.main() is True
    assert ferme == ["cluster"]
