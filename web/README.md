# Interface web OpenCacao

Interface de discussion légère (HTML/CSS/JS, **sans dépendance ni build**) qui
dialogue avec l'API OpenCacao (`POST /v1/chat`). Design épuré inspiré des
assistants modernes ; en-tête avec l'**armoirie de la Côte d'Ivoire** (gauche) et
le logo **OpenLab Consulting** (droite).

## Logos

| Emplacement | Fichier attendu | Statut |
|---|---|---|
| Extrême gauche | `web/assets/armoirie-ci.png` | **À ajouter** (armoiries de la République de Côte d'Ivoire) |
| Extrême droite | `web/assets/openlab.png` | ✅ fourni |

Tant que `armoirie-ci.png` est absent, un cadre gris discret s'affiche à sa place
(aucune image cassée). Dépose simplement le PNG officiel à ce chemin.

## Lancer en local

L'interface est statique. Sers-la avec n'importe quel serveur, par ex. :

```bash
cd web
python -m http.server 5173
```

Puis ouvre http://localhost:5173 et, via l'icône ⚙️ (en haut à droite), renseigne
l'**URL de l'API** (par défaut `http://localhost:8080`). Elle est mémorisée dans
le navigateur.

## CORS (important)

L'interface et l'API sont sur des origines différentes : autorise l'origine de
l'interface côté API, via la variable `CORS_ORIGINS` (cf. `.env`). Exemple :

```
CORS_ORIGINS=http://localhost:5173
```

Alternative : servir cette interface depuis la même origine que l'API (montage
statique FastAPI) pour éviter toute question de CORS.

## Ce que l'interface affiche

Pour chaque réponse, sous le texte :
- les **sources** citées (CNRA, ANADER, Conseil du Café-Cacao, FAO) ;
- un badge **« Voir un agent ANADER »** si la réponse redirige ;
- le **niveau de confiance** (faible / moyenne / élevée) ;
- le **disclaimer** obligatoire.

Les garde-fous métier (refus des dosages, etc.) sont appliqués **côté API** : si
l'API refuse et redirige, l'interface l'affiche tel quel. L'interface n'appelle
jamais le modèle directement.
