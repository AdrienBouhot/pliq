# Processeurs — Nettoyage

Quatre processeurs qui corrigent **le contenu** d'une colonne, sans toucher au
nombre de lignes.

## Remplir les cellules vides

Remplace les valeurs vides par une valeur fixe.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne** | celle qu'on remplit |
| **Valeur de remplacement** | ce qui prend la place des vides |

> [!attention] Remplir les vides fait disparaître une information : après cette étape, plus rien ne distingue « la valeur était absente » de « la valeur valait ça ». Sur une mesure, un `0` de remplissage entre dans les moyennes ; c'est rarement ce qu'on veut.

## Rechercher & remplacer

Remplace un texte par un autre dans une colonne.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne** | celle qu'on corrige |
| **Rechercher** | le texte cherché |
| **Remplacer par** | ce qui le remplace |

## Transformer le texte

Majuscules, minuscules, ou suppression des espaces.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne** | celle qu'on transforme |
| **Transformation** | minuscules, MAJUSCULES, sans espaces autour, Capitalisé (1re lettre), minuscules sans espaces |

> [!note] « Capitalisé » ne met en majuscule que la première lettre de la **valeur**, pas celle de chaque mot.

« minuscules sans espaces » existe parce que c'est la paire qu'on applique
presque toujours ensemble avant un rapprochement : deux clés qui ne diffèrent
que par la casse ou un espace de fin ne se joignent pas.

## Changer le type

Convertit une colonne dans un autre type.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne** | celle qu'on convertit |
| **Nouveau type** | la liste vient de l'entrepôt ouvert |

Les types proposés sont ceux de **votre** entrepôt : `varchar` n'existe pas sur
BigQuery, `double` ni sur PostgreSQL ni sur Redshift. La liste est servie par le
serveur, qui la tient du dialecte du projet — elle ne peut donc pas proposer un
type que le `dbt build` refuserait.

Pour convertir un texte en date, c'est
[Parser une date](processeurs-dates.md#parser-une-date) qu'il faut : le format
y est explicite, et la conversion sûre là où l'entrepôt sait l'être.
