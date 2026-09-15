# Processeurs — Lignes

Quatre processeurs qui portent sur les lignes : leur nombre, leur ordre, et ce
qu'une ligne sait de ses voisines.

## Dédoublonner

Ne garde qu'une ligne par combinaison de valeurs — le `distinct` du SQL, sur
**toutes** les colonnes. Aucun champ à régler.

Pour une clé métier, c'est le processeur suivant qu'il faut.

## Dédoublonner par clé

Une seule ligne par clé — la plus récente, par exemple. C'est ce que `distinct`
ne sait pas faire.

| Champ | Ce qu'on y met |
|---|---|
| **Colonnes de la clé** | ce qui identifie une entité (le client, la commande) |
| **Garder la ligne qui vient en premier selon** | une colonne et un sens de tri |
| **S'il reste des ex æquo** | n'en garder qu'une, ou les garder toutes |

Le tri est **obligatoire** : sans lui, la ligne retenue serait tirée au hasard,
et pas la même d'une exécution à l'autre.

> [!note] Les valeurs vides de la colonne de tri sont toujours reléguées en dernier. Sans `nulls last`, `desc` remonte les `NULL` en tête sur DuckDB, PostgreSQL et Redshift — et une date manquante passerait pour la plus récente.

## Fonction de fenêtre

Classement, cumul, valeur précédente, moyenne glissante.

| Champ | Ce qu'on y met |
|---|---|
| **Fonction** | voir le tableau ci-dessous |
| **Colonne de sortie** | le nom de la colonne créée |
| **Colonne mesurée** | pour les fonctions qui en ont besoin |
| **Fenêtre — une par combinaison de** | ce qui découpe les lignes : « par client », « par client et par mois » |
| **Ordre dans la fenêtre** | pour les fonctions de position |
| **De combien de lignes** | pour la valeur précédente ou suivante |
| **Sur combien de lignes** | pour la moyenne glissante |

### Les fonctions

| Fonction | Ce qu'elle rend |
|---|---|
| Numéro dans la fenêtre | `row_number` |
| Classement (ex æquo au même rang, rangs sautés) | `rank` |
| Classement (ex æquo au même rang, rangs suivis) | `dense_rank` |
| Valeur précédente / suivante | `lag` / `lead` |
| Cumul | somme qui s'accumule ligne à ligne |
| Moyenne glissante | sur les N dernières lignes |
| Somme, Moyenne, Minimum, Maximum, Nombre de lignes **de la fenêtre** | une valeur identique pour toute la fenêtre |

> [!attention] Les fonctions qui portent sur la fenêtre entière (somme, moyenne, minimum, maximum) **n'acceptent pas d'ordre** : avec un ordre, l'entrepôt en ferait un cumul, c'est-à-dire un total différent sur chaque ligne, sans que rien ne le dise. L'écran ne propose donc pas le champ.

Les champs affichés dépendent de la fonction choisie : c'est le serveur qui dit
lesquelles réclament une colonne mesurée et lesquelles réclament un ordre.

## Trier

Trie les lignes.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne** | celle qui commande l'ordre |
| **Décroissant** | une case à cocher |

> [!note] Un tri dans un modèle dbt n'a pas la portée qu'on lui prête : l'entrepôt ne garantit pas l'ordre d'une table matérialisée qu'on relit ensuite sans `order by`. Le tri sert surtout à ce qui suit dans la même requête — et à lire l'aperçu.
