"""Ce qu'une jointure fait vraiment aux lignes, mesuré sur les tables entières.

Un SQL parfaitement valide ne dira jamais qu'il vient de tripler le chiffre
d'affaires. Les contrôles de noms de colonnes attrapent ce que `dbt build`
aurait refusé ; ceux-ci attrapent ce qu'il aurait accepté sans broncher — une
clé qui n'est pas unique du côté rapporté, et chaque ligne du pilote qui se
dédouble en silence.

Tout tient en une seule requête, et porte sur les tables *entières* : un
contrôle d'unicité sur deux cents lignes d'échantillon n'est pas un contrôle
d'unicité. C'est donc cher, et c'est donc à la demande.

Le module ne lit rien lui-même : il écrit la requête, et relit les valeurs
qu'on lui rapporte. L'entrepôt est l'affaire de l'appelant.
"""

from __future__ import annotations

from .recipes import (
    RecipeError,
    join_from_lines,
    as_int,
    free_cte,
    input_aliases,
    join_config,
    q,
    source_cte,
)


def diagnose_join_sql(spec: dict, columns: dict[str, list[dict]]) -> tuple[str, dict]:
    """(requête de diagnostic, plan de lecture) pour une recipe Joindre."""
    inputs = spec.get("inputs") or []
    if len(inputs) < 2:
        raise RecipeError(
            "Le diagnostic porte sur une recipe Joindre, qui prend au moins "
            "deux datasets."
        )
    aliases = input_aliases(inputs)
    driver = aliases[0]

    # Sans filtre incrémental : on compte ce que contiennent les tables, pas ce
    # que la prochaine exécution en relirait.
    ctes = [source_cte(a, i) for a, i in zip(aliases, inputs, strict=True)]
    result = free_cte("resultat", set(aliases))
    ctes.append(
        f"{result} as (\n\n    select 1 as _n\n"
        + "\n".join(join_from_lines(spec, aliases))
        + "\n\n)"
    )

    measures = [
        (f"(select count(*) from {driver})", "pilote_lignes"),
        (f"(select count(*) from {result})", "sortie_lignes"),
    ]
    checks: list[dict] = []

    for n, alias in enumerate(aliases[1:], start=1):
        jtype, keys = join_config(spec, n)
        p = f"e{n}"
        measures.append((f"(select count(*) from {alias})", f"{p}_lignes"))
        check = {"n": n, "alias": alias, "type": jtype, "keys": [], "prefix": p}

        if jtype != "cross":
            left = [q(k.get("left")) for k in keys]
            right = [q(k.get("right")) for k in keys]
            check["keys"] = [f"{k.get('left')} = {k.get('right')}" for k in keys]
            pairing = "\n              and ".join(
                f"{driver}.{q(k.get('left'))} = {alias}.{q(k.get('right'))}"
                for k in keys
            )
            # Les deux côtés sont mesurés, pas seulement celui qu'on rapporte.
            # Avec `right` ou `full`, c'est l'autre qui commande, et ne regarder
            # qu'une moitié disait l'inverse de la vérité.
            measures += _cardinality(driver, left, f"{p}_pilote")
            measures += _cardinality(alias, right, p)
            measures += [
                (
                    f"(select count(*) from {driver} where not exists (\n"
                    f"        select 1 from {alias}\n"
                    f"        where {pairing}\n"
                    f"    ))",
                    f"{p}_orphelines",
                ),
                (
                    f"(select count(*) from {alias} where not exists (\n"
                    f"        select 1 from {driver}\n"
                    f"        where {pairing}\n"
                    f"    ))",
                    f"{p}_orphelines_droite",
                ),
            ]
        checks.append(check)

    proj = ",\n    ".join(f"{e} as {a}" for e, a in measures)
    sql = "with " + ",\n\n".join(ctes) + f"\n\nselect\n    {proj}\n"
    return sql, {"driver": driver, "checks": checks}


def _cardinality(table: str, keys: list[str], p: str) -> list[tuple[str, str]]:
    """Clés distinctes, lignes de la plus répétée, et clés vides.

    Les clés vides sont écartées des deux premiers comptes : NULL ne s'apparie
    à rien, donc un million de lignes sans clé ne multiplie rien. Les compter
    annoncerait une explosion qui n'aura pas lieu — le genre d'alerte qui
    apprend à ignorer les alertes.
    """
    key = ", ".join(keys)
    filled_in = " and ".join(f"{c} is not null" for c in keys)
    empties = " or ".join(f"{c} is null" for c in keys)
    return [
        (
            f"(select count(*) from (select {key} from {table}"
            f" where {filled_in} group by {key}) as {p}_c)",
            f"{p}_cles",
        ),
        (
            f"(select coalesce(max(_n), 0) from (select count(*) as _n"
            f" from {table} where {filled_in} group by {key}) as {p}_m)",
            f"{p}_max",
        ),
        (f"(select count(*) from {table} where {empties})", f"{p}_cle_nulle"),
    ]


def _fanout_factor(x: float) -> str:
    """« 3 » plutôt que « 3.0 », « 2,4 » quand ce n'est pas rond."""
    return str(round(x)) if abs(x - round(x)) < 0.01 else f"{x:.1f}".replace(".", ",")


def join_diagnosis(plan: dict, values_list: dict) -> list[dict]:
    """Ce que les comptes veulent dire, dit en clair.

    Le premier constat est celui qui compte : combien de lignes entrent,
    combien sortent. Les suivants expliquent pourquoi.
    """
    driver = plan["driver"]
    n_driver = as_int(values_list.get("pilote_lignes"))
    n_output = as_int(values_list.get("sortie_lignes"))
    out: list[dict] = []

    if n_driver == 0:
        out.append(
            {
                "level": "warn",
                "title": f"« {driver} » est vide.",
                "detail": "Rien à diagnostiquer tant que le dataset qui mène la "
                "jointure n'a pas de lignes. Construisez-le d'abord.",
            }
        )
    elif n_output > n_driver:
        out.append(
            {
                "level": "bad",
                "title": f"Cette jointure multiplie les lignes de « {driver} » "
                f"par {_fanout_factor(n_output / n_driver)}.",
                "detail": f"{n_driver} lignes en entrée, {n_output} en sortie. "
                f"Toute somme calculée après cette jointure comptera plusieurs "
                f"fois la même ligne de « {driver} » — un chiffre d'affaires "
                f"multiplié d'autant, qu'aucun build ne signalera.",
            }
        )
    elif n_output < n_driver:
        out.append(
            {
                "level": "warn",
                "title": f"Cette jointure perd {n_driver - n_output} lignes de "
                f"« {driver} ».",
                "detail": f"{n_driver} lignes en entrée, {n_output} en sortie.",
            }
        )
    else:
        out.append(
            {
                "level": "ok",
                "title": f"La jointure ne change pas le nombre de lignes : "
                f"{n_driver} en entrée, autant en sortie.",
                # Et surtout pas « une ligne donne exactement une ligne » :
                # une jointure interne peut écarter la clé 2 et dupliquer la
                # clé 1 — deux lignes entrent, deux lignes sortent, et ce ne
                # sont pas les mêmes. Les constats suivants, eux, savent dire
                # ce qui se multiplie et ce qui ne s'apparie pas ; ce premier
                # constat ne porte que sur le compte total, et il doit le dire.
                "detail": f"Le total est le même de part et d'autre. Ce n'est "
                f"pas une correspondance une à une pour autant : une clé "
                f"dupliquée et une clé non appariée se compensent sans que le "
                f"total bouge. Les constats ci-dessous disent ce qui se "
                f"multiplie et ce qui reste sans correspondance dans "
                f"« {driver} ».",
            }
        )

    for c in plan.get("checks") or []:
        p, alias, jtype = c["prefix"], c["alias"], c["type"]
        n_right = as_int(values_list.get(f"{p}_lignes"))

        if jtype == "cross":
            out.append(
                {
                    "level": "bad" if n_right > 1 else "ok",
                    "title": f"« {alias} » est joint en croix : chaque ligne de "
                    f"« {driver} » est répétée {n_right} fois.",
                    "detail": "C'est le propre d'une jointure croisée — elle "
                    "n'apparie rien, elle combine tout avec tout.",
                }
            )
            continue

        on_keys = ", ".join(c["keys"]) or "sa clé"
        g_max = as_int(values_list.get(f"{p}_pilote_max"))
        g_keys = as_int(values_list.get(f"{p}_pilote_cles"))
        d_max = as_int(values_list.get(f"{p}_max"))
        d_keys = as_int(values_list.get(f"{p}_cles"))

        # La cardinalité ne dépend pas du type de jointure : si une clé se
        # répète trois fois d'un côté, les lignes de l'autre ressortent trois
        # fois, que la jointure soit `left`, `inner`, `right` ou `full`. Le
        # type ne décide que du sort des lignes *sans* correspondance.
        if g_max <= 1 and d_max <= 1:
            out.append(
                {
                    "level": "ok",
                    "title": f"Relation de 1 à 1 sur {on_keys}.",
                    "detail": f"« {driver} » a {g_keys} clés distinctes, "
                    f"« {alias} » en a {d_keys}, et aucune ne se répète : rien "
                    f"ne peut se dédoubler.",
                }
            )
        elif d_max <= 1:
            out.append(
                {
                    "level": "ok",
                    "title": f"Relation de N à 1 : « {alias} » est unique "
                    f"sur {on_keys}.",
                    "detail": f"Plusieurs lignes de « {driver} » peuvent pointer "
                    f"la même ligne de « {alias} », jamais l'inverse. Rapporter "
                    f"« {alias} » ne multiplie rien.",
                }
            )
        elif g_max <= 1:
            out.append(
                {
                    "level": "bad",
                    "title": f"« {alias} » n'est pas unique sur {on_keys} : jusqu'à "
                    f"{d_max} lignes pour une même clé.",
                    "detail": f"{d_keys} clés distinctes pour {n_right} lignes. "
                    f"Chaque ligne de « {driver} » qui tombe sur une clé répétée "
                    f"ressort autant de fois. Dédoublonnez « {alias} » par sa clé "
                    f"avant de joindre, ou regroupez-la.",
                }
            )
        else:
            out.append(
                {
                    "level": "bad",
                    "title": f"Relation de N à N sur {on_keys} : ni « {driver} » ni "
                    f"« {alias} » n'est unique.",
                    "detail": f"Jusqu'à {g_max} lignes pour une clé d'un côté, "
                    f"{d_max} de l'autre : une clé présente des deux côtés produit "
                    f"le produit des deux. C'est presque toujours une clé de "
                    f"jointure incomplète — il manque une colonne.",
                }
            )

        # Les lignes sans correspondance : là, le type décide. Chaque côté a
        # son sort, et `right` comme `full` gardent celui qu'on ne regardait pas.
        keep_left = jtype in ("left", "full")
        keep_right = jtype in ("right", "full")
        for orphans, total, side, other, kept in (
            (
                as_int(values_list.get(f"{p}_orphelines")),
                n_driver,
                driver,
                alias,
                keep_left,
            ),
            (
                as_int(values_list.get(f"{p}_orphelines_droite")),
                n_right,
                alias,
                driver,
                keep_right,
            ),
        ):
            if not orphans:
                continue
            title = (
                f"{orphans} lignes de « {side} » sur {total} n'ont pas de "
                f"correspondance dans « {other} »."
            )
            if kept:
                out.append(
                    {
                        "level": "warn",
                        "title": title,
                        "detail": f"La jointure est « {jtype} » : ces lignes "
                        f"restent, avec les colonnes de « {other} » vides.",
                    }
                )
            else:
                out.append(
                    {
                        "level": "bad",
                        "title": title,
                        "detail": f"La jointure est « {jtype} » : ces lignes "
                        f"disparaissent du résultat.",
                    }
                )

        for empties, side in (
            (as_int(values_list.get(f"{p}_pilote_cle_nulle")), driver),
            (as_int(values_list.get(f"{p}_cle_nulle")), alias),
        ):
            if not empties:
                continue
            out.append(
                {
                    "level": "warn",
                    "title": f"{empties} lignes de « {side} » ont une clé vide.",
                    "detail": "Une clé vide ne s'apparie à rien, pas même à une "
                    "clé vide en face : ces lignes comptent parmi celles sans "
                    "correspondance.",
                }
            )

    return out
