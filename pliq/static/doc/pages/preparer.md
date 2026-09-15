# Préparer

Une entrée, un script d'étapes empilées, une grille qui se recalcule à chaque
modification. C'est la recipe la plus employée, et celle qui ressemble le plus
à un tableur — à ceci près que chaque étape devient un CTE nommé dans le `.sql`.

## Ajouter une étape

Deux chemins :

- **cliquer l'en-tête d'une colonne** : le menu propose les étapes pertinentes
  pour *cette* colonne — une date propose « extraire les composants », un texte
  propose « mettre en minuscules », une colonne avec des trous propose
  « remplir les cellules vides » ;
- **+ Ajouter une étape** ouvre la bibliothèque de processeurs, avec ses
  raccourcis vers les plus utilisés.

La liste complète, classée : [La bibliothèque de processeurs](processeurs.md).

## La carte d'une étape

Chaque étape affiche ce qu'elle fait **en une phrase** — la même phrase qui
ira en commentaire au-dessus du CTE, dans le `.sql`.

Elle affiche aussi ses compteurs de colonnes (`+` créées, `~` modifiées, `−`
supprimées), et ses actions :

| | |
|---|---|
| **monter** / **descendre** | changer le rang de l'étape |
| **œil** | voir le résultat *à cette étape*, et non à la fin |
| **interrupteur** | désactiver l'étape sans la supprimer |
| **dupliquer** | la même étape, à régler autrement |
| **corbeille** | la retirer |

> [!astuce] L'**œil** suit le rang, pas l'étape : déplacer une étape prévisualisée garde l'aperçu sur la position qu'on regardait. C'est ce qu'on veut quand on cherche où une valeur se casse.

## L'ordre compte

Les étapes s'appliquent dans l'ordre, chacune sur le résultat de la précédente.
Filtrer avant de calculer n'est pas la même chose que calculer avant de
filtrer — et sur un entrepôt facturé au volume, ce n'est pas le même prix.

Le `.sql` produit le montre directement : un CTE par étape, nommé et commenté.
Voir [Ce qui est écrit sur le disque](fichiers.md).

## Ce qui est validé

Chaque expression saisie est validée par `sqlglot` **avant** d'être écrite : une
formule invalide est refusée avec un message, et non au moment du `dbt build`.

Le SQL écrit par les étapes visuelles est traduit dans le dialecte de l'entrepôt
du projet. Ce qui n'est pas portable est refusé avec de quoi le remplacer,
plutôt qu'écrit au hasard — voir [Limites actuelles](limites.md#les-dialectes).
Une expression tapée dans **Formule**, elle, part telle quelle : c'est du SQL de
votre entrepôt, l'atelier ne le traduit pas.
