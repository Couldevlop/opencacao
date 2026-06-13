"""Couche domaine : entités, règles métier et ports.

Cette couche ne dépend d'aucun framework (FastAPI, Redis, httpx). Elle définit
les contrats (ports) que l'infrastructure implémente, conformément à la clean
architecture : les dépendances pointent vers l'intérieur.
"""
