"""La fiche doit être bâtie AVANT la troncature de la fenêtre glissante.

Défaut constaté en production le 19/08 : ``DialogueSessionService`` borne l'historique
à 8 messages, puis l'orchestrateur en tirait la fiche. Les faits du 1er tour étaient
donc déjà perdus quand on cherchait à s'en souvenir — la mémoire ne marchait que dans
le chemin sans session, celui que les tests d'intégration exerçaient.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.dialogue_session import DialogueSessionService
from app.models.domain import Langue
from app.models.session import Session


class _ConseilEspion:
    """Capture la mémoire reçue par le cas d'usage de conseil."""

    def __init__(self) -> None:
        self.fiche_recue = None

    async def conseiller(self, question, langue, client_ip, historique=None, fiche_producteur=None):
        from app.domain.entities import Conseil
        from app.models.domain import Confiance

        self.fiche_recue = fiche_producteur
        return Conseil("Taillez en fin de saison sèche.", Confiance.ELEVEE, [])

    async def conseiller_stream(
        self, question, langue, client_ip, historique=None, fiche_producteur=None
    ):
        self.fiche_recue = fiche_producteur
        yield {"type": "token", "text": "Taillez."}
        yield {"type": "done"}


class _Message:
    def __init__(self, role: str, content: str) -> None:
        self.role, self.content = role, content


class _SessionsLongues:
    """Dépôt de session portant un fil bien plus long que la fenêtre glissante."""

    def __init__(self) -> None:
        self.messages = [_Message("user", "Je suis à Soubré, ma plantation a 15 ans")]
        for i in range(12):
            self.messages.append(_Message("assistant", f"Réponse {i}."))
            self.messages.append(_Message("user", f"Question sans rapport {i} ?"))

    async def lister_messages(self, session_id: str):
        return self.messages

    async def obtenir_session(self, session_id: str):
        maintenant = datetime.now(UTC)
        return Session(id=session_id, titre="Discussion", cree_le=maintenant, maj_le=maintenant)

    async def ajouter_message(self, session_id: str, role: str, content: str) -> None:
        self.messages.append(_Message(role, content))

    async def renommer_session(self, session_id: str, titre: str) -> None:
        return None


@pytest.mark.asyncio
async def test_la_fiche_survit_a_la_troncature_de_la_fenetre() -> None:
    """Une localité citée au 1er tour parvient encore au modèle au 13e."""
    conseil = _ConseilEspion()
    service = DialogueSessionService(conseil, _SessionsLongues())
    await service.conseiller("À quelle période tailler ?", Langue.FR, "1.2.3.4", session_id="s1")
    assert conseil.fiche_recue is not None
    assert conseil.fiche_recue.localite == "Soubré"
    assert conseil.fiche_recue.age_ans == 15


@pytest.mark.asyncio
async def test_la_fiche_survit_a_la_troncature_en_flux() -> None:
    """Même garantie sur /chat/stream, le chemin réel du navigateur."""
    conseil = _ConseilEspion()
    service = DialogueSessionService(conseil, _SessionsLongues())
    async for _ in service.conseiller_stream(
        "À quelle période tailler ?", Langue.FR, "1.2.3.4", session_id="s1"
    ):
        pass
    assert conseil.fiche_recue is not None
    assert conseil.fiche_recue.localite == "Soubré"
    assert conseil.fiche_recue.age_ans == 15


# --- La fiche fournie en amont prime sur celle qu'on extrairait du fil tronqué ---


def test_le_bloc_utilise_la_fiche_fournie_plutot_que_le_fil_tronque() -> None:
    """Le fil transmis a perdu « Soubré » ; la fiche bâtie en amont le sait encore."""
    from app.application import conseil_commun
    from app.services.fiche import Fiche

    tronque = [{"role": "user", "content": "Question sans rapport ?"}]
    bloc = conseil_commun.memoire_du_fil(
        "Et donc ?", tronque, True, Fiche(localite="Soubré", age_ans=15)
    )
    assert "Soubré" in bloc
    assert "15 ans" in bloc


def test_la_salutation_utilise_la_fiche_fournie_pour_rouvrir_le_fil() -> None:
    """Dire bonjour sur une longue conversation rappelle ce dont on parlait."""
    from app.application import conseil_commun
    from app.services.fiche import Fiche

    texte = conseil_commun.civilite_ou_none(
        "Bonjour", [], True, Fiche(localite="Soubré", sujet="symptome")
    )
    assert texte is not None
    assert "Soubré" in texte


def test_le_repli_ignore_meme_une_fiche_fournie() -> None:
    """Drapeau bas : une fiche pleine ne doit RIEN injecter (repli strict)."""
    from app.application import conseil_commun
    from app.services.fiche import Fiche

    pleine = Fiche(localite="Soubré", age_ans=15, superficie_ha=3.0, sujet="symptome")
    assert conseil_commun.memoire_du_fil("Et donc ?", [], False, pleine) == ""
    assert conseil_commun.civilite_ou_none("Bonjour", [], False, pleine) is None


# --- La clarification ne redemande pas ce que la fiche sait déjà ---


def test_les_faits_connus_se_resument_en_une_phrase() -> None:
    """La fiche sait se dire au modèle, pour qu'il cesse de redemander."""
    from app.services.fiche import Fiche, faits_connus

    phrase = faits_connus(Fiche(localite="Soubré", age_ans=15, superficie_ha=3.0))
    assert "Soubré" in phrase
    assert "3 ha" in phrase
    assert "15 ans" in phrase


def test_une_fiche_vide_ne_dit_rien() -> None:
    """Sans fait connu, aucune phrase — donc aucune consigne parasite."""
    from app.services.fiche import Fiche, faits_connus

    assert faits_connus(Fiche()) == ""


def test_la_consigne_de_clarification_interdit_de_redemander_le_connu() -> None:
    """Constaté en production : « où se trouve votre exploitation ? » alors qu'il le savait."""
    from app.services import clarification

    consigne = clarification.consigne_theme(
        "fertilisation", besoin_localite=False, deja_connu="il se trouve à Soubré"
    )
    assert "Soubré" in consigne
    assert "redemand" in consigne.lower()


def test_la_consigne_reste_inchangee_sans_fait_connu() -> None:
    """Rien de connu : la consigne est exactement celle d'avant (repli neutre)."""
    from app.services import clarification

    assert clarification.consigne_theme("fertilisation", besoin_localite=False) == (
        clarification.consigne_theme("fertilisation", besoin_localite=False, deja_connu="")
    )


@pytest.mark.asyncio
async def test_la_clarification_ne_redemande_pas_la_localite_deja_connue() -> None:
    """La consigne envoyée au modèle porte les faits acquis et n'exige plus la ville."""
    from app.application import conseil_commun
    from app.services.fiche import Fiche

    class _Espion:
        def __init__(self) -> None:
            self.consigne = ""

        async def generer(self, question, **kw) -> str:
            self.consigne = str(kw.get("consigne") or "")
            return "Quel type de sol ?"

    espion = _Espion()
    await conseil_commun.question_clarification(
        espion,
        "fertilisation",
        "Dois-je fertiliser cette année ?",
        [],
        Fiche(localite="Soubré", age_ans=15, superficie_ha=3.0),
    )
    assert "Soubré" in espion.consigne
    assert "redemandez" in espion.consigne
    assert "dans quelle localité" not in espion.consigne
