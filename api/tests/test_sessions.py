"""Tests du dépôt SQLite des sessions de conversation (SessionStore)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.sessions import SessionStore
from app.models.domain import Canal, Langue


async def _store_pret(chemin: Path) -> SessionStore:
    store = SessionStore(chemin)
    await store.initialiser()
    assert store.pret is True
    return store


async def test_initialiser_cree_le_schema_et_la_version(tmp_path: Path) -> None:
    """L'initialisation crée les tables et fixe PRAGMA user_version."""
    chemin = tmp_path / "sessions.db"
    await _store_pret(chemin)

    with closing(sqlite3.connect(str(chemin))) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert version == 2  # migration 1 (schéma) + migration 2 (proprietaire, D1)
    assert {"sessions", "messages"} <= tables


async def test_initialiser_est_idempotent(tmp_path: Path) -> None:
    """Initialiser deux fois ne réapplique pas les migrations et ne perd rien."""
    store = await _store_pret(tmp_path / "sessions.db")
    session = await store.creer_session()
    await store.initialiser()  # second passage : aucune migration à rejouer
    assert await store.obtenir_session(session.id) is not None


async def test_creer_session_par_defaut(tmp_path: Path) -> None:
    """Une session créée porte un id, un titre par défaut et des horodatages."""
    store = await _store_pret(tmp_path / "sessions.db")
    session = await store.creer_session()
    assert session.id
    assert session.titre == "Nouvelle conversation"
    assert session.langue is Langue.FR
    assert session.canal is Canal.WEB
    assert session.cree_le == session.maj_le


async def test_obtenir_session_inconnue_retourne_none(tmp_path: Path) -> None:
    store = await _store_pret(tmp_path / "sessions.db")
    assert await store.obtenir_session("inexistant") is None
    assert await store.obtenir_session_avec_messages("inexistant") is None


async def test_ajouter_et_lister_messages_dans_l_ordre(tmp_path: Path) -> None:
    """Les messages sont rendus dans l'ordre d'insertion."""
    store = await _store_pret(tmp_path / "sessions.db")
    session = await store.creer_session()
    await store.ajouter_message(session.id, "user", "Quand récolter le cacao ?")
    await store.ajouter_message(session.id, "assistant", "Quand les cabosses sont mûres.")
    await store.ajouter_message(session.id, "user", "Et à quelle fréquence ?")

    messages = await store.lister_messages(session.id)
    assert [m.role for m in messages] == ["user", "assistant", "user"]
    assert messages[0].content == "Quand récolter le cacao ?"
    assert await store.compter_messages(session.id) == 3


async def test_ajouter_message_session_inconnue(tmp_path: Path) -> None:
    """Ajouter un message à une session inexistante renvoie None."""
    store = await _store_pret(tmp_path / "sessions.db")
    assert await store.ajouter_message("inexistant", "user", "bonjour") is None


async def test_ajouter_message_met_a_jour_maj_le(tmp_path: Path) -> None:
    """Le dernier message fait avancer maj_le (sert au tri de la liste)."""
    store = await _store_pret(tmp_path / "sessions.db")
    session = await store.creer_session()
    await store.ajouter_message(session.id, "user", "bonjour")
    rechargee = await store.obtenir_session(session.id)
    assert rechargee is not None
    assert rechargee.maj_le >= session.maj_le


async def test_lister_sessions_tri_par_activite(tmp_path: Path) -> None:
    """La liste classe les sessions de la plus récemment active à la plus ancienne."""
    store = await _store_pret(tmp_path / "sessions.db")
    a = await store.creer_session(titre="A")
    await store.creer_session(titre="B")
    # On active A après B : A doit repasser en tête.
    await store.ajouter_message(a.id, "user", "coucou")

    titres = [s.titre for s in await store.lister_sessions()]
    assert titres[0] == "A"
    assert set(titres) == {"A", "B"}


async def test_lister_sessions_pagination(tmp_path: Path) -> None:
    store = await _store_pret(tmp_path / "sessions.db")
    for i in range(5):
        await store.creer_session(titre=f"S{i}")
    page = await store.lister_sessions(limite=2, decalage=0)
    assert len(page) == 2


async def test_obtenir_session_avec_messages(tmp_path: Path) -> None:
    store = await _store_pret(tmp_path / "sessions.db")
    session = await store.creer_session()
    await store.ajouter_message(session.id, "user", "bonjour")
    detail = await store.obtenir_session_avec_messages(session.id)
    assert detail is not None
    assert detail.session.id == session.id
    assert len(detail.messages) == 1


async def test_renommer_session(tmp_path: Path) -> None:
    store = await _store_pret(tmp_path / "sessions.db")
    session = await store.creer_session()
    assert await store.renommer_session(session.id, "Séchage des fèves") is True
    rechargee = await store.obtenir_session(session.id)
    assert rechargee is not None
    assert rechargee.titre == "Séchage des fèves"


async def test_renommer_titre_vide_revient_au_defaut(tmp_path: Path) -> None:
    store = await _store_pret(tmp_path / "sessions.db")
    session = await store.creer_session(titre="Initial")
    await store.renommer_session(session.id, "   ")
    rechargee = await store.obtenir_session(session.id)
    assert rechargee is not None
    assert rechargee.titre == "Nouvelle conversation"


async def test_renommer_session_inconnue(tmp_path: Path) -> None:
    store = await _store_pret(tmp_path / "sessions.db")
    assert await store.renommer_session("inexistant", "x") is False


async def test_supprimer_session_cascade(tmp_path: Path) -> None:
    """Supprimer une session efface aussi ses messages (cascade)."""
    store = await _store_pret(tmp_path / "sessions.db")
    session = await store.creer_session()
    await store.ajouter_message(session.id, "user", "bonjour")

    assert await store.supprimer_session(session.id) is True
    assert await store.obtenir_session(session.id) is None
    assert await store.lister_messages(session.id) == []
    assert await store.supprimer_session(session.id) is False  # déjà supprimée


async def test_persistance_entre_instances(tmp_path: Path) -> None:
    """Les données survivent à la recréation du dépôt (durabilité sur disque)."""
    chemin = tmp_path / "sessions.db"
    store = await _store_pret(chemin)
    session = await store.creer_session(titre="Persistante")
    await store.ajouter_message(session.id, "user", "bonjour")

    autre = await _store_pret(chemin)  # nouvelle instance, même fichier
    rechargee = await autre.obtenir_session_avec_messages(session.id)
    assert rechargee is not None
    assert rechargee.session.titre == "Persistante"
    assert len(rechargee.messages) == 1


async def test_message_trop_long_rejete(tmp_path: Path) -> None:
    """Le contenu d'un message est borné (validation Pydantic, anti-abus)."""
    store = await _store_pret(tmp_path / "sessions.db")
    session = await store.creer_session()
    with pytest.raises(ValueError):
        await store.ajouter_message(session.id, "user", "x" * 2001)


async def test_initialiser_tolere_un_chemin_invalide(tmp_path: Path) -> None:
    """Un chemin impossible n'interrompt pas le démarrage : pret reste False."""
    fichier = tmp_path / "fichier"
    fichier.write_text("x", encoding="utf-8")  # un FICHIER là où on attend un dossier
    store = SessionStore(fichier / "sous" / "sessions.db")
    await store.initialiser()  # ne doit pas lever
    assert store.pret is False


def test_from_settings(tmp_path: Path) -> None:
    """La fabrique reflète le chemin et le plafond de messages configurés."""
    settings = Settings(sessions_db_path=str(tmp_path / "x.db"), sessions_max_messages=42)
    store = SessionStore.from_settings(settings)
    assert isinstance(store, SessionStore)
    assert store.max_messages == 42


# --- Identité par appareil (D1) ---


async def test_listage_cloisonne_par_appareil(tmp_path: Path) -> None:
    """Chaque appareil ne voit que ses propres conversations."""
    store = await _store_pret(tmp_path / "sessions.db")
    a = await store.creer_session(proprietaire="appareil-A")
    await store.creer_session(proprietaire="appareil-B")

    liste_a = await store.lister_sessions(proprietaire="appareil-A")
    assert [s.id for s in liste_a] == [a.id]
    assert await store.lister_sessions(proprietaire="appareil-A", decalage=0)  # non vide
    assert len(await store.lister_sessions(proprietaire="appareil-B")) == 1


async def test_acces_refuse_a_un_autre_appareil(tmp_path: Path) -> None:
    """Lire/renommer/supprimer la session d'un autre appareil échoue (cloisonnement)."""
    store = await _store_pret(tmp_path / "sessions.db")
    session = await store.creer_session(proprietaire="appareil-A")

    assert await store.obtenir_session(session.id, proprietaire="appareil-B") is None
    assert await store.obtenir_session(session.id, proprietaire="appareil-A") is not None
    assert await store.renommer_session(session.id, "X", proprietaire="appareil-B") is False
    assert await store.supprimer_session(session.id, proprietaire="appareil-B") is False
    # Le propriétaire, lui, peut agir.
    assert await store.renommer_session(session.id, "Mien", proprietaire="appareil-A") is True


async def test_acces_interne_sans_filtre_proprietaire(tmp_path: Path) -> None:
    """Sans proprietaire (None), l'accès interne (chat) ignore le cloisonnement."""
    store = await _store_pret(tmp_path / "sessions.db")
    session = await store.creer_session(proprietaire="appareil-A")
    assert await store.obtenir_session(session.id) is not None  # proprietaire=None


# --- Recherche plein-texte (C5) ---


async def test_recherche_par_titre_et_par_contenu(tmp_path: Path) -> None:
    """La recherche trouve par titre ET par contenu de message, scopée par appareil."""
    store = await _store_pret(tmp_path / "sessions.db")
    s_titre = await store.creer_session(titre="Séchage des fèves", proprietaire="A")
    s_msg = await store.creer_session(titre="Autre sujet", proprietaire="A")
    await store.ajouter_message(s_msg.id, "user", "Comment lutter contre la pourriture brune ?")
    await store.creer_session(titre="Séchage chez le voisin", proprietaire="B")

    par_titre = await store.rechercher_sessions("séchage", proprietaire="A")
    assert [s.id for s in par_titre] == [s_titre.id]  # la session de B est exclue
    par_contenu = await store.rechercher_sessions("pourriture", proprietaire="A")
    assert [s.id for s in par_contenu] == [s_msg.id]
    assert await store.rechercher_sessions("introuvable", proprietaire="A") == []
    assert await store.rechercher_sessions("   ", proprietaire="A") == []


async def test_recherche_echappe_les_jokers_sql(tmp_path: Path) -> None:
    """Les caractères LIKE (%, _) saisis sont cherchés littéralement, pas comme jokers."""
    store = await _store_pret(tmp_path / "sessions.db")
    cible = await store.creer_session(titre="Remise 100%", proprietaire="A")
    await store.creer_session(titre="Sans rapport", proprietaire="A")
    resultats = await store.rechercher_sessions("100%", proprietaire="A")
    assert [s.id for s in resultats] == [cible.id]
