"""L'état des colonnes au fil d'un script, et le SQL d'une étape.

Une recipe Préparer est une suite d'étapes, chacune un CTE. `ColumnState`
porte ce que les colonnes sont devenues à cet endroit du script ; `StepSql`
porte ce que l'étape écrit, avant qu'on en fasse du texte.
"""

from __future__ import annotations

from typing import Callable

from .errors import RecipeError

# ------------------------------------------------------------------ état colonnes


class ColumnState:
    """Colonnes disponibles à un instant du script, et leur type connu."""

    def __init__(self, columns: list[str], types: dict[str, str] | None = None):
        self.columns = list(columns)
        self.types = dict(types or {})

    def copy(self) -> "ColumnState":
        return ColumnState(self.columns, self.types)

    def type_of(self, col: str) -> str | None:
        return self.types.get(col)

    def require(self, col: str, step_label: str) -> str:
        if col not in self.columns:
            raise RecipeError(
                f"{step_label} : la colonne « {col} » n'existe pas à cette étape. "
                f"Colonnes disponibles : {', '.join(self.columns) or '(aucune)'}."
            )
        return col


def _check_unique_columns(before: list[str], after: list[str], label: str) -> None:
    """Refuse qu'une étape fabrique deux colonnes du même nom.

    Le compilateur tient l'état des colonnes pour les étapes suivantes ; il
    doit donc décrire ce que l'entrepôt exécutera vraiment. Renommer « a » en
    « b » quand « b » existe déjà compilait sans un mot : le compilateur
    annonçait `['b', 'b']`, DuckDB rendait `['b', 'b_1']`, et l'étape d'après
    relisait la première des deux — la seconde valeur disparaissait sans que
    rien ne le dise. La jointure refuse déjà ce cas ; il manquait ici.

    La comparaison ignore la casse : tous les entrepôts visés confondent
    `b` et `B` sur un identifiant nu, et c'est ainsi que le compilateur les
    écrit. Mais seul un doublon *que l'étape crée* est refusé — une entrée qui
    porte déjà deux noms que seule la casse sépare vient de l'entrepôt, et ce
    n'est pas à une étape de renommage de la rejeter.

    Un échange `a ↔ b` passe : la vérification porte sur l'état d'arrivée, où
    chaque nom n'apparaît toujours qu'une fois.
    """

    def tally(cols: list[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in cols:
            out[c.casefold()] = out.get(c.casefold(), 0) + 1
        return out

    before_counts = tally(before)
    for key, n in tally(after).items():
        if n > 1 and before_counts.get(key, 0) < n:
            names = [c for c in after if c.casefold() == key]
            what = (
                f"« {names[0]} »"
                if len(set(names)) == 1
                else " et ".join(f"« {c} »" for c in dict.fromkeys(names))
            )
            raise RecipeError(
                f"{label} : deux colonnes de sortie s'appelleraient {what}. "
                f"L'entrepôt en renommerait une au passage, et les étapes "
                f"suivantes reliraient la mauvaise. Choisissez un autre nom"
                + (
                    ", ou retirez d'abord la colonne qui porte déjà celui-là."
                    if before_counts.get(key)
                    else "."
                )
            )


class StepSql:
    """Ce qu'une étape produit : une projection, un filtre, un distinct.

    Deux échappatoires, pour les étapes qui ne tiennent pas dans une simple
    projection :

    * `window` calcule des expressions de fenêtre dans une sous-requête, que
      `window_where` filtre ensuite depuis l'extérieur. C'est la seule forme
      portable : `qualify` n'existe que sur DuckDB, Snowflake et BigQuery, et
      une fonction de fenêtre ne se met pas dans un `where`.
    * `body` laisse le processeur écrire le corps entier, en recevant le nom
      du CTE précédent. Un dépivotage est une union, un pivot un regroupement :
      ni l'un ni l'autre n'est une projection.
    """

    def __init__(
        self,
        select: list[tuple[str, str]] | None = None,
        where: str | None = None,
        distinct: bool = False,
        order_by: str | None = None,
        limit: int | None = None,
        cte: str = "etape",
        window: list[tuple[str, str]] | None = None,
        window_where: str | None = None,
        body: Callable[[str], str] | None = None,
    ):
        self.select = select  # [(expression, alias)] ; None = select *
        self.where = where
        self.distinct = distinct
        self.order_by = order_by
        self.limit = limit
        self.cte = cte
        self.window = window
        self.window_where = window_where
        self.body = body
