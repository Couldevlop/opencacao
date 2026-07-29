"""Chemins de dégradation du RAG et des services support (couverture 99 %).

Ces tests visent les branches *défensives* — index absent, corrompu ou désaccordé
avec le modèle d'embeddings, base SQLite illisible, client HTTP à refermer, course
de suppression concurrente. L'exigence commune : **sans donnée fiable, le système
dégrade proprement et n'invente rien** (correctif v0.6.48).

Aucun appel réseau : les transports httpx sont simulés (``httpx.MockTransport``) et
les bases SQLite vivent dans ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api_deps import get_cache_client, get_session_store
from app.core import email as email_mod
from app.core.auth_store import AuthStore
from app.core.parametres import CLE_EMAIL_EXPEDITEUR, ParametresStore
from app.models.session import Session
from app.services import clarification, localites
from app.services.localites import _quasi_egal
from app.services.notifier import ZeptoMailNotifier
from app.services.rag import _BM25, RagIndex, RagRecuperateur, couverture_lexicale
from app.services.rag_index_builder import ecrire_index, lire_index, lire_textes_indexes

# --------------------------------------------------------------------------- outils


class _EmbeddingsFixes:
    """Service d'embeddings simulé : renvoie toujours le même vecteur."""

    def __init__(self, vecteur: list[float]) -> None:
        self.vecteur = vecteur
        self.appels: list[list[str]] = []

    async def embed(self, textes: list[str]) -> list[list[float]]:
        """Retourne un vecteur fixe par texte (aucun appel réseau)."""
        self.appels.append(list(textes))
        return [list(self.vecteur) for _ in textes]


def _ecrire_lignes(chemin: Path, lignes: list[str]) -> None:
    """Écrit un fichier JSONL brut, lignes vides et lignes invalides comprises."""
    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")


def _index_trois_dimensions(chemin: Path) -> RagIndex:
    """Construit un index RAG minimal (3 dimensions) et le charge."""
    _ecrire_lignes(
        chemin,
        [
            json.dumps(
                {
                    "texte": "La taille sanitaire du cacaoyer se pratique en saison sèche.",
                    "source": "CNRA",
                    "vecteur": [1.0, 0.0, 0.0],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "texte": "Le séchage des fèves se fait en couche fine.",
                    "source": "ANADER",
                    "vecteur": [0.0, 1.0, 0.0],
                },
                ensure_ascii=False,
            ),
        ],
    )
    index = RagIndex.charger(chemin)
    assert index is not None
    return index


# ------------------------------------------------------------------ rag.py : lexical


def test_couverture_lexicale_nulle_si_la_reference_na_aucun_mot_porteur() -> None:
    """Une référence réduite à des mots vides ne peut valider aucun hit de cache.

    Garde-fou du cache sémantique : si la question cachée ne contient aucun mot
    significatif, la couverture vaut 0 — donc jamais le seuil — et le cache ne peut
    pas resservir une réponse au titre d'une ressemblance vide de sens.
    """
    assert couverture_lexicale("et le la des", "comment tailler mon cacaoyer ?") == 0.0
    # Contraste : une référence porteuse, elle, se mesure normalement.
    assert couverture_lexicale("tailler cacaoyer", "comment tailler mon cacaoyer ?") == 1.0


def test_bm25_sur_un_corpus_sans_mot_porteur_ne_classe_aucun_document() -> None:
    """Un corpus dont aucun document n'a de mot significatif ne remonte rien.

    Longueur moyenne nulle : le canal lexical renvoie des scores nuls plutôt que de
    diviser par zéro. Le vivier reste vide au lieu de proposer des voisins arbitraires.
    """
    bm25 = _BM25.construire(["le la des", "de la"])
    scores = bm25.scores({"cacaoyer"})
    assert scores.shape == (2,)
    assert not scores.any()
    assert bm25.top({"cacaoyer"}, 5) == []


# -------------------------------------------------------- rag.py : chargement d'index


def test_index_ignore_lignes_vides_et_corrompues_sans_perdre_les_valides(
    tmp_path: Path,
) -> None:
    """Un index partiellement abîmé garde ses entrées saines au lieu d'être rejeté.

    Une ligne vide, une ligne non JSON et une entrée sans vecteur sont sautées ; les
    deux entrées valides restent interrogeables.
    """
    chemin = tmp_path / "index_abime.jsonl"
    _ecrire_lignes(
        chemin,
        [
            json.dumps({"texte": "Taille du cacaoyer.", "source": "CNRA", "vecteur": [1.0, 0.0]}),
            "",
            "   ",
            "{ceci n'est pas du JSON",
            json.dumps({"texte": "Entrée sans vecteur.", "source": "FAO"}),
            json.dumps({"texte": "Séchage des fèves.", "source": "ANADER", "vecteur": [0.0, 1.0]}),
        ],
    )

    index = RagIndex.charger(chemin)

    assert index is not None
    assert index.taille == 2
    passages = index.rechercher([1.0, 0.0], k=2, seuil=0.5)
    assert [p.texte for p in passages] == ["Taille du cacaoyer."]


def test_index_entierement_corrompu_est_traite_comme_absent(tmp_path: Path) -> None:
    """Un index illisible équivaut à pas d'index : ``None``, jamais un index vide bancal.

    L'API démarre alors sans RAG (réponse sans contexte) au lieu de servir un index
    sans vecteur qui ferait échouer toute recherche en cours de requête.
    """
    chemin = tmp_path / "index_corrompu.jsonl"
    _ecrire_lignes(chemin, ["", "pas du json", json.dumps({"texte": "sans vecteur"})])

    assert RagIndex.charger(chemin) is None


# ------------------------------------------------------------- rag.py : récupérateur


async def test_mode_dense_seul_reste_operationnel(tmp_path: Path) -> None:
    """Hybride désactivé : le vivier dense seul alimente encore le contexte.

    Le drapeau ``hybride`` est un interrupteur d'exploitation (repli si le canal BM25
    pose problème) : il ne doit pas priver l'API de contexte.
    """
    index = _index_trois_dimensions(tmp_path / "idx.jsonl")
    recuperateur = RagRecuperateur(
        _EmbeddingsFixes([1.0, 0.0, 0.0]),
        index,
        top_k=1,
        seuil=0.5,
        hybride=False,
    )

    contexte = await recuperateur.contexte_pour("Quand faire la taille sanitaire ?")

    assert contexte is not None
    assert "taille sanitaire" in contexte
    assert "(source : CNRA)" in contexte


async def test_index_desaccorde_avec_le_modele_dembeddings_ne_rend_aucun_contexte(
    tmp_path: Path,
) -> None:
    """Dimensions incompatibles : aucun contexte, aucune exception, rien d'inventé.

    Cas d'exploitation réel (bascule 4B→0,6B mal séquencée) : l'index est en 3
    dimensions et le service d'embeddings en renvoie 5. Plutôt qu'un HTTP 500 ou,
    pire, des voisins calculés sur des vecteurs incomparables, la recherche renvoie
    un vivier vide et l'API répond sans contexte.
    """
    index = _index_trois_dimensions(tmp_path / "idx.jsonl")

    assert index.candidats([1.0, 0.0, 0.0, 0.0, 0.0], 5) == []
    assert index.vivier_hybride([1.0, 0.0, 0.0, 0.0, 0.0], "taille du cacaoyer", 5) == []

    for hybride in (True, False):
        recuperateur = RagRecuperateur(
            _EmbeddingsFixes([1.0, 0.0, 0.0, 0.0, 0.0]),
            index,
            top_k=3,
            seuil=0.5,
            hybride=hybride,
        )
        assert await recuperateur.contexte_pour("Quand faire la taille sanitaire ?") is None


# ------------------------------------------------------- rag_index_builder.py : lecture


def test_lire_index_ignore_lignes_vides_et_corrompues(tmp_path: Path) -> None:
    """La relecture de l'index saute lignes vides et lignes invalides.

    Un index alimenté en append (console de curation) peut se terminer par une ligne
    partielle ; la reconstruction ne doit pas s'effondrer pour autant.
    """
    chemin = tmp_path / "index.jsonl"
    _ecrire_lignes(
        chemin,
        [
            json.dumps({"texte": "Réponse A", "source": "CNRA", "vecteur": [0.1, 0.2]}),
            "",
            "{tronqué",
            json.dumps({"texte": "Réponse B", "vecteur": [0.3, 0.4]}),
        ],
    )

    entrees = lire_index(chemin)

    assert [e["texte"] for e in entrees] == ["Réponse A", "Réponse B"]
    assert entrees[1]["source"] == ""  # source absente -> chaîne vide, pas d'erreur


def test_lire_textes_indexes_ignore_lignes_vides_et_corrompues(tmp_path: Path) -> None:
    """La déduplication en flux tolère un index abîmé sans perdre les textes valides.

    C'est ce jeu de textes qui empêche un reindex additif de créer des doublons : s'il
    échouait sur une ligne partielle, la console réindexerait tout le corpus en double.
    """
    chemin = tmp_path / "index.jsonl"
    _ecrire_lignes(
        chemin,
        [
            json.dumps({"texte": " Réponse A ", "vecteur": [0.1]}),
            "",
            "   ",
            "{tronqué",
            json.dumps({"texte": 42, "vecteur": [0.2]}),  # texte non textuel : ignoré
            json.dumps({"texte": "Réponse B", "vecteur": [0.3]}),
        ],
    )

    assert lire_textes_indexes(chemin) == {"Réponse A", "Réponse B"}


def test_ecriture_index_echouee_ne_laisse_ni_temporaire_ni_index_corrompu(
    tmp_path: Path,
) -> None:
    """Une écriture qui échoue en cours de route ne détruit pas l'index en place.

    Écriture atomique : le nouvel index s'écrit dans un fichier temporaire renommé à
    la fin. Si la sérialisation échoue à mi-parcours, l'erreur remonte, le temporaire
    est supprimé et l'index de production reste exactement celui d'avant.
    """
    chemin = tmp_path / "rag_index.jsonl"
    ancien = json.dumps({"texte": "Réponse en place", "source": "CNRA", "vecteur": [0.5]})
    _ecrire_lignes(chemin, [ancien])

    with pytest.raises(TypeError):
        ecrire_index(
            chemin,
            [
                {"texte": "Nouvelle réponse", "source": "CNRA", "vecteur": [0.1]},
                {"texte": "Réponse impossible", "source": "CNRA", "vecteur": {1, 2}},
            ],
        )

    assert chemin.read_text(encoding="utf-8").strip() == ancien
    assert list(tmp_path.glob("*.tmp")) == []


# ------------------------------------------------------------------ bases SQLite


async def test_auth_store_tolere_une_base_illisible(tmp_path: Path) -> None:
    """Base d'authentification inouvrable : le dépôt se déclare non prêt, sans lever.

    Le démarrage de l'API ne doit jamais échouer à cause du volume ``/data`` (droits,
    montage manquant) : l'authentification se signale indisponible et le reste du
    service continue de répondre.
    """
    chemin = tmp_path / "auth.db"
    chemin.mkdir()  # un répertoire à la place du fichier : SQLite ne peut pas l'ouvrir
    store = AuthStore(chemin)

    await store.initialiser()

    assert store.pret is False


async def test_parametres_store_tolere_une_base_illisible(tmp_path: Path) -> None:
    """Base des réglages inouvrable : dépôt non prêt et lectures qui renvoient ``None``.

    L'appelant retombe alors sur ses valeurs par défaut (expéditeur des emails) plutôt
    que de propager une panne de stockage.
    """
    chemin = tmp_path / "parametres.db"
    chemin.mkdir()
    store = ParametresStore(chemin)

    await store.initialiser()

    assert store.pret is False
    assert await store.obtenir(CLE_EMAIL_EXPEDITEUR) is None


# -------------------------------------------------------------------- localites.py


def test_un_mot_identique_est_quasi_egal() -> None:
    """La tolérance aux fautes accepte d'abord l'identité stricte.

    Cas de base du comparateur flou : une localité écrite correctement reste reconnue
    par la voie floue (utilisée en repli quand la recherche exacte n'a rien donné).
    """
    assert _quasi_egal("daloa", "daloa") is True
    assert _quasi_egal("daloa", "gagnoa") is False


def test_detecter_tolere_une_lettre_substituee() -> None:
    """Une frappe mobile avec une lettre fausse désigne quand même la bonne localité.

    « daloq » (touche voisine de « a ») doit rester Daloa : c'est ce qui permet aux
    garde-fous de localité de se déclencher malgré une coquille.
    """
    assert localites.detecter("ma plantation est a daloq") == "Daloa"
    assert localites.detecter_nord("je suis a korhpgo") == "Korhogo"


# ----------------------------------------------------------------- clarification.py


def test_question_informationnelle_ne_declenche_pas_de_clarification() -> None:
    """Une question de prévention/définition reçoit une réponse, pas un interrogatoire.

    La clarification sert à cadrer un diagnostic ; une question déjà précise
    (« comment prévenir… ») doit passer directement à la réponse.
    """
    assert clarification.detecter_theme("Comment prévenir le swollen shoot ?", []) is None
    # Contraste : le même champ lexical, mais en observation de symptôme, clarifie.
    assert clarification.detecter_theme("mes feuilles jaunissent", []) == "symptome"


# ---------------------------------------------------------- notifier.py / email.py


async def test_fermeture_du_notifier_libere_le_client_http() -> None:
    """Le notifier ZeptoMail referme son client HTTP à l'arrêt (pas de socket fuité)."""
    transport = httpx.MockTransport(lambda _requete: httpx.Response(201, json={"message": "OK"}))
    client = httpx.AsyncClient(transport=transport)
    notifier = ZeptoMailNotifier(
        token="jeton-test",
        api_url="https://zeptomail.invalid/v1.1/email",
        from_address="waopron@openlabconsulting.com",
        from_name="OpenCacao",
        client=client,
    )
    await notifier.envoyer_lien("paysan@cacao.ci", "https://opencacao.ci/?auth=x")
    assert client.is_closed is False

    await notifier.close()

    assert client.is_closed is True


async def test_alerte_sans_client_injecte_cree_et_referme_son_propre_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En production (client non injecté), le mailer referme le client qu'il a créé.

    Les alertes partent depuis des tâches courtes (cron, watchdog) : un client httpx
    laissé ouvert à chaque envoi fuiterait des connexions. On vérifie aussi que
    l'en-tête d'authentification ZeptoMail porte bien le préfixe littéral attendu.
    """
    monkeypatch.setenv("ZEPTOMAIL_TOKEN", "jeton-test")
    monkeypatch.setenv("ZEPTOMAIL_API_URL", "https://zeptomail.invalid/v1.1/email")
    crees: list[httpx.AsyncClient] = []
    requetes: list[httpx.Request] = []

    def _repondre(requete: httpx.Request) -> httpx.Response:
        requetes.append(requete)
        return httpx.Response(201, json={"message": "OK"})

    fabrique_reelle = httpx.AsyncClient

    def _fabrique(**kwargs: object) -> httpx.AsyncClient:
        client = fabrique_reelle(transport=httpx.MockTransport(_repondre), **kwargs)
        crees.append(client)
        return client

    monkeypatch.setattr(email_mod.httpx, "AsyncClient", _fabrique)

    assert await email_mod.envoyer_alerte("Inférence indisponible", "Détail") is True

    assert len(crees) == 1
    assert crees[0].is_closed is True
    assert str(requetes[0].url) == "https://zeptomail.invalid/v1.1/email"
    assert requetes[0].headers["Authorization"] == "Zoho-enczapikey jeton-test"


# --------------------------------------------------------------- routeurs HTTP


class _CacheSature:
    """Cache dont le quota par IP est toujours dépassé (rate-limit déclenché)."""

    async def hit_rate_limit(self, client_ip: str) -> bool:
        """Signale systématiquement le dépassement de quota."""
        return True


def test_demande_de_lien_magique_refusee_au_dela_du_quota(auth_client: TestClient) -> None:
    """Une IP qui dépasse le quota ne peut plus demander de lien magique (429).

    Anti-abus : sans ce plafond, l'endpoint d'envoi servirait de relais de spam vers
    des adresses arbitraires.
    """
    auth_client.app.dependency_overrides[get_cache_client] = _CacheSature

    reponse = auth_client.post("/v1/auth/request", json={"email": "paysan@cacao.ci"})

    assert reponse.status_code == 429
    assert "réessayer" in reponse.json()["detail"]


class _StoreSupprimeApresRenommage:
    """Dépôt simulant une suppression concurrente entre le renommage et la relecture."""

    async def renommer_session(
        self, session_id: str, titre: str, proprietaire: str | None = None
    ) -> bool:
        """Le renommage réussit (la session existait encore à cet instant)."""
        return True

    async def obtenir_session(
        self, session_id: str, proprietaire: str | None = None
    ) -> Session | None:
        """La session a disparu entre-temps (supprimée depuis un autre onglet)."""
        return None


def test_renommage_gagne_par_une_suppression_concurrente_renvoie_404(
    client: TestClient,
) -> None:
    """Si la session disparaît juste après le renommage, l'API renvoie 404, pas 500.

    Course réelle (deux onglets) : le renommage réussit puis la relecture ne trouve
    plus rien. On répond « session inconnue » plutôt que de tenter de sérialiser un
    ``None`` en réponse.
    """
    client.app.dependency_overrides[get_session_store] = _StoreSupprimeApresRenommage

    reponse = client.patch("/v1/sessions/sess-inexistante", json={"titre": "Séchage"})

    assert reponse.status_code == 404
    assert reponse.json()["detail"] == "Session inconnue."


def test_index_numpy_vide_ne_propose_aucun_passage() -> None:
    """Un index chargé sans aucune ligne ne propose rien plutôt que de lever.

    Filet complémentaire au chargement : même construit à vide, l'index répond par une
    liste vide — le RAG s'efface, il ne fabrique pas de voisin.
    """
    index = RagIndex([], [], np.zeros((0, 3), dtype=np.float32))

    assert index.taille == 0
    assert index.candidats([1.0, 0.0, 0.0], 3) == []
    assert index.vivier_hybride([1.0, 0.0, 0.0], "taille du cacaoyer", 3) == []
