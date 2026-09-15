# Renommer un modèle

**Clic droit sur un dataset → Renommer…**, le bouton **Renommer** de son
panneau — c'est lui, la table — ou celui de l'en-tête de l'éditeur, où le nom
affiché est déjà celui du modèle produit. Le modèle garde sa couche : seul son
nom change.

L'entrée n'apparaît que sur un **modèle** : un seed tient son nom de son fichier
CSV, une source celui qu'elle porte dans l'entrepôt, et un bloc replié n'est pas
un dataset.

> [!note] L'action n'est pas sur la recipe, et c'est voulu : une recipe n'a pas de nom à elle. Elle porte celui du modèle qu'elle écrit, et son script s'appelle `.pliq/recipes/<ce nom>.yml` — renommer le dataset renomme donc le script avec.

## Les cinq endroits qu'un nom occupe

Un modèle dbt porte son nom à cinq endroits, et n'en changer qu'un laisse un
projet qui parse encore mais ne se construit plus. L'atelier les suit tous d'un
coup :

| | Ce qui est réécrit |
|---|---|
| Le modèle | `<couche>/<ancien>.sql` devient `<couche>/<nouveau>.sql` — le SQL, lui, ne bouge pas d'une ligne |
| Le script visuel | `.pliq/recipes/<ancien>.yml` devient `<nouveau>.yml`, et le `name:` qu'il porte suit |
| La doc et les tests | l'entrée du YAML qui documente le modèle, à sa place, avec ses commentaires |
| Ce qui le lit | les `ref()` des modèles, tests singuliers, analyses, macros, snapshots — et les cibles `to: ref(…)` des tests de relation |
| Les recipes d'aval | l'entrée du script visuel qui désigne le modèle |

Un `ref()` qui nomme un autre paquet — `ref('dbt_utils', 'commandes')` — n'est
pas touché : ce n'est pas le même modèle. Un `ref()` construit au vol depuis une
variable non plus ; mieux vaut le laisser intact que de le réécrire de travers.

## La modale dit ce qui bougerait avant d'écrire

Elle liste les fichiers qui suivront, et refuse tout de suite un nom déjà pris
par un modèle, un seed, un snapshot ou un script visuel orphelin — dbt refuse
deux ressources du même nom, autant ne pas l'apprendre au parse suivant.

## Ce que le renommage ne fait pas

> [!attention] Il ne touche pas à l'entrepôt. La table déjà construite garde l'ancien nom, le prochain `dbt build` en crée une sous le nouveau, et l'ancienne reste. La modale la nomme ; c'est à vous de la retirer. Pliq ne parle à l'entrepôt qu'en lecture, et par dbt.

Il ne suit pas non plus ce que l'atelier ne peut pas lire : un `ref()` construit
depuis une variable ou une macro, et les objets de l'entrepôt qui nomment la
table en dur — vue métier, tableau de bord, export.

Un **seed**, une **source** et un **snapshot** ne se renomment pas ainsi, et
l'atelier dit pourquoi : un seed tient son nom de son fichier CSV, une source du
nom qu'elle porte dans l'entrepôt, et renommer un snapshot déplacerait
l'historique déjà collecté.
