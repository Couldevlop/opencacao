# Interface web OpenCacao

Interface de discussion (HTML/CSS/JS **sans dépendance ni build**) qui dialogue
avec l'API OpenCacao (`POST /v1/chat`). Design épuré inspiré des assistants
modernes ; en-tête avec l'**armoirie de la Côte d'Ivoire** (gauche) et le logo
**OpenLab Consulting** (droite).

## Architecture (Clean Architecture)

Le code (`src/`) suit une séparation en couches, dépendances pointant **vers
l'intérieur** (le DOM et `fetch` restent en périphérie) :

```
src/
  domain/         models.js          # entités + règles pures (Confiance, ConseilError,
                                     #   validation, normalisation) — aucune dépendance
  application/    conseil.js         # cas d'usage « demanderConseil » (valide + délègue)
  infrastructure/ api-client.js      # adaptateur HTTP : fetch /v1/chat, HTTP -> domaine
  ui/             chat-view.js       # présentation (rendu DOM)
                  markdown.js        # rendu markdown minimal et sûr
  main.js         (composition root) # câble client -> cas d'usage -> vue + événements
```

- **Le domaine ne dépend de rien.** L'application reçoit le client par injection
  (port) ; on peut donc tester/échanger l'API sans toucher au métier.
- **L'UI ne connaît ni `fetch` ni l'API** : elle ne manipule que des entités.

## Sécurité (recommandations OWASP)

- **A03 — Injection / XSS** : toute donnée externe est posée en `textContent` ;
  la seule insertion HTML (réponse du modèle) passe par un rendu markdown qui
  **échappe d'abord tout le HTML**, puis n'autorise que gras/italique/listes.
- **CSP** : `Content-Security-Policy` stricte en `<meta>` (`script-src 'self'`,
  pas de JS/CSS inline, `object-src 'none'`, `frame-ancestors 'none'`,
  `base-uri 'none'`). Aucun gestionnaire d'événement inline (`onerror`, etc.).
- **Aucune dépendance tierce / CDN** : 100 % local (souveraineté + surface
  d'attaque réduite, pas de risque de chaîne d'approvisionnement).
- **Aucun secret côté client** : seule l'URL de l'API est stockée (localStorage).
- **Validation** : longueur de question bornée (3–2000) côté UI **et** API.
- **En déploiement**, servez l'interface en **HTTPS** et ajoutez au serveur les
  en-têtes `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` et
  `Strict-Transport-Security` (les en-têtes ne se posent pas via `<meta>`).
  Restreignez `connect-src` à l'origine réelle de l'API.

## Logos

| Emplacement | Fichier attendu | Statut |
|---|---|---|
| Extrême gauche | `web/assets/armoirie-ci.png` | **À ajouter** (armoiries de Côte d'Ivoire) |
| Extrême droite | `web/assets/openlab.png` | ✅ fourni |

Si `armoirie-ci.png` est absent, un cadre gris discret s'affiche (géré en JS,
sans image cassée). Dépose le PNG officiel à ce chemin.

## Lancer en local

Les **modules ES** imposent un service HTTP (pas d'ouverture en `file://`) :

```bash
cd web
python -m http.server 5173
```

Ouvre http://localhost:5173, puis via ⚙️ renseigne l'**URL de l'API** (défaut
`http://localhost:8080`, mémorisée dans le navigateur).

## CORS

Interface et API étant sur des origines différentes, autorise l'origine de
l'interface côté API via `CORS_ORIGINS` (cf. `.env`), par ex.
`CORS_ORIGINS=http://localhost:5173`. Ou sers l'interface depuis la même origine
que l'API.

## Ce que l'interface affiche

Sous chaque réponse : **sources** citées, badge **« Voir un agent ANADER »** si
redirection, **niveau de confiance**, et le **disclaimer**. Les garde-fous
métier (refus des dosages…) sont appliqués **côté API** ; l'interface n'appelle
jamais le modèle directement.
