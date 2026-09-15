# Créer une recipe

Depuis le [Flow](flow.md) : **cliquez un dataset**, puis choisissez une recipe
dans le panneau de droite. Le dataset cliqué devient l'entrée.

## Les six types

| Type | Entrées | Ce qu'il fait |
|---|---|---|
| **Préparer** | 1 | un script d'étapes empilées sur une grille — voir [Préparer](preparer.md) |
| **Joindre** | 2+ | rapprocher deux datasets sur une clé — voir [Joindre](joindre.md) |
| **Grouper** | 1 | des clés de regroupement et des mesures — voir [Grouper](grouper.md) |
| **Empiler** | 2+ | mettre bout à bout des datasets alignés par nom de colonne — voir [Empiler](empiler.md) |
| **Dédoublonner** | 1 | un raccourci vers une recipe Préparer d'une seule étape |
| **SQL** | 1+ | le modèle s'écrit à la main, Jinja compris — voir [Recipe SQL](sql.md) |

**Joindre** demande le second dataset dans une modale ; l'atelier **devine la
clé** d'après les noms de colonnes, puis vous réglez le type de jointure et les
colonnes retenues.

> [!astuce] **Dédoublonner** retire les lignes identiques sur *toutes* leurs colonnes. Pour « une ligne par client, la plus récente », c'est **Préparer** avec l'étape [Dédoublonner par clé](processeurs-lignes.md#dedoublonner-par-cle).

## Où va le modèle ? La couche est suggérée

À la création, « Ranger dans » propose une couche selon la nature des entrées —
la convention dbt, pas une règle de l'outil :

| Entrées | Couche proposée | Pourquoi |
|---|---|---|
| une source ou un seed | `staging` | une vue par table brute, renommée et typée |
| des modèles de `staging` | `intermediate` | une étape de calcul, pas encore un objet métier |
| des modèles déjà retravaillés | `marts` | un objet métier, exposable |

La raison est affichée sous le champ, et la matérialisation suit ce que
`dbt_project.yml` impose à cette couche (`staging` → `view`, `marts` → `table`).
Tout reste modifiable : c'est une proposition, pas une contrainte.

## Ce que la recipe produit

Deux fichiers : le modèle `.sql` — la source de vérité, lisible et relisible —
et le script visuel `.pliq/recipes/<nom>.yml`, hors des `model-paths`. Voir
[Ce qui est écrit sur le disque](fichiers.md).
