"""Questions fréquentes de la filière cacao (source unique).

Sert au **pré-chauffage du cache** : ces questions sont générées une fois (au
démarrage de l'API et/ou via ``scripts/prewarm_cache.py``) puis servies en
~0,2 s pendant 7 jours (TTL du cache), au lieu de ~20 s d'inférence CPU.

Aucune ne demande de dosage phytosanitaire (refusé par les garde-fous).
"""

from __future__ import annotations

QUESTIONS_FAQ: tuple[str, ...] = (
    "Quand récolter les cabosses de cacao ?",
    "Comment reconnaître une cabosse de cacao mûre ?",
    "Comment reconnaître la pourriture brune des cabosses ?",
    "Quelles sont les principales maladies du cacaoyer ?",
    "Comment reconnaître le swollen shoot du cacaoyer ?",
    "Comment lutter contre les mirides du cacaoyer ?",
    "Comment lutter contre les foreurs des tiges du cacaoyer ?",
    "Comment bien fermenter les fèves de cacao ?",
    "Combien de temps faut-il sécher les fèves de cacao ?",
    "Comment éviter le mauvais goût des fèves de cacao ?",
    "Comment conserver les fèves de cacao après séchage ?",
    "Quelle est la bonne densité de plantation pour le cacaoyer ?",
    "Quand planter les cacaoyers en Côte d'Ivoire ?",
    "Comment préparer une pépinière de cacaoyer ?",
    "Quels arbres d'ombrage associer au cacaoyer ?",
    "Comment gérer l'ombrage dans une cacaoyère adulte ?",
    "Comment tailler un cacaoyer ?",
    "Quand et comment greffer le cacaoyer ?",
    "Comment entretenir une jeune plantation de cacao ?",
    "Comment réhabiliter une vieille plantation de cacao ?",
    "Comment améliorer le rendement de ma cacaoyère ?",
    "Quelle variété de cacao planter en Côte d'Ivoire ?",
    "Comment reconnaître une carence en azote chez le cacaoyer ?",
    "Comment préparer le sol avant de planter le cacao ?",
    "Comment fertiliser une cacaoyère sans excès d'engrais ?",
    "Comment reconnaître une carence en potassium chez le cacaoyer ?",
    "Comment gérer les mauvaises herbes dans une cacaoyère ?",
    "Comment protéger les jeunes cacaoyers du plein soleil ?",
    "Comment lutter naturellement contre la pourriture brune des cabosses ?",
    "Comment lutter contre les chenilles défoliatrices du cacaoyer ?",
    "Comment prévenir le swollen shoot dans ma plantation de cacao ?",
    "Comment écabosser et trier les fèves après la récolte ?",
    "Comment stocker le cacao dans de bonnes conditions avant la vente ?",
    "Comment produire un cacao de qualité marchande ?",
    "Quand et comment recéper un vieux cacaoyer improductif ?",
    "Qu'est-ce que le règlement européen contre la déforestation (EUDR) pour le cacao ?",
    "Comment tracer et géolocaliser ma parcelle de cacao pour l'export ?",
    "Comment s'engager dans une production de cacao durable et certifiée ?",
    # Formulations EXACTES des suggestions de l'écran d'accueil (web/index.html) :
    # le cache est sensible au libellé, on les pré-chauffe telles quelles pour que
    # cliquer une suggestion réponde en ~0,2 s au lieu de ~20 s d'inférence CPU.
    "Comment réussir le séchage des fèves ?",
    "À quelle période tailler mes cacaoyers ?",
    "Comment prévenir la pourriture brune des cabosses ?",
)
