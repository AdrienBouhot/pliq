# Grouper

Une entrée, des clés de regroupement, des mesures.

## Les clés

Des chips : chaque colonne ajoutée devient une clé de regroupement.

**Aucune clé** signifie une seule ligne en sortie, tous totaux confondus. C'est
valide, et parfois exactement ce qu'on veut — le chiffre d'affaires total, le
nombre de clients.

## Les mesures

Une ligne par mesure :

| | |
|---|---|
| **Fonction** | `count`, `count distinct`, `sum`, `avg`, `min`, `max`, `median` |
| **Colonne** | celle qu'on mesure |
| **Nom de sortie** | proposé automatiquement (`total_revenue_eur`), modifiable |
| **Condition** *(facultative)* | une condition SQL, qui devient un `filter (where …)` |

La condition facultative est ce qui remplace un « compter seulement les
commandes payées » : au lieu d'une seconde recipe filtrée puis jointe, une
mesure conditionnelle dans la même agrégation.

> [!note] La **médiane** est approchée sur Athena et BigQuery, qui n'en ont pas d'exacte. Le SQL généré le dit en commentaire, pour que personne ne l'apprenne d'un écart de deux centimes six mois plus tard.

## Ce qu'il faut regarder après

Une agrégation change la granularité : la table de sortie n'a plus une ligne par
commande mais une ligne par client, par mois, par ce que vous avez choisi. C'est
le bon moment pour poser un test `unique` sur la clé — voir
[Les tests](tests.md).
