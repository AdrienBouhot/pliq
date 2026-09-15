# Limites actuelles

Ce que l'atelier ne sait pas faire, et pourquoi. Une limite annoncée coûte moins
cher qu'une fonction qui ment.

## Ce que l'aperçu ne dit pas

- Le **nombre de lignes** d'une table n'est pas affiché dans l'inventaire de
  l'entrepôt : chaque entrepôt le range ailleurs et il n'existe pas de requête
  portable. Le nombre de colonnes, lui, vient d'`information_schema`.
- Les **types de colonnes** de l'aperçu sont ceux qu'agate rend à dbt
  (`INTEGER`, `VARCHAR`, `DECIMAL`, `DATE`…), plus grossiers que les types
  déclarés de l'entrepôt : `int` et `bigint` s'y confondent.
- Le **contrôle complet** s'arrête à soixante colonnes : au-delà, la requête
  d'agrégation devient elle-même un problème. Les colonnes écartées sont
  nommées, et la raison distinguée d'un type que l'atelier ne sait pas agréger.

## Ce que l'atelier ne décompose pas

**Pas de rétro-ingénierie.** Un modèle écrit à la main s'affiche comme une
[recipe SQL](sql.md), s'exécute, et son `.sql` s'édite dans l'atelier — mais
l'atelier ne le décompose pas en étapes. C'est délibéré : mieux vaut un bloc
honnête qu'une décomposition fausse.

Le SQL d'un modèle *généré* par une recipe, lui, reste en lecture seule : il est
réécrit à chaque enregistrement de la recipe, qui est sa source de vérité.

## L'analyse d'impact avertit sans réparer

Elle ne regarde que les modèles qui *nomment* la colonne, et seulement ceux qui
lisent directement le modèle modifié. Un `select *` n'est pas signalé : il ne
casse pas, sa sortie change de forme. Ce qui vient plus loin ne casse que si le
voisin laissait passer la colonne — et ça, on ne peut pas le savoir sans mentir.

Renommer une colonne **ne réécrit aucun modèle d'aval** : il faut les ouvrir.
Voir [L'écran d'une recipe](recipe-ecran.md#ce-que-l-enregistrement-verifie-avant-d-ecrire).

## Le renommage ne suit pas tout

Un `ref()` construit depuis une variable ou une macro, et les objets de
l'entrepôt qui nomment la table en dur — vue métier, tableau de bord, export —
ne sont pas suivis. La table déjà construite n'est pas renommée non plus : dbt
en crée une nouvelle à côté. Voir [Renommer un modèle](renommer.md).

## Les dialectes

Le SQL écrit par les étapes visuelles est traduit dans le dialecte de l'entrepôt
du projet — DuckDB, PostgreSQL, Redshift, Athena/Trino, BigQuery et Snowflake.
Ailleurs, ce qui est portable passe (filtres, jointures, agrégats) et ce qui ne
l'est pas est refusé avec de quoi le remplacer, plutôt qu'écrit au hasard.

Une expression tapée dans [Formule](processeurs-formules.md#formule), elle, part
telle quelle : c'est du SQL de votre entrepôt.

Deux écarts connus :

- la **médiane** est approchée sur Athena et BigQuery, qui n'en ont pas
  d'exacte ; le SQL généré le dit en commentaire ;
- sur Redshift et PostgreSQL,
  [Parser une date](processeurs-dates.md#parser-une-date) fait **échouer**
  l'exécution sur une valeur illisible au lieu de rendre une case vide.

## Ce qui n'existe pas encore

- Les **contrats** de modèle (`contract: {enforced: true}`) et les **snapshots**
  ne sont pas exposés.
- Les tests **singuliers** et ceux des paquets externes ne sont pas éditables
  dans l'interface — ils tournent, et leurs échecs sont consultables.
- Pas d'**historique des exécutions** : seul l'état du dernier run est gardé,
  celui que dbt écrit dans `run_results.json`.
- Pas de réordonnancement des étapes par glisser-déposer, ni de groupes
  d'étapes.
- L'atelier ne sait **créer** que des projets DuckDB. Pour un autre entrepôt :
  `dbt init`, puis « Ajouter un dossier existant ».
- Un projet neuf déclenche un avertissement dbt (« unused configuration
  paths ») tant qu'une couche est vide : c'est dbt qui signale une configuration
  sans ressource, ça disparaît au premier modèle rangé dedans.
- Pas encore de **Git** dans l'interface (branche, diff, PR).
- Interface en **thème clair** uniquement, comme les ETL visuels dont elle
  s'inspire.
- **Aucune authentification**, et c'est assumé : voir
  [Sur le réseau](reseau.md). L'atelier est un outil de poste de travail.

## Les tests de l'interface

L'interface n'a **pas de tests dans un vrai navigateur**. Ce qui se vérifie
automatiquement est pour l'essentiel statique — syntaxe de chaque module, routes
appelées, identifiants d'éléments, accord avec les listes du serveur — plus
quelques fonctions réellement exécutées sous node. Ni les événements DOM, ni le
focus, ni les parcours clavier ne sont couverts : ils se vérifient en ouvrant
l'atelier.
