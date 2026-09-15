"""Le profilage qui porte sur toute la table, et non sur l'échantillon.

Deux cents lignes ne disent pas combien de valeurs distinctes compte une table
d'un million, ni combien de lignes une étape a retirées. Le contrôle complet
agrège sur tout.

Il coûte donc une vraie requête, et il ne part que sur demande — c'est aussi
pour ça que le résultat s'affiche sous un autre nom que « échantillon » : les
deux ne se valent pas, et les confondre est la façon la plus simple de se
tromper d'un facteur mille.

Comme le diagnostic de jointure, ce module écrit du SQL et relit des valeurs ;
il ne parle pas à l'entrepôt.
"""

from __future__ import annotations

from .recipes import (
    FINAL_SELECT,
    as_text,
    as_int,
    filled_sql,
    q,
    type_family,
)
from .recipes.dialect import current_dialect

# Au-delà, la requête d'agrégation devient elle-même un problème : quatre
# mesures par colonne, et certains entrepôts bornent la taille d'un select.
PROFILE_MAX_COLUMNS = 60

# Les seules familles sur lesquelles un plan d'agrégats générique a un sens.
# `type_family` rendait une chaîne non vide pour *tout* type nommé — y compris
# `JSON`, `STRUCT(...)` ou `INTEGER[]` — donc la liste « non profilées, faute
# d'un type que l'atelier sache agréger » était toujours vide, et `min` sur une
# structure faisait échouer la requête entière : on perdait le profil de toutes
# les autres colonnes avec elle.
PROFILABLE_FAMILIES = ("texte", "nombre", "date", "booléen")

# `min`/`max` sur un booléen : PostgreSQL et Redshift n'ont pas ces agrégats
# pour ce type, et le refus porte sur la requête entière. On garde alors les
# deux mesures qui existent partout, et on dit lesquelles manquent plutôt que
# de laisser deux cases vides sans explication.
# https://www.postgresql.org/docs/current/functions-aggregate.html
_NO_BOOL_MIN_MAX = ("postgres", "redshift")


def _possible_measures(family: str) -> tuple[bool, bool]:
    """(min/max disponible, count distinct disponible) pour cette famille."""
    if family == "booléen" and current_dialect().family in _NO_BOOL_MIN_MAX:
        return False, True
    return True, True


def profile_measures(columns: list[dict]) -> tuple[str, list[dict], dict]:
    """Les agrégats d'un contrôle complet, de quoi relire la ligne, et les écartées.

    Deux raisons d'écarter une colonne, et il faut les distinguer : un type que
    l'atelier ne sait pas agréger — `min` sur une structure ferait échouer la
    requête entière et on perdrait le profil de toutes les autres — ou un
    plafond de colonnes. Les confondre annoncerait un type incompréhensible
    là où il n'y a qu'une table large.
    """

    def profilable(c: dict) -> bool:
        return type_family(c.get("type")) in PROFILABLE_FAMILIES

    typeable = [c for c in columns if profilable(c)]
    kept_entries = typeable[:PROFILE_MAX_COLUMNS]
    discarded = {
        "type": [c["name"] for c in columns if not profilable(c)],
        "limite": [c["name"] for c in typeable[PROFILE_MAX_COLUMNS:]],
        # Colonne profilée, mais dont une mesure n'existe pas sur cet
        # entrepôt : l'écran doit pouvoir dire « pas de minimum ici » plutôt
        # que d'afficher une case vide qui ressemble à une donnée absente.
        "mesures": {},
    }
    measures = ["count(*) as n_lignes"]
    plan: list[dict] = []
    for i, c in enumerate(kept_entries):
        ref = q(c["name"])
        p = f"c{i}"
        family = type_family(c.get("type"))
        min_max, has_distinct = _possible_measures(family)
        # « Rempli » a la même définition que partout ailleurs dans l'atelier —
        # `filled_sql` —, et non `count(colonne)`, qui ne compte que les
        # non-`null`. L'aperçu voyait trois cellules vides là où le contrôle
        # complet en voyait une : deux écrans, deux vérités, sur la même donnée.
        measures.append(
            f"sum(case when {filled_sql(ref, c.get('type'))} then 1 else 0 end) "
            f"as {p}_remplies"
        )
        omitted = []
        if has_distinct:
            measures.append(f"count(distinct {ref}) as {p}_distinctes")
        else:
            omitted.append("distinctes")
        if min_max:
            measures += [
                f"{as_text(f'min({ref})')} as {p}_min",
                f"{as_text(f'max({ref})')} as {p}_max",
            ]
        else:
            omitted += ["min", "max"]
        if omitted:
            discarded["mesures"][c["name"]] = omitted
        plan.append(
            {
                "name": c["name"],
                "prefix": p,
                "type": c.get("type"),
                "omises": omitted,
            }
        )
    return ",\n    ".join(measures), plan, discarded


def aggregate_over(sql: str, measures: str) -> str:
    """Pose une agrégation sur le résultat d'un modèle compilé.

    Quand le SQL sort d'un de nos compilateurs, il finit exactement par
    `select * from <cte>` : on remplace cette ligne et l'agrégat lit le dernier
    CTE. Sinon — une recipe SQL écrite à la main — il faut une sous-requête, et
    tous les entrepôts n'acceptent pas un `with` à l'intérieur. D'où l'ordre :
    le chemin sûr d'abord, le chemin large en dernier recours.
    """
    m = FINAL_SELECT.search(sql)
    if m:
        return sql[: m.start()] + f"\nselect\n    {measures}\n\nfrom {m.group(1)}\n"
    # Le point-virgule final est la ponctuation habituelle d'un SQL écrit à la
    # main, et dbt l'accepte puisqu'il enveloppe lui-même le modèle. Enveloppé
    # tel quel, il se retrouvait *à l'intérieur* de la parenthèse, et l'entrepôt
    # rendait une erreur de syntaxe brute là où l'utilisateur n'a rien fait de
    # mal. On ne retire que ce qui termine le texte : un `;` dans un littéral
    # ou un commentaire n'est pas concerné.
    body = sql.rstrip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    return f"select\n    {measures}\n\nfrom (\n{body}\n) as source\n"


def read_profile(plan: list[dict], values_list: dict) -> dict:
    """La ligne d'agrégats, relue en profil de colonnes."""
    total = as_int(values_list.get("n_lignes"))
    columns = []
    for c in plan:
        p = c["prefix"]
        filled = as_int(values_list.get(f"{p}_remplies"))
        omitted = c.get("omises") or []
        columns.append(
            {
                "name": c["name"],
                "type": c.get("type"),
                "filled": filled,
                "empty": max(0, total - filled),
                "distinct": (
                    None
                    if "distinctes" in omitted
                    else as_int(values_list.get(f"{p}_distinctes"))
                ),
                "min": None if "min" in omitted else values_list.get(f"{p}_min"),
                "max": None if "max" in omitted else values_list.get(f"{p}_max"),
                # Ce que l'entrepôt ne sait pas calculer sur ce type : une case
                # vide pour cette raison n'est pas une donnée manquante.
                "omitted": omitted,
            }
        )
    return {"total": total, "columns": columns}


def profile_delta(before: dict, after: dict) -> list[str]:
    """Ce qui a changé entre l'entrée et la sortie, dit en une phrase chacun.

    C'est le vrai apport du contrôle complet : « 12 % des lignes supprimées,
    340 dates devenues nulles » — deux chiffres qu'un échantillon ne peut pas
    donner, et qui trouvent les erreurs qu'un SQL valide ne signale pas.
    """
    out: list[str] = []
    n_before, n_after = before.get("total") or 0, after.get("total") or 0
    if n_before and n_after != n_before:
        gap = n_before - n_after
        share = abs(gap) * 100 / n_before
        verb = "supprimées" if gap > 0 else "ajoutées"
        out.append(
            f"{abs(gap)} lignes {verb} ({share:.1f} %) — {n_before} en entrée, "
            f"{n_after} en sortie."
        )

    by_name = {c["name"]: c for c in before.get("columns") or []}
    for c in after.get("columns") or []:
        stale = by_name.get(c["name"])
        if not stale:
            continue
        # Les vides se comparent à nombre de lignes égal : sur un filtre, une
        # colonne perd des vides sans qu'aucune valeur n'ait été abîmée.
        if n_before != n_after:
            continue
        new_empties = (c.get("empty") or 0) - (stale.get("empty") or 0)
        if new_empties > 0:
            out.append(f"{new_empties} valeurs de « {c['name']} » sont devenues vides.")
    return out
