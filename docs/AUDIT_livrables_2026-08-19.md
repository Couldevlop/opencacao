# Audit des livrables — ce qui décrédibiliserait une étude devant une commission

**Date** : 19 août 2026 · **Version auditée** : 0.6.89 (production)
**Méthode** : ouverture et analyse des six fichiers réellement produits par la chaîne
de production (`etudes_test_19082026/`), pas relecture du code. Chaque constat ci-dessous
a été **mesuré** dans le fichier, pas supposé.

Les trois études auditées : accès au métier d'exportateur, campagne 2025-2026,
déforestation Sud-Ouest / EUDR.

---

## Bloquant — un lecteur institutionnel referme le document

### B1. Les astérisques Markdown sont imprimés tels quels dans le Word

**Mesuré** : `52` occurrences de `**` dans `etude_exportateur.docx`.
Le modèle émet du gras Markdown (`**réforme de la commercialisation**`) et le rendu Word
l'écrit littéralement au lieu de le convertir en gras.

**Effet** : le document a l'air d'un brouillon collé depuis un chat. C'est le défaut le
plus visible et le plus immédiatement disqualifiant.

**Correction** : convertir `**…**` en `run.bold = True` dans `_paragraphe`
(`api/app/services/rendu/word.py`), et faire de même dans les diapositives et le tableur.
Le Markdown a un rendu, il ne doit jamais fuir en texte.

### B2. Le résumé est tronqué en plein mot

**Mesuré** : le résumé de l'étude « exportateur » se termine par
« …renforcer la position de la Côte d'I ».

**Effet** : une coupure au milieu d'un mot sur la première page utile suggère un
générateur qui déborde et qu'on n'a pas relu.

**Correction** : le plafond de tokens du résumé coupe la génération. Soit on augmente le
plafond, soit on tronque proprement sur une frontière de phrase — jamais au caractère.

### B3. Le PowerPoint n'a aucun design

**Mesuré** : thème `theme1.xml` par défaut de python-pptx — police **Calibri**, palette
d'accents **Office 2007** (`4F81BD`, `C0504D`, `9BBB59`…), et format **4:3**
(10 × 7,5 pouces). Aucune couleur ni typographie posée sur les diapositives.

**Effet** : un deck en 4:3 aux couleurs d'Office 2007 est daté de quinze ans. Il ne porte
aucune identité OpenCacao, alors que le Word en porte une (orange `EA5B13`).

**Correction** : passer en **16:9**, appliquer la charte du projet (orange `EA5B13`,
sombre `1F1F1F`, gris `606060`), poser une diapositive de titre travaillée, un bandeau
de section, et aligner la palette des graphiques sur celle du Word.

---

## Majeur — entame la crédibilité sans faire refermer le document

### M1. Le titre de couverture porte les fautes de la demande

**Mesuré** : « Étude de marché — l'exportation du cacao ivoirien : **acces au marche et
reglementation** ». Les accents manquants viennent du sujet saisi ; le système les
recopie tels quels en couverture.

**Correction** : ne jamais reprendre le sujet brut en titre. Le normaliser (capitalisation,
accents, ponctuation) ou faire porter le titre par le gabarit seul.

### M2. La date de génération est un horodatage machine

**Mesuré** : « Généré le `2026-08-19T21:04:29.675716+00:00` » sur la page de garde —
microsecondes et décalage UTC compris.

**Correction** : « Généré le 19 août 2026 ». L'horodatage précis reste au manifeste, où
il sert la rejouabilité ; il n'a rien à faire sur une couverture.

### M3. La section en lacune dit deux fois la même chose

**Mesuré** : « Section en lacune — aucune source mobilisable. » puis, juste en dessous,
« Aucune source mobilisable n'a été trouvé pour cette section… ».

**Correction** : une seule mention. Le doublon donne l'impression d'un gabarit mal assemblé
au moment précis où le document avoue une faiblesse — le pire endroit pour bafouiller.

### M4. Le sommaire est un texte figé

**Mesuré** : aucun champ `TOC`, **aucun numéro de page**, **aucun en-tête ni pied de page**,
**aucun style Word** (tout est mis en forme en direct).

**Effet** : impossible de naviguer, impossible de citer « page 4 » en réunion, et le
document ne se laisse ni re-styler ni intégrer dans une charte.

**Correction** : styles Word nommés (`Titre 1`, `Titre 2`, `Corps`), champ TOC réel,
pied de page avec numérotation et titre court.

### M5. Le contenu ne colle pas toujours au sujet demandé

**Mesuré** :
- étude « exportateur » : les mots **agrément** et **licence** n'apparaissent **jamais**.
  La question posée — comment devenir exportateur — reste sans réponse ;
- étude « déforestation » : l'EUDR est cité, mais **jamais le règlement 2023/1115**, ni
  aucune localité précise du Sud-Ouest ;
- étude « exportateur » : la section « Évolution documentée des prix » traite de
  **2015-2020** alors que le sujet porte sur l'accès au marché aujourd'hui.

**Correction** : cause racine en R1 ci-dessous. Un gabarit « accès au métier d'exportateur »
et l'entrée des textes réglementaires au corpus sont nécessaires en plus.

### M6. Une seule source porte 88 % du document

**Mesuré** : `ANADER 15 affirmations / 17`, soit **88,2 %** — l'étude « exportateur ».

**Ce n'est pas un défaut du logiciel** : le camembert le montre, c'est même son rôle.
Mais un document reposant à 88 % sur une source unique n'est pas une étude, et il vaut
mieux le savoir avant qu'un rapporteur ne le remarque.

**Correction** : diversifier le corpus (CNRA, Conseil du Café-Cacao, FIRCA, ICCO, textes
réglementaires) et envisager un seuil de diversité en dessous duquel le document se
déclare « note documentaire » plutôt qu'« étude ».

---

## Racine — ce qui explique la plupart des points ci-dessus

### R1. Les exigences de l'utilisateur n'atteignent jamais le prompt

`consigne_section(section, sujet)` ne reçoit que **le gabarit et 200 caractères de sujet**.
Tout le reste de la demande — destinataire, artefacts attendus, profondeur — est jeté à la
résolution d'intention.

**Demande explicite de Waopron (19/08)** : le destinataire doit être porté jusqu'au prompt
— **commission européenne, organisme d'État, opérateur économique, coopérative** — car
chacun attend un registre et une profondeur différents.

**Contrainte de sécurité** : le sujet est déjà assaini contre l'injection de consignes,
précisément parce qu'il entre dans le prompt. Le destinataire doit donc être résolu en
**vocabulaire fermé** (une valeur d'énumération choisie déterministement à partir de la
phrase), **jamais** en texte libre recopié.

**Travaux** : migration SQLite de la table `rapports` pour persister le destinataire ;
résolution dans `/v1/rapports/intention` ; fragments de prompt figés par destinataire.

### R2. Le prompt impose des sections courtes

`SYSTEM_PROMPT_REDACTION` : « **un seul paragraphe de 600 à 800 caractères** ». Les
sections font donc environ 1 000 signes quel que soit le sujet — une étude pour une
commission n'a pas cette forme.

**Correction** : longueur pilotée par le destinataire (R1), et autorisation de plusieurs
paragraphes pour les registres institutionnels.

### R3. Aucun tableau chiffré ne vient des outils

Le moteur assemblait `Document(..., tableaux=())`. Depuis 0.6.89 il produit le tableau de
la base documentaire, mais **aucune donnée métier** : ni série de prix, ni alertes de
déforestation, ni pluviométrie. Les outils Prix, Météo et Satellite existent pourtant et
sont déjà interrogés à la collecte — ils rendent des affirmations en prose, pas des séries.

**Correction** : faire rendre aux collecteurs des `Tableau` chiffrés en plus de leurs
affirmations, puis les tracer. La forme `LIGNES` est déjà livrée et testée pour cela.

### R4. Aucune demande ne se résout avec certitude

Les trois demandes réelles sont revenues `certaine: false` avec une liste de candidats.
Le comportement est voulu — une ambiguïté est une question, pas une panne — mais trois
demandes sur trois signale un lexique de résolution trop étroit.

---

## Ordre d'exécution proposé

| # | Chantier | Effet visible | Dépend de |
|---|---|---|---|
| 1 | B1 gras Markdown, B2 troncature, M2 date, M3 doublon | Immédiat, très visible | — |
| 2 | B3 refonte graphique du PowerPoint (16:9 + charte) | Immédiat, très visible | — |
| 3 | M4 styles Word, TOC, pieds de page | Fort | — |
| 4 | R1 destinataire jusqu'au prompt (+ M1, M5, R2) | Structurel | migration SQLite |
| 5 | R3 tableaux chiffrés depuis les outils | Structurel | collecteurs |
| 6 | M6 diversification du corpus, R4 lexique de résolution | Fond | corpus |

Les points 1 à 3 ne touchent que les adaptateurs de format : ils sont sans risque pour le
moteur et rendent les documents présentables. Les points 4 et 5 sont ceux qui feront la
différence entre une note documentaire et une étude.
