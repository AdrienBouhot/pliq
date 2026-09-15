# Empiler

Deux entrées ou plus, mises bout à bout.

## L'alignement se fait par nom

Les colonnes sont alignées **par nom**, et non par position. Un tableau montre
quelle colonne existe dans quel dataset, et ce qui manque d'un côté devient
`null`.

C'est le contraire d'un `union` écrit à la main, où l'ordre des colonnes fait
foi et où deux colonnes inversées passent sans erreur — pour peu qu'elles aient
des types compatibles.

## Tout garder, ou dédoublonner

| | Ce que ça écrit |
|---|---|
| **Tout garder** | `union all` |
| **Dédoublonner** | `union` |

`union all` est ce qu'on veut presque toujours : il ne compare rien, donc il ne
coûte rien de plus que la lecture. `union` dédoublonne sur **toutes** les
colonnes — pour une clé métier, c'est
[Dédoublonner par clé](processeurs-lignes.md#dedoublonner-par-cle) qu'il faut,
dans une recipe [Préparer](preparer.md).
