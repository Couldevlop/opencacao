"""Registre analytique des livrables (spec §8.2).

**Le corpus n'est pas en registre d'étude.** Les 10 000 paires de
``corpus_cacao_rag.jsonl`` sont intégralement en registre *conseil au producteur*
(« rendez-vous auprès de l'agent ANADER de votre zone »). Sollicitée sur une section
d'étude, la LoRA s'adresserait au producteur et renverrait vers l'ANADER — ce qui est
faux dans un document destiné à un bailleur.

**On redresse par le prompt système, pas par ``consigne``.** ``build_messages`` traite
``consigne`` comme un REMPLACEMENT du contexte RAG (voir sa docstring) : or une section
a besoin des deux, du contexte *et* du registre. ``system_prompt``, lui, compose. Le
traitement durable — 200 à 400 exemples de prose analytique ajoutés au corpus, puis un
rafraîchissement de LoRA — viendra après l'événement, sans changement de socle.

Le levier est la consigne, pas le plafond de tokens ; mais comme ailleurs dans ce
projet, on ne fait pas confiance au modèle pour la respecter : les garde-fous de
sortie s'appliquent aussi aux livrables.
"""

from __future__ import annotations

from app.services.gabarits import SectionGabarit

SYSTEM_PROMPT_REDACTION = (
    "Tu rédiges une section d'un document d'analyse sur la filière cacao ivoirienne, "
    "destiné à des institutions et des bailleurs.\n"
    "REGISTRE : prose analytique à la troisième personne. Ne t'adresse jamais au "
    "lecteur, n'emploie ni « vous » ni l'impératif, et ne renvoie jamais vers un agent "
    "ANADER — ce document ne s'adresse pas à un producteur.\n"
    "INTERDITS : n'invente jamais un chiffre, une date ni un pourcentage ; n'écris que "
    "ce que le contexte fourni établit. Ne nomme aucune maladie ni aucun ravageur. Ne "
    "propose aucun produit de traitement ni aucune dose. N'écris aucun titre, aucune "
    "puce, aucune numérotation : la structure du document est déjà fixée.\n"
    "FORME : un seul paragraphe de prose continue, de 600 à 800 caractères."
)


ENTETE_CONTEXTE_ANALYTIQUE = (
    "Voici les éléments sourcés à mobiliser pour cette section. N'écris que ce qu'ils "
    "établissent. Ne les présente pas comme des extraits, ne t'adresse pas au lecteur, "
    "et n'oriente vers aucun interlocuteur.\n\n"
    "{contexte}"
)

# Remplace « Question : » devant la demande. Le mot compte : il réinstallerait le
# registre questions-réponses que le prompt système vient précisément de quitter.
LIBELLE_SECTION = "Section à rédiger"


def consigne_section(section: SectionGabarit, sujet: str) -> str:
    """Construit la demande de rédaction d'une section.

    Args:
        section: Section déclarée par le gabarit.
        sujet: Sujet du document, repris dans la demande.

    Returns:
        La demande adressée au modèle pour cette section.
    """
    propre = f" {section.consigne}" if section.consigne else ""
    return f"Rédige la section « {section.titre} » d'un document portant sur {sujet}.{propre}"
