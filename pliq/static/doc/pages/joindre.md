# Joindre

Deux entrées ou plus. Le panneau de gauche montre une carte par dataset ajouté,
et le premier dataset est le **principal** : ce sont ses lignes qui commandent.

## Les trois réglages

| Réglage | Ce que ça fait |
|---|---|
| **Type** | `inner`, `left`, `right`, `full`, `cross` — chacun expliqué en clair dans la liste |
| **Clés** | une ou plusieurs paires « colonne de gauche = colonne de droite », combinées par `and` |
| **Colonnes retenues** | soit `a.*, b.*`, soit une sélection avec renommage |

La clé est **devinée** au premier affichage : deux colonnes du même nom, c'est
presque toujours la bonne. L'atelier le signale — « clé devinée d'après les noms
de colonnes, vérifiez-la » — et elle reste modifiable.

Une jointure `cross` n'a pas de clé : elle produit **toutes les combinaisons**
des deux datasets, et l'écran le dit avant qu'on s'en aperçoive sur la facture.

## Deux garde-fous sur les noms

C'est là qu'on se trompe, et `dbt build` ne pardonne pas :

- si une colonne existe des deux côtés (`customer_id`, presque toujours),
  l'atelier prévient que la table de sortie aurait deux colonnes du même nom et
  que le build échouerait ; passer en « Choisir les colonnes » suffixe
  automatiquement le doublon (`customer_id_stg_customers`) ;
- deux colonnes de sortie qui portent le même nom sont **refusées** à la
  compilation, avec le nom des deux coupables.

La comparaison est insensible à la casse, comme l'entrepôt : `id` et `ID` sont
une collision.

## Vérifier la jointure

**Vérifier la jointure** répond à la question que le SQL ne pose jamais : ce
qu'elle fait aux *lignes*. Les contrôles ci-dessus attrapent ce que `dbt build`
aurait refusé ; celui-ci attrape ce qu'il aurait accepté sans broncher.

| Ce qu'il dit | Exemple |
|---|---|
| la multiplication | « cette jointure multiplie les lignes de `stg_orders` par 3 » |
| la cardinalité | « relation de N à 1 : `stg_customers` est unique sur `customer_id` » |
| les lignes sans correspondance | « 12 lignes sur 500 n'ont pas de correspondance : elles disparaissent, la jointure est `inner` » |
| les clés vides | « 3 lignes de `stg_customers` ont une clé vide » |

Les deux côtés sont mesurés, pas seulement celui qu'on rapporte : avec `right`
ou `full`, c'est l'autre qui commande. Les clés vides sont exclues du compte de
multiplication — `NULL` ne s'apparie à rien, et mille lignes sans clé
annonceraient une explosion qui n'aura pas lieu.

> [!attention] Le contrôle porte sur les **tables entières**, et c'est pour ça qu'il est à la demande : un contrôle d'unicité sur deux cents lignes d'échantillon n'en est pas un, et chaque mesure est un agrégat complet. Si la jointure change après le contrôle, l'atelier le dit et demande de le relancer.
