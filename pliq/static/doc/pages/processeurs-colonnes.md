# Processeurs — Colonnes

Six processeurs qui changent la **forme** de la table : le nom des colonnes,
leur nombre, ou leur orientation.

## Renommer des colonnes

Renomme une ou plusieurs colonnes.

| Champ | Ce qu'on y met |
|---|---|
| **Renommages** | une ou plusieurs paires « ancien nom → nouveau nom » |

Le `.sql` produit garde les autres colonnes intactes. Attention à l'aval :
renommer une colonne lue par un autre modèle est signalé à l'enregistrement,
mais l'atelier ne réécrit pas les modèles d'aval — voir
[L'écran d'une recipe](recipe-ecran.md#ce-que-l-enregistrement-verifie-avant-d-ecrire).

## Supprimer / conserver des colonnes

Ne garde que certaines colonnes, ou en retire.

| Champ | Ce qu'on y met |
|---|---|
| **Action** | Supprimer, ou Conserver uniquement |
| **Colonnes** | celles que l'action désigne |

« Conserver uniquement » est la forme sûre quand la table d'entrée peut gagner
des colonnes : ce qui arrivera plus tard dans la source ne traversera pas le
modèle sans qu'on l'ait décidé.

## Concaténer des colonnes

Assemble plusieurs colonnes en une seule.

| Champ | Ce qu'on y met |
|---|---|
| **Colonnes à assembler** | dans l'ordre choisi |
| **Séparateur** | inséré entre deux valeurs |
| **Colonne de sortie** | le nom de la colonne créée |

## Découper une colonne

Découpe une colonne texte sur un séparateur.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne** | celle qu'on découpe |
| **Séparateur** | le texte sur lequel couper |
| **Nombre de morceaux** | combien de colonnes créer |

Le nombre de morceaux est demandé, et non deviné : une requête ne peut pas
inventer ses propres colonnes, et un modèle ne doit pas changer de schéma parce
qu'une valeur plus longue est apparue dans la source.

## Dépivoter (colonnes → lignes)

Replie plusieurs colonnes en deux : le nom, et la valeur. Douze colonnes
mensuelles deviennent douze lignes.

| Champ | Ce qu'on y met |
|---|---|
| **Colonnes à replier** | celles qui deviendront des lignes |
| **Colonne qui recevra les noms** | par défaut `variable` |
| **Colonne qui recevra les valeurs** | par défaut `valeur` |

L'écriture est une **union** — la seule qui se lise partout, `unpivot`
n'existant ni sur PostgreSQL ni sur Redshift.

> [!attention] Les colonnes repliées doivent porter le même genre de valeur : l'atelier refuse plutôt que d'empiler un texte sous un nombre.

## Pivoter (lignes → colonnes)

Déplie les valeurs d'une colonne en autant de colonnes. Une colonne par valeur
listée, une ligne par clé.

| Champ | Ce qu'on y met |
|---|---|
| **Une ligne par combinaison de** | les colonnes qui font la clé de sortie |
| **Colonne qui porte les noms** | celle dont les valeurs deviennent des colonnes |
| **Colonne qui porte les valeurs** | celle qui remplit les cases |
| **Valeurs à déplier** | la liste, donnée à la main |
| **Si plusieurs lignes tombent dans la même case** | en garder une (max), en garder une (min), les additionner, en faire la moyenne, les compter |
| **Préfixe des colonnes créées** | facultatif |

> [!note] Les valeurs sont données à la main, et c'est voulu : une requête ne peut pas inventer ses propres colonnes, et un modèle ne doit pas changer de schéma parce qu'une donnée nouvelle est apparue dans la source.

Le réglage des doublons dit le geste plutôt que la fonction : « en garder une
(max) » se comprend sans savoir ce que `max` fait d'un texte.
