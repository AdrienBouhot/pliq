# Les tests

Le troisième onglet d'une recipe, et une carte par colonne. Le tout part dans le
`schema.yml` voisin du modèle.

| | Ce que ça écrit |
|---|---|
| `not_null` | en un clic |
| `unique` | en un clic |
| **+ valeurs autorisées** | `accepted_values` |
| **+ relation** | `relationships` vers un autre modèle et sa colonne |
| **Sévérité** | *bloquant* (`error`) ou *avertissement* (`warn`), test par test |
| **Description** | la description de la colonne, qui ressort dans `dbt docs` |

Ces quatre tests sont les **tests génériques** de dbt : rien d'inventé ici, et
rien qui demande l'atelier pour tourner. `dbt test` les exécute en CI comme
n'importe quel autre.

## La sévérité

Un test *bloquant* fait échouer le build ; un test *en avertissement* le laisse
passer et se signale. C'est ce qui permet de poser un test sur une règle qu'on
sait imparfaitement respectée, sans bloquer la production le jour où on
l'ajoute.

## Les lignes en échec

Dans le panneau du [Flow](flow.md), **cliquer un test** montre les lignes qui le
font échouer.

dbt compile chaque test en une requête qui *sélectionne les échecs* : l'atelier
la rejoue, sans avoir besoin de `store_failures` ni de deviner quoi que ce soit.
La requête est affichée à côté, repliée — pour qu'on puisse la relire, et la
copier ailleurs.

## Ce qui n'est pas proposé

> [!note] Les tests **singuliers** (un fichier `.sql` dans `tests/`) et les tests des paquets externes (`dbt_utils`, `dbt_expectations`) ne s'éditent pas dans l'interface. Ils continuent de fonctionner s'ils sont déjà dans le projet, ils tournent avec les autres, et leurs lignes en échec sont consultables de la même façon.

Les **tests unitaires** de dbt (`unit_tests`) ne sont pas exposés.

## Tester autre chose qu'un modèle

L'onglet Tests d'une recipe porte sur le modèle qu'elle écrit. Pour un seed ou
une table de source, c'est la fiche **Documenter & tester** du Flow qui ouvre
les mêmes réglages : voir [Documenter](documenter.md).
