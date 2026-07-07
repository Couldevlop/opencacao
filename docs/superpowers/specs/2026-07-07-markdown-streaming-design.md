# Rendu markdown au fil de l'eau — design

**Date** : 2026-07-07 · **Statut** : validé (Waopron)

## Problème

Pendant le streaming, la bulle de réponse affiche le texte brut
(`corps.textContent`) : le gras arrive en `**brut**` et n'est rendu qu'à la
finalisation (`rendreMarkdown`). Constaté lors du test mobile v0.6.67.

## Décision (approche A)

Re-rendre le texte **accumulé** à chaque fragment : `append()` fait
`corps.innerHTML = rendreMarkdown(texte)`. Le backend émet des phrases
complètes (~20-40 fragments ≤ 2 Ko par réponse) : coût négligeable, zéro état
supplémentaire. Un parseur incrémental (blocs terminés seulement) a été écarté
— plus de code pour un gain invisible (YAGNI).

## Sécurité

Surface XSS inchangée : `rendreMarkdown` échappe TOUT le HTML d'abord
(`&<>"'`) puis n'applique qu'une liste blanche (`strong/em/ul/li/p`) — même
moteur et même garantie que la finalisation et les trois autres points
d'affichage de `chat-view.js` (OWASP A03, cf. en-tête de `markdown.js`).

## Curseur de streaming

Le `▋` clignotant passe de `.stream-corps.curseur::after` (tomberait seul
sous le dernier paragraphe, `::after` d'un conteneur de blocs) au dernier
bloc rendu : `> p:last-child::after` et `> ul:last-child > li:last-child::after`.
Cibler `:is(p, li):last-child` sans ancrage au conteneur créerait un double
curseur (le dernier `li` d'une liste en milieu de réponse matcherait aussi).
`white-space: pre-wrap` retiré (les sauts de ligne sont structurés en blocs).

## Cas limite assumé

Un `**` non encore fermé s'affiche littéralement puis bascule en gras dès que
la paire se ferme au fragment suivant — auto-correctif, rare (flux par phrases
complètes).

## Vérification

Pas d'infra de test JS : rejeu du test mobile puppeteer (émulation Pixel 5,
3G) avec interception réseau servant les fichiers modifiés sur la prod —
capture en cours de streaming : gras rendu pendant la frappe, curseur unique
en fin de dernier bloc. Backend et API inchangés.
