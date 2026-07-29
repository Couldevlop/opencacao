"""Adaptateurs de rendu des livrables.

Un ``Document`` entre, un format sort. **Ces modules ne remontent jamais dans le
moteur** : ajouter un format ne touche pas une ligne de ``application/redaction.py``.

Chaque adaptateur porte les garanties propres à son format — l'échappement d'un pipe
en Markdown, la neutralisation d'une formule en Excel — et aucune ne fuit dans les
autres : préfixer une valeur par une apostrophe est correct pour un tableur et
corromprait un document Word.
"""
