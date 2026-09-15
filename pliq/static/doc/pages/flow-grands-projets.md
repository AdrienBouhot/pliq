# Quand il y a trop de tables

Trois leviers, du plus fort au plus léger : replier, suivre une lignée,
chercher.

## Replier

**Clic droit sur un dataset** du Flow :

| | Ce que ça replie |
|---|---|
| **Replier l'amont** | tout ce qui alimente ce nœud, de près ou de loin |
| **Replier l'aval** | tout ce qui est calculé à partir de lui |
| **Replier le dossier « marts »** | les datasets de `models/marts/`, où qu'ils soient dans le Flow |
| **Replier les seeds / les sources** | de même, pour ce qui n'a pas de dossier de modèles |

Dans les trois cas, le nœud sur lequel vous avez cliqué **reste visible** :
c'est lui qu'on regarde, c'est son voisinage qu'on range. Chaque entrée du menu
donne le nombre de datasets concernés, et son infobulle dit exactement ce qui
va disparaître.

Le tout devient **un seul bloc**, qui hérite des arêtes de ce qu'il cache et
annonce le pire statut de run de ses membres. Survoler un bloc en liste le
contenu ; **cliquer un bloc le déplie** ; et **Tout déplier** apparaît dans la
barre dès qu'il y a quelque chose de replié.

### Ce n'est pas un masquage

Le graphe est *contracté*, puis redisposé. Les colonnes, les profondeurs et le
contournement des arêtes valent pour ce qui est réellement affiché, et non pour
un graphe complet dont on aurait caché des morceaux.

Deux replis se recouvrent souvent — dans un DAG, deux marts partagent leurs
ancêtres : le premier demandé garde le nœud, pour qu'un dataset n'apparaisse
jamais deux fois.

> [!note] Le pliage est mémorisé par projet, dans votre navigateur. Il ne part donc pas dans le dépôt : c'est un confort d'affichage, pas une propriété du projet.

## Suivre une lignée

Avec **Lignée** actif (c'est le cas par défaut), cliquer un nœud n'éclaire que
son amont et son aval — tout le reste s'efface sans disparaître, la forme du
graphe reste lisible. Le bouton **Lignée** désactive ce comportement.

C'est la réponse à « qu'est-ce qui casse si je touche à cette table ? », sans
rien replier.

## Chercher

Le champ **chercher un dataset** ne garde en évidence que les datasets dont le
nom contient le texte saisi. `Entrée` centre le plan sur le premier résultat,
`Échap` vide le champ.
