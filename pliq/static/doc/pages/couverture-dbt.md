# Ce que l'atelier couvre de dbt

Pliq n'enveloppe pas dbt : il en expose une partie, et laisse le reste
accessible par les fichiers. Ce tableau dit où passe la frontière.

| Notion dbt | Dans l'atelier |
|---|---|
| Modèles, `ref()`, `source()` | recipes visuelles, ou SQL écrit à la main |
| `view` / `table` / `ephemeral` | [onglet Sortie](sortie.md) |
| `incremental` : stratégie, `unique_key`, `on_schema_change`, filtre | [onglet Sortie](incremental.md), avec garde-fous |
| Fenêtre de reprise pour les données tardives | [onglet Sortie](incremental.md#la-fenetre-de-reprise) ; refusée avec `append` |
| `microbatch` : `event_time`, `begin`, `batch_size`, `lookback` | [onglet Sortie](microbatch.md), validés comme dbt les valide |
| Seeds | documenter, tester, charger (`dbt seed`) ; pas de création |
| Sources | [déclarer depuis l'entrepôt](datasets.md), documenter, tester |
| Tests génériques + sévérité | `unique`, `not_null`, `accepted_values`, `relationships` |
| Descriptions table et colonnes | partout, y compris seeds et sources |
| Tags | modèles, seeds, sources |
| Lignes en échec d'un test | clic sur le test : l'atelier rejoue la requête que dbt a compilée |
| `dbt build / run / test / seed / compile` | [barre du haut](construire.md), avec `--select`, `--exclude`, `--full-refresh` |
| `dbt deps` | barre du haut, et une pastille quand les paquets manquent |
| `dbt source freshness` | barre du haut ; `loaded_at_field` et seuils dans la fiche d'une source ; pastille sur le Flow |
| Tests singuliers (`tests/*.sql`) | tournent s'ils existent, pas éditables ici |
| Paquets (`dbt_utils`, `packages.yml`) | installables ; leurs tests ne sont pas proposés dans l'interface |
| Snapshots | documentables et testables comme un modèle ; pas de création depuis l'atelier |
| Contrats, exposures, métriques | non |
| Doc blocks `{% docs %}`, `meta`, `persist_docs` | non |
| Macros, `vars`, hooks | utilisables dans une [recipe SQL](sql.md), pas d'écran dédié |
| Tests unitaires dbt (`unit_tests`) | non |
| Historique des exécutions | non — seul le dernier run est conservé |
| `dbt docs serve` | non — l'atelier ne remplace pas le catalogue |

## Les choix qui expliquent ce tableau

- **Aperçu sans rien écrire.** Le script est compilé, puis envoyé tel quel à
  `dbt show --inline` : c'est dbt qui résout `ref()`, `source()`, vos macros et
  vos `var()`, avec le manifest du projet. Aucun fichier n'est créé tant que
  vous n'enregistrez pas.
- **Aucune connexion à l'entrepôt.** Pliq n'ouvre jamais de connexion lui-même
  et n'importe aucun driver. Tout passe par l'adaptateur dbt du projet — ce qui
  rend l'atelier indépendant de l'entrepôt, et évite deux connexions
  concurrentes sur une base qui n'en accepte qu'une.
- **Échantillon, et contrôle complet.** La grille travaille sur un échantillon
  (200 lignes par défaut), comme tout ETL visuel. Ce qui est mesuré sur tout —
  le contrôle complet des colonnes, le diagnostic d'une jointure — le dit en
  toutes lettres et ne part que sur demande.
- **Répertoire courant.** L'atelier se place dans le dossier du projet dbt au
  démarrage : un adaptateur sur fichier résout un `path:` relatif depuis le
  répertoire courant du processus, pas depuis `--project-dir`.
- **Connexions dbt.** `reset_adapters()` n'est appelé qu'après un *run*, et à la
  bascule de projet : un run a pu changer le schéma, et DuckDB n'accepte qu'un
  écrivain. Les lectures gardent la connexion ouverte — sans quoi un aperçu de
  jointure rouvrirait l'entrepôt trois fois, soit une poignée de main
  d'authentification par frappe sur un entrepôt distant.
- **Colonnes d'entrée en cache.** Elles ne changent pas tant que le projet n'est
  pas reparsé : l'atelier compte ses parses et s'en sert comme clé. Un
  enregistrement reparse, donc rien de périmé ne peut être servi.
