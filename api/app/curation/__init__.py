"""Console de curation (interne) : revue humaine des interactions → corpus.

Application ASGI **distincte** de l'API publique (`app.curation.main:app`),
déployée en ClusterIP hors Ingress (accès par port-forward). Elle relit le
journal anonymisé, priorise les retours 👎 / faible confiance, et permet de
valider/corriger une réponse versée au corpus d'entraînement (brique 3).
"""
