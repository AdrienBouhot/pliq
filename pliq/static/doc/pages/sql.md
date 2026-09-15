# Recipe SQL

Le modèle s'écrit à la main, Jinja compris. C'est la porte de sortie de
l'atelier : tout ce qu'une recipe visuelle ne sait pas faire s'écrit ici, et
reste un modèle dbt ordinaire.

## Ce qui marche dedans

- `ref()` et `source()`, résolus par dbt avec le manifest du projet ;
- vos **macros**, vos `var()`, vos paquets installés ;
- l'aperçu, exactement comme pour une recipe visuelle : le script est envoyé à
  `dbt show --inline`, sans qu'aucun fichier soit écrit.

## Un modèle écrit à la main est une recipe SQL

L'atelier n'exige pas qu'un modèle vienne de lui. Un `.sql` déjà présent dans le
projet s'affiche dans le Flow comme une recipe SQL, s'exécute, et son fichier
s'édite ici.

L'atelier **devine son type** — jointure, agrégat, union — en lisant son SQL,
pour lui donner la bonne icône. C'est une lecture, pas une décomposition.

> [!note] **Pas de rétro-ingénierie.** L'atelier ne décompose pas un modèle écrit à la main en étapes visuelles. C'est délibéré : mieux vaut un bloc honnête qu'une décomposition fausse.

## Le sens inverse : de la recipe visuelle au SQL

Le SQL d'un modèle **généré** par une recipe visuelle est en lecture seule dans
l'atelier : il est réécrit à chaque enregistrement de la recipe, qui est sa
source de vérité.

Pour qu'un modèle généré redevienne un modèle écrit à la main, il faut
**supprimer la recipe en gardant le SQL** : la case à décocher de la modale de
suppression laisse le `.sql` en place, et le modèle reste dans le Flow comme
recipe SQL. Voir [Supprimer une recipe](supprimer.md).

### Si le `.sql` a été modifié hors de l'atelier

Un script visuel **écrit** son fichier `.sql` : l'enregistrer le regénère
entièrement. L'atelier s'en aperçoit et prévient — « le SQL a été modifié hors
de l'atelier » — avant d'écraser quoi que ce soit. Pour garder la modification,
il faut la reporter dans le script, ou supprimer la recipe en gardant le SQL.

> [!attention] Une recipe SQL exécute ce que vous écrivez sur votre entrepôt, y compris à l'aperçu. C'est aussi ce qui rend un atelier joignable depuis le réseau dangereux : voir [Sur le réseau](reseau.md).
