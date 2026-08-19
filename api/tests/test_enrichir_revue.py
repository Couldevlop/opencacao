"""Le cron nocturne découvre et alerte — il n'indexe plus tout seul.

Incident du 19/08/2026. Le document officiel `Politique-LCB-FT-FIRCA.pdf` — une
politique de conformité anti-blanchiment, parfaitement lisible et parfaitement
étrangère à l'agronomie — a été indexé, et il a **capté la question « quel est le
rôle du FIRCA ? »** : la réponse est passée de trois sources et une confiance élevée
à un exposé sur le blanchiment de capitaux.

Ce jour-là quelqu'un regardait. La nuit, personne ne regarde : le CronJob enchaînait
découverte **puis** constitution, sans aucun filtre humain. N'importe quel PDF publié
par une source officielle — rapport financier, charte interne, politique qualité —
entrait donc dans l'index tout seul. Or l'inspection des quatre sites montre qu'ils
publient un mélange d'agronomie et d'administratif, et que le second est majoritaire
chez certains.

La correction ne consiste pas à filtrer plus finement : deviner ce qui est
« agronomique » à partir d'un PDF est précisément ce qu'on ne sait pas faire de façon
fiable. Elle consiste à **remettre l'humain dans la boucle** — la file de revue de la
console existe pour ça — et à le prévenir qu'il a du travail.
"""

from __future__ import annotations

import pytest

from app.curation import enrichir


class _Jobs:
    """Registre de jobs en mémoire, qui retient les types créés."""

    def __init__(self, decouverts: int = 0) -> None:
        self.types: list[str] = []
        self._decouverts = decouverts

    def reconcilier_orphelins(self) -> int:
        return 0

    async def creer(self, type_: str, details: dict | None = None) -> dict:
        self.types.append(type_)
        return {"id": f"job-{type_}"}

    async def obtenir(self, job_id: str) -> dict | None:
        return {"id": job_id, "details": {"telecharges": self._decouverts}}


class _Pipeline:
    """Pipeline factice qui retient les étapes réellement exécutées."""

    def __init__(self) -> None:
        self.etapes: list[str] = []

    async def collecter_sources(self, job_id: str) -> None:
        self.etapes.append("recherche")

    async def decouvrir_sources(self, job_id: str) -> None:
        self.etapes.append("decouverte")

    async def constituer_rag(self, job_id: str) -> None:
        self.etapes.append("constitution")


@pytest.fixture
def brancher(monkeypatch):
    """Branche des doubles et collecte les alertes envoyées."""

    def _brancher(decouverts: int):
        jobs, pipeline, alertes = _Jobs(decouverts), _Pipeline(), []
        monkeypatch.setattr(enrichir.JobsRegistry, "from_env", classmethod(lambda cls: jobs))
        monkeypatch.setattr(
            enrichir.PipelineService, "from_env", classmethod(lambda cls, j: pipeline)
        )

        async def _alerte(sujet: str, texte: str) -> None:
            alertes.append((sujet, texte))

        monkeypatch.setattr(enrichir.email, "envoyer_alerte", _alerte)
        return jobs, pipeline, alertes

    return _brancher


async def test_le_cron_n_indexe_plus_tout_seul(brancher) -> None:
    """Le cœur du correctif : plus aucune constitution automatique."""
    _, pipeline, _ = brancher(decouverts=3)

    await enrichir.executer()

    assert "constitution" not in pipeline.etapes


async def test_le_cron_continue_de_chercher_et_de_decouvrir(brancher) -> None:
    """Contre-épreuve : un correctif qui désarmerait tout le cron passerait le test
    précédent sans rien apporter. La collecte, elle, ne coûte rien et ne risque rien."""
    _, pipeline, _ = brancher(decouverts=3)

    await enrichir.executer()

    assert pipeline.etapes == ["recherche", "decouverte"]


async def test_des_documents_decouverts_declenchent_une_alerte(brancher) -> None:
    """Sans alerte, les documents s'empileraient sans que personne ne le sache : on
    aurait remplacé une indexation aveugle par un corpus qui ne grandit plus."""
    _, _, alertes = brancher(decouverts=3)

    await enrichir.executer()

    assert len(alertes) == 1
    sujet, texte = alertes[0]
    assert "3" in texte
    assert "console" in texte.lower()


async def test_sans_decouverte_aucune_alerte(brancher) -> None:
    """Une alerte quotidienne « rien à faire » finit par ne plus être lue."""
    _, _, alertes = brancher(decouverts=0)

    await enrichir.executer()

    assert alertes == []
