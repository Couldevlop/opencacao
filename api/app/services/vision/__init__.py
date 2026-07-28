"""Analyse visuelle des parcelles — cascade d'étages indépendants.

Étage 0 (``recevabilite``) est le seul étage livré par le chantier C1 : il ne mobilise
aucun modèle d'apprentissage. Les étages suivants (tri d'organe, localisation des
lésions, étiologie) relèvent du chantier C2.
"""

from __future__ import annotations
