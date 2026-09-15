# La bibliothèque de processeurs

Un **processeur** est une étape d'une recipe [Préparer](preparer.md). Il y en a
vingt-deux, classés en sept familles, et c'est ce classement que la
bibliothèque affiche.

| Famille | Ce qu'on y trouve |
|---|---|
| [Colonnes](processeurs-colonnes.md) | renommer, supprimer/conserver, concaténer, découper, pivoter, dépivoter |
| [Formules](processeurs-formules.md) | formule SQL ; si/alors/sinon |
| [Filtrage](processeurs-filtrage.md) | sur une valeur, par formule, lignes vides |
| [Nettoyage](processeurs-nettoyage.md) | remplir les vides, rechercher & remplacer, transformer le texte, changer le type |
| [Nombres](processeurs-nombres.md) | arrondir |
| [Dates](processeurs-dates.md) | parser, extraire les composants |
| [Lignes](processeurs-lignes.md) | dédoublonner, dédoublonner par clé, fonction de fenêtre, trier |

## Ouvrir la bibliothèque

**+ Ajouter une étape**, dans l'en-tête du script. La bibliothèque s'ouvre sur
des **raccourcis vers les processeurs les plus utilisés** — formule,
si/alors/sinon, filtrer sur une valeur, remplir les cellules vides, rechercher &
remplacer, parser une date, dédoublonner par clé — puis liste les sept familles.

L'autre chemin, souvent plus court : **cliquer l'en-tête d'une colonne** dans la
grille. Le menu ne propose alors que les étapes pertinentes pour *cette*
colonne, déjà remplies avec son nom.

## Ce qu'une étape a en commun avec les autres

- Elle affiche **une phrase** qui dit ce qu'elle fait. Cette phrase est écrite
  une fois pour toutes côté serveur : c'est elle qu'on lit sur la carte, et elle
  qui part en commentaire au-dessus du CTE dans le `.sql`.
- Elle compte ce qu'elle change : `+` colonnes créées, `~` modifiées,
  `−` supprimées.
- Elle se **désactive** sans se supprimer, se **duplique**, se **déplace**, et
  se prévisualise à son rang avec l'**œil**.
- Les listes déroulantes qu'elle propose — opérateurs, types, fonctions de
  fenêtre — viennent du serveur, et dépendent de l'entrepôt ouvert : `varchar`
  n'existe pas sur BigQuery, et il n'est donc pas proposé.

## Ce que les étapes visuelles garantissent

Le SQL qu'elles écrivent est **traduit dans le dialecte de l'entrepôt** du
projet : DuckDB, PostgreSQL, Redshift, Athena/Trino, BigQuery et Snowflake.
Ailleurs, ce qui est portable passe et ce qui ne l'est pas est refusé avec de
quoi le remplacer, plutôt qu'écrit au hasard.

> [!attention] Une expression tapée dans **Formule** fait exception : elle part telle quelle. C'est du SQL de votre entrepôt, et l'atelier ne le traduit pas — il vérifie seulement qu'il est syntaxiquement valide, avec `sqlglot`.
