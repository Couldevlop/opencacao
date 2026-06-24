"""Tests du service de dialogue avec mémoire serveur : fenêtre + titre (B2/B3, V2).

Exerce :class:`DialogueSessionService` directement (avec un conseil enregistreur et
un vrai dépôt SQLite temporaire), pour vérifier le bornage du contexte transmis au
modèle et l'auto-titrage, sans passer par la couche HTTP.
"""

from __future__ import annotations

from pathlib import Path

from app.application.dialogue_session import DialogueSessionService
from app.core.sessions import SessionStore
from app.domain.entities import Conseil
from app.models.domain import Confiance, Langue


class _ConseilEnregistreur:
    """Conseil factice : mémorise l'historique reçu et renvoie une réponse fixe."""

    def __init__(self) -> None:
        self.historiques: list[list[dict[str, str]]] = []

    async def conseiller(
        self,
        question: str,
        langue: Langue,
        client_ip: str,
        historique: list[dict[str, str]] | None = None,
    ) -> Conseil:
        self.historiques.append(list(historique or []))
        return Conseil("Étalez vos fèves au soleil.", Confiance.ELEVEE, [])


async def _store(tmp_path: Path) -> SessionStore:
    store = SessionStore(tmp_path / "sessions.db")
    await store.initialiser()
    return store


async def test_contexte_borne_par_la_fenetre_et_le_resume(tmp_path: Path) -> None:
    """Après plusieurs tours, le contexte transmis = résumé + fenêtre récente."""
    store = await _store(tmp_path)
    conseil = _ConseilEnregistreur()
    service = DialogueSessionService(conseil, store, fenetre=4, seuil_resume=8)
    session = await store.creer_session()

    for i in range(7):
        await service.conseiller(f"Question {i} ?", Langue.FR, "ip", session_id=session.id)

    # Au dernier tour, 12 messages étaient déjà stockés (6 tours * 2) : ils sont
    # condensés. Le contexte reçu = 1 résumé + 4 messages récents.
    dernier_contexte = conseil.historiques[-1]
    assert len(dernier_contexte) == 5
    assert dernier_contexte[0]["role"] == "assistant"
    assert dernier_contexte[0]["content"].startswith("Résumé de nos échanges")
    # Tous les tours restent persistés intégralement (la fenêtre ne borne que le prompt).
    messages = await store.lister_messages(session.id)
    assert len(messages) == 14  # 7 tours complets


async def test_titre_auto_au_premier_tour(tmp_path: Path) -> None:
    """Le premier tour fixe le titre de la session depuis la question."""
    store = await _store(tmp_path)
    service = DialogueSessionService(_ConseilEnregistreur(), store)
    session = await store.creer_session()

    await service.conseiller(
        "Comment bien sécher mes fèves ?", Langue.FR, "ip", session_id=session.id
    )
    rafraichie = await store.obtenir_session(session.id)
    assert rafraichie is not None
    assert rafraichie.titre == "Comment bien sécher mes fèves"


async def test_titre_auto_ne_change_plus_apres_le_premier_tour(tmp_path: Path) -> None:
    """Le titre est figé au 1er tour : les questions suivantes ne le réécrivent pas."""
    store = await _store(tmp_path)
    service = DialogueSessionService(_ConseilEnregistreur(), store)
    session = await store.creer_session()

    await service.conseiller(
        "Comment tailler le cacaoyer ?", Langue.FR, "ip", session_id=session.id
    )
    await service.conseiller("Et le séchage des fèves ?", Langue.FR, "ip", session_id=session.id)

    rafraichie = await store.obtenir_session(session.id)
    assert rafraichie is not None
    assert rafraichie.titre == "Comment tailler le cacaoyer"


async def test_titre_manuel_jamais_ecrase(tmp_path: Path) -> None:
    """Une session nommée par l'utilisateur conserve son titre."""
    store = await _store(tmp_path)
    service = DialogueSessionService(_ConseilEnregistreur(), store)
    session = await store.creer_session(titre="Mon séchage à Daloa")

    await service.conseiller(
        "Comment tailler le cacaoyer ?", Langue.FR, "ip", session_id=session.id
    )

    rafraichie = await store.obtenir_session(session.id)
    assert rafraichie is not None
    assert rafraichie.titre == "Mon séchage à Daloa"


async def test_session_inconnue_retourne_none(tmp_path: Path) -> None:
    store = await _store(tmp_path)
    service = DialogueSessionService(_ConseilEnregistreur(), store)
    assert await service.conseiller("Bonjour ?", Langue.FR, "ip", session_id="inconnu") is None
