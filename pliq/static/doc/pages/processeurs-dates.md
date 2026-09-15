# Processeurs — Dates

Deux processeurs : l'un fait entrer une vraie date dans la table, l'autre en
sort ce dont on a besoin pour regrouper.

## Parser une date

Convertit un texte en vraie date.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne** | la colonne texte |
| **Format strptime (vide = auto)** | `%d/%m/%Y`, `%Y-%m-%d %H:%M:%S`… |
| **Colonne de sortie** | le nom de la colonne créée |

Le format est **traduit** dans celui de l'entrepôt : on écrit du strptime, et
l'atelier écrit ce que DuckDB, Snowflake ou BigQuery attendent.

> [!attention] Sur **Redshift et PostgreSQL**, une valeur illisible fait échouer l'exécution au lieu de rendre une case vide : ces entrepôts n'ont pas de conversion « sûre ». Filtrez les valeurs douteuses avant l'étape — un [filtre par formule](processeurs-filtrage.md#filtrer-avec-une-formule) suffit.

## Extraire les composants de date

Année, mois, jour, jour de la semaine.

| Champ | Ce qu'on y met |
|---|---|
| **Colonne** | la colonne date |
| **Composants** | année, mois, jour, trimestre, semaine, jour sem. |

Chaque composant coché devient une colonne. C'est ce qui prépare un regroupement
par mois ou par trimestre dans une recipe [Grouper](grouper.md).

### Le numéro du jour de la semaine n'est pas portable

Il suit l'entrepôt :

| Entrepôt | Convention |
|---|---|
| DuckDB, Redshift, PostgreSQL | 0 = dimanche |
| Athena | 1 = lundi |
| BigQuery | 1 = dimanche |
| Snowflake | dépend du paramètre de session `WEEK_START` — à sa valeur par défaut (0), dimanche vaut 0, et non 1 |

> [!attention] Si la numérotation compte pour vous, comparez plutôt le **nom** du jour, ou fixez la convention dans une colonne calculée. Un modèle qui change de sens en changeant d'entrepôt ne le dit jamais de lui-même.
