# Documentation de Pliq

**Pliq** est un atelier ETL visuel posé sur **dbt Core**. La grammaire est celle
d'un ETL visuel — `dataset → recipe → dataset` — où une recipe est un nœud à
part entière, qui se configure dans une grille de données et non dans un
éditeur de texte.

Ce que vous construisez à la souris **est** un projet dbt : des fichiers `.sql`
et `.yml` normaux, versionnés, que `dbt build` exécute en intégration continue
sans l'atelier. Il n'y a pas de format propriétaire à exporter, pas de moteur
d'exécution à part, et rien à migrer le jour où l'atelier ne vous sert plus.

> [!note] Cette documentation est servie par l'atelier lui-même, à l'adresse `/doc`. Elle ne demande rien au réseau : elle s'ouvre aussi bien dans un train que devant un entrepôt de production.

## Par où commencer

| Vous voulez | Allez voir |
|---|---|
| installer Pliq et ouvrir un premier projet | [Installer](installer.md), puis [Lancer l'atelier](lancer.md) |
| comprendre l'écran principal | [Lire le Flow](flow.md) |
| transformer une table sans écrire de SQL | [Créer une recipe](recipes.md), puis [Préparer](preparer.md) |
| savoir ce que fait une étape précise | [La bibliothèque de processeurs](processeurs.md) |
| régler un modèle incrémental | [Les modèles incrémentaux](incremental.md) |
| savoir ce que l'atelier écrit sur le disque | [Ce qui est écrit sur le disque](fichiers.md) |

## Les sept chapitres

**[Prise en main](installer.md)** — installer Pliq dans le venv d'un projet dbt
ou en outil isolé, lancer l'atelier, le tenir sur la boucle locale, et gérer la
liste des projets connus.

**[Le Flow](flow.md)** — le graphe biparti de votre projet : lire ses formes et
ses couleurs, s'y déplacer, le tenir lisible quand il compte cent tables, et y
faire entrer une table de l'entrepôt.

**[Les recipes](recipes.md)** — les six façons de fabriquer un dataset à partir
d'un autre : préparer, joindre, grouper, empiler, dédoublonner, ou écrire le
SQL soi-même. Puis renommer et supprimer ce qu'on a fait.

**[Les processeurs](processeurs.md)** — les vingt-deux étapes d'une recipe
« Préparer », classées comme dans la bibliothèque de l'atelier, avec ce que
chacune demande et ce qu'elle écrit.

**[Écrire dans l'entrepôt](sortie.md)** — la matérialisation d'un modèle, les
stratégies incrémentales et leurs garde-fous, le microbatch, et les commandes
dbt lancées depuis la barre du haut.

**[Documenter et tester](tests.md)** — les tests génériques et leur sévérité,
les descriptions qui ressortent dans `dbt docs`, et la fraîcheur des sources.

**[Sous le capot](fichiers.md)** — les fichiers produits, la couverture exacte
de dbt, et les limites que l'atelier assume.

## Ce que Pliq ne fait pas

- **Il ne charge pas de données.** L'entrepôt est alimenté par ailleurs
  (Fivetran, Airbyte, un script) ; dbt transforme ce qui s'y trouve.
- **Il n'ouvre aucune connexion à l'entrepôt.** Toute lecture passe par
  l'adaptateur dbt de votre projet, via `dbt show` — voir
  [Ce que l'atelier couvre de dbt](couverture-dbt.md).
- **Il ne remplace pas le catalogue.** `dbt docs serve` reste ce qu'il est.
- **Il ne fait pas de rétro-ingénierie** d'un modèle écrit à la main : il
  l'affiche, l'exécute et l'édite, mais ne le décompose pas en étapes.
