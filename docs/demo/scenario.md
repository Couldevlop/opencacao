# Scénario de démonstration — OpenCacao V3

> **Ce document est un squelette à remplir par Waopron.** La structure, les contraintes
> techniques et les pièges connus sont écrits ; le contenu — les questions exactes, le
> déroulé, la parcelle choisie — relève de vous. Un scénario écrit par quelqu'un qui ne
> montera pas sur scène ne tient pas trente secondes.
>
> **Critère d'acceptation (spec §9.6) : le scénario complet joué en production, sans
> intervention, deux fois de suite.** Répété *en production*, pas en local.

---

## 0. Contraintes qui déterminent le déroulé

Elles ne sont pas négociables et doivent façonner le scénario, pas le subir.

| Contrainte | Conséquence sur le déroulé |
|---|---|
| Une génération prend des dizaines de secondes | Ne pas enchaîner deux questions lourdes sans transition parlée |
| Cloudflare coupe vers 100 s | Toute réponse longue passe par le flux (`/chat/stream`), jamais le synchrone |
| Une seule génération à la fois | Ne pas lancer une étude et une question de chat en parallèle |
| Le cache est invalidé par `APP_VERSION` | **Le pré-chauffage est la DERNIÈRE opération avant d'entrer en scène** |
| Les drapeaux V3 sont à `false` | Les activer et les vérifier la veille, pas le matin |

---

## 1. Préparation, la veille

- [ ] Bascule GPU exécutée et chronométrée (runbook §2), retour CPU vérifié
- [ ] Drapeaux activés : `PARCELLES_ENABLED`, `VISION_ENABLED`, `RAPPORTS_ENABLED`
- [ ] Budget de latence de la vision tranché (runbook §3)
- [ ] Parcelle de démonstration créée, avec ses photos déposées
- [ ] Scénario joué **en entier**, en production, deux fois
- [ ] Plan de secours hors-ligne produit (runbook §5, palier 4)

## 2. Préparation, juste avant d'entrer en scène

```bash
# LE PRE-CHAUFFAGE EN DERNIER : un roll-image.sh posterieur invalide tout le cache,
# car APP_VERSION entre dans la cle.
python scripts/prewarm_cache.py            # questions du scénario ci-dessous
curl -s https://opencacao.openlabconsulting.com/v1/ready
```

- [ ] Aucun déploiement depuis le pré-chauffage — **si un déploiement a lieu, refaire
      le pré-chauffage**

---

## 3. Déroulé

Remplir une ligne par temps de la démonstration. La colonne « repli » est celle qu'on
lit sous pression : elle doit être remplie même quand tout va bien.

| # | Temps | Ce qui est montré | Question / action exacte | Réponse attendue | Agent(s) mobilisé(s) | Repli si échec |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |

### Le moment de scène (spec §8.7)

Faire générer **en direct** le PPTX d'une étude de filière — la présentation que
l'assemblée regarde. Le flux écrit les sections à l'écran, puis le fichier se
télécharge.

- Sujet de l'étude : ……
- Gabarit : `etude_filiere`
- Durée attendue : ……  *(à mesurer en répétition — une étude enchaîne une génération
  par section)*
- Repli : le fichier pré-généré, ouvert depuis la machine de présentation

> **À vérifier en répétition, pas en scène :** une étude sur un sujet hors filière ou
> portant un dosage est **refusée** — c'est voulu. Le sujet choisi doit passer les
> garde-fous. Le vérifier une fois, à l'avance.

---

## 4. Ce qu'il ne faut PAS montrer

Écrit noir sur blanc pour qu'aucune improvisation ne s'y aventure.

- **Aucune demande de dosage phytosanitaire.** Le système refuse et redirige vers
  l'ANADER — c'est un bon message, mais il se raconte, il ne s'improvise pas.
- **Aucun diagnostic de maladie sur photo.** La cascade produit un *constat*, jamais un
  diagnostic : les étages d'étiologie ne sont pas livrés, délibérément.
- **Le dossier de parcelle n'est pas une attestation EUDR.** Il porte la mention
  « document préparatoire ». Ne pas le présenter comme une conformité.
- **Aucune autre culture que le cacao.** Le vivrier et l'anacarde sont redirigés.

## 5. Ce qui mérite d'être dit, si l'occasion se présente

Trois points que la démonstration montre sans les expliquer, et qui font la différence
devant un public technique :

- **La provenance.** Chaque livrable dit d'où vient chacun de ses chiffres, et embarque
  de quoi être rejoué : modèle, version, extraits mobilisés avec leur empreinte.
- **Le constat de lacune.** Quand une source manque, le document le **dit** au lieu
  d'estimer. C'est un choix, pas une limite.
- **La boucle humaine.** Chaque constat visuel part en revue ANADER ; la correction de
  l'agent alimente le jeu de données. Le système s'améliore parce qu'il est utilisé.
