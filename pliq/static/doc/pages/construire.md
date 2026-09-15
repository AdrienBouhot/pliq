# Construire avec dbt

La barre du haut lance dbt, et montre ce qu'il fait.

| | |
|---|---|
| **Commande** | `build`, `run`, `test`, `seed`, `compile`, `source freshness`, `deps` |
| **Sélecteur** | ce que dbt doit construire (`--select`) — vide signifie tout le projet |
| **Exclure** | ce qu'il doit laisser de côté (`--exclude`) |
| **full-refresh** | reconstruit les modèles incrémentaux de zéro (`--full-refresh`) |
| **Journal** | la sortie de dbt, en direct |

Le bouton de lancement porte la commande choisie : **dbt build**.

## Pendant un run

Les pastilles des nœuds du [Flow](flow.md) suivent l'exécution en direct : c'est
le WebSocket de l'atelier qui les met à jour. Si la liaison est coupée, une
pastille **suivi en direct interrompu** le dit — l'état affiché peut alors être
en retard.

> [!note] dbt n'accepte **qu'une invocation à la fois**. Pendant un build, une lecture qui aurait besoin de dbt est refusée tout de suite (409) plutôt que de l'attendre en silence, et une écriture refuse avant de toucher au disque — dbt lit ces fichiers-là pendant qu'il construit. Ce qui ne passe pas par dbt (le journal, l'état des nœuds, le projet ouvert) continue de répondre : c'est justement ce qu'on regarde pendant un build.

## Construire un seul nœud

Le panneau d'un dataset et celui d'une recipe portent un bouton **Construire** :
c'est le sélecteur rempli pour vous. Et **Enregistrer & exécuter**, dans
l'éditeur d'une recipe, écrit les fichiers puis lance `dbt build` sur le modèle
produit.

## `dbt deps`

Un projet qui déclare des paquets ne parse pas tant qu'ils ne sont pas
installés. La pastille **dépendances à installer** apparaît alors dans la barre,
et l'installation part d'un clic.

`deps` ne part jamais avec un sélecteur : il installe des paquets, il ne
sélectionne pas des nœuds.

## `source freshness`

La mesure de fraîcheur des sources, qui n'a rien à voir avec un build : voir
[La fraîcheur des sources](fraicheur.md).

## Ce qui n'est pas gardé

Il n'y a **pas d'historique des exécutions** : l'atelier garde l'état du dernier
run, celui que dbt écrit dans `run_results.json`. Comparer deux exécutions
demanderait un stockage que l'atelier n'a pas.
