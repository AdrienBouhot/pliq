"""Recipe → SQL dbt : un compilateur par type de recipe.

Chaque étape devient un CTE nommé et commenté, et le fichier finit toujours
par `select * from <dernier CTE>` — `full_check` s'appuie sur cette forme
pour poser son agrégat sans sous-requête.
"""

from __future__ import annotations

import re

from .columns import ColumnState, StepSql, _check_unique_columns
from .config import (
    check_microbatch_output,
    config_block,
    incremental_column,
    incremental_filter,
    incremental_group_filter,
    microbatch,
    strategy_of,
)
from .describe import describe_step
from .dialect import current_dialect
from .errors import RecipeError
from .names import check_predicate, validate_name
from .processors import PROCESSORS
from .refs import ref_sql
from .spec import (
    free_cte,
    input_alias,
    input_aliases,
)
from .sql import q
from .storage import RECIPE_DIR
from .vocabulary import RECIPE_TYPES
from .writers import median, median_is_approximate, type_family

# ------------------------------------------------------------------ compilation


# La dernière ligne d'un modèle compilé, et de quoi la relire. Les deux
# s'écrivent côte à côte pour qu'elles ne puissent pas diverger en silence : si
# elles divergeaient, le contrôle complet retomberait sans bruit sur la
# sous-requête, que Redshift refuse. `full_check` importe `FINAL_SELECT`, et
# un test vérifie que chaque compilateur finit bien par cette forme.
FINAL_SELECT = re.compile(r"\nselect \* from (\w+)\n\Z")


def final_select(cte: str) -> str:
    """La dernière ligne d'un modèle compilé : `select * from <dernier CTE>`."""
    return f"\n\nselect * from {cte}\n"


def _projection_sql(select: list[tuple[str, str]], indent: str = "        ") -> str:
    width = max((len(e) for e, a in select if e != a), default=0)
    parts = []
    for expr, alias in select:
        if expr == alias or expr == q(alias):
            # `q()` et pas `alias` : une colonne « order total » doit rester
            # citée, sinon la projection sort deux identifiants.
            parts.append(q(alias))
        else:
            parts.append(f"{expr.ljust(width)}  as {q(alias)}")
    return (",\n" + indent).join(parts)


def _render(step_sql: StepSql, prev: str, state_columns: list[str]) -> str:
    if step_sql.body is not None:
        return step_sql.body(prev)

    select = step_sql.select

    if step_sql.window:
        # Une fonction de fenêtre ne se filtre pas dans un `where` : elle se
        # calcule dans une sous-requête, et le filtre se pose dehors. La
        # sous-requête est nommée parce que PostgreSQL et Redshift l'exigent.
        internal = (select or [(q(c), c) for c in state_columns]) + list(
            step_sql.window
        )
        external = ",\n        ".join(
            q(a) for _, a in (select or [(q(c), c) for c in state_columns])
        )
        window_body = (
            f"    select\n        {external}\n\n"
            f"    from (\n"
            f"        select\n            "
            f"{_projection_sql(internal, '            ')}\n\n"
            f"        from {prev}\n"
            # L'alias est dérivé du CTE : une étape de fenêtre suivie d'un
            # dédoublonnage donnait `from ( … from fenetre ) as fenetre`, légal
            # mais illisible — et le .sql est la source de vérité.
            f"    ) as {step_sql.cte}_base"
        )
        if step_sql.window_where:
            window_body += f"\n    where {step_sql.window_where}"
        return window_body

    cols = "*" if select is None else _projection_sql(select)

    head = "select distinct" if step_sql.distinct else "select"
    body = f"    {head}\n        {cols}\n\n    from {prev}"
    if step_sql.where:
        body += f"\n    where {step_sql.where}"
    if step_sql.order_by:
        body += f"\n    order by {step_sql.order_by}"
    if step_sql.limit:
        body += f"\n    limit {int(step_sql.limit)}"
    return body


def source_cte(alias: str, inp: dict, inc: str = "", here: str = "") -> str:
    """CTE d'entrée, avec le filtre incrémental s'il y en a un.

    `here` nomme la table lue : un filtre corrélé a besoin de pouvoir la
    désigner pour la distinguer de celle de sa sous-requête.
    """
    since = ref_sql(inp) + (f" as {here}" if here else "")
    return f"{alias} as (\n\n    select * from {since}\n{inc}\n)"


def _apply_step(state: ColumnState, step: dict):
    """Passe une étape sur l'état des colonnes, et rend son SQL et son delta.

    Le delta est ce que l'écran affiche d'une étape sans rien réexécuter : ce
    qui y entre, ce qui en sort, et ce qu'elle a créé, modifié ou supprimé au
    passage. Il se calcule entièrement de tête, d'où le fait qu'on puisse le
    produire pour des étapes que l'aperçu n'exécute pas.
    """
    proc = PROCESSORS.get(step.get("type"))
    if proc is None:
        raise RecipeError(f"Étape inconnue : {step.get('type')}")
    before = list(state.columns)
    step_sql = proc["fn"](state, step.get("params") or {})
    after = list(state.columns)
    _check_unique_columns(before, after, proc["label"])

    created = [c for c in after if c not in before]
    deleted = [c for c in before if c not in after]
    modified = []
    if step_sql.select:
        for expr, a in step_sql.select:
            if a in before and expr not in (a, q(a)):
                modified.append(a)
    return step_sql, {
        "step": step.get("id"),
        # La phrase de l'étape, écrite une seule fois et servie à l'écran.
        # Elle était écrite deux fois — ici pour le commentaire du `.sql`, et
        # en JavaScript pour la carte — et quatre des vingt-quatre types
        # finissaient par diverger. Le pire était le tri : le sens, qui est
        # tout ce que l'étape fait, manquait au fichier. Le projet nomme
        # ailleurs cette duplication comme une dette qu'il a délibérément
        # supprimée pour les opérateurs de filtre ; il restait celle-ci.
        "label": describe_step(step),
        "before": before,
        "after": after,
        "created": created,
        "deleted": deleted,
        "modified": [m for m in modified if m not in created],
        "filters": bool(step_sql.where) or step_sql.distinct,
    }


def compile_prepare(
    spec: dict, columns: dict[str, list[dict]], upto: int | None = None
):
    """Compile un script Prepare.

    Retourne (sql, colonnes_finales, deltas_par_étape). `upto` borne le SQL au
    n-ième étape active — c'est l'œil de l'écran — sans borner les deltas ni
    les colonnes finales : eux décrivent le script, pas l'aperçu. Chaque delta
    dit par son `applied` s'il est dans le SQL rendu.
    """
    inputs = spec.get("inputs") or []
    if len(inputs) != 1:
        raise RecipeError("Une recipe Prepare prend exactement un dataset en entrée.")
    alias = input_alias(inputs[0], 0)
    cols_meta = columns.get(alias) or columns.get("__input__") or []
    state = ColumnState(
        [c["name"] for c in cols_meta],
        {c["name"]: c.get("type") for c in cols_meta},
    )

    output = spec.get("output") or {}
    bound_column = incremental_column(output)
    if bound_column and state.columns and bound_column not in state.columns:
        raise RecipeError(
            f"La colonne incrémentale « {bound_column} » n'existe pas dans "
            f"« {alias} » : c'est là que le filtre la compare. Choisissez une "
            f"colonne du dataset d'entrée, ou passez la sortie en « table »."
        )
    ctes = [
        source_cte(
            alias,
            inputs[0],
            incremental_filter(
                output, state.type_of(bound_column) if bound_column else None
            ),
        )
    ]
    prev = alias
    deltas: list[dict] = []
    # L'alias d'entrée est déjà un CTE : le réserver évite qu'une étape le
    # reprenne et produise deux CTE du même nom, donc un SQL invalide.
    taken: set[str] = {alias}

    steps = list((spec.get("steps") or []))
    active = [s for s in steps if s.get("enabled", True)]
    # L'œil arrête le SQL au milieu du script, mais pas le suivi des colonnes :
    # savoir ce qu'une étape reçoit ne coûte rien à l'entrepôt. On continue
    # donc à produire les deltas jusqu'au bout, en marquant ceux qui ne sont
    # pas dans le SQL exécuté. Sans ça, l'écran n'a plus rien pour dire quelles
    # colonnes entrent dans une étape placée après l'œil : il retombe sur les
    # colonnes de l'aperçu — celles de l'œil — et propose dans les formulaires
    # des colonnes qui n'existent pas encore, ou en cache d'autres.
    up_to = len(active) if upto is None else max(0, upto)

    for i, step in enumerate(active, start=1):
        in_sql = i <= up_to
        try:
            step_sql, delta = _apply_step(state, step)
        except Exception:
            # Au-delà de la borne, une étape qui ne compile pas ne casse pas
            # l'aperçu : c'est justement quand la fin du script est cassée
            # qu'on pose l'œil avant elle. On cesse d'y voir clair à partir de
            # là, et l'écran s'en tiendra au dernier état connu.
            if in_sql:
                raise
            break
        delta["applied"] = in_sql
        deltas.append(delta)
        if not in_sql:
            continue

        name = free_cte(step_sql.cte, taken)
        label = describe_step(step)
        ctes.append(
            f"-- {i} · {label}\n{name} as "
            f"(\n\n{_render(step_sql, prev, delta['after'])}\n\n)"
        )
        prev = name

    # Un aperçu partiel s'arrête au milieu du script : la colonne de temps peut
    # n'apparaître qu'après, et le contrôle ne vaut que pour le script entier.
    if upto is None:
        check_microbatch_output(output, state.columns, "ce script")
        _check_prepare_incremental(bound_column, state.columns)
        _check_prepare_incremental_steps(output, bound_column, active, deltas)

    sql = "with " + ",\n\n".join(ctes) + final_select(prev)
    return sql, state, deltas


def _check_prepare_incremental(bound_column: str, produced: list[str]) -> None:
    """La colonne de borne doit ressortir du script, pas seulement y entrer.

    Le filtre incrémental compare deux choses : `col` telle qu'elle arrive dans
    le CTE d'entrée, et `max(col)` lu dans la table déjà écrite. Les deux ne
    parlent de la même colonne que si elle traverse le script — sous le même
    nom. Une étape qui la renomme, ou qui la retire, laisse `max(col)` chercher
    dans la sortie une colonne qui n'y est plus.

    Rien n'en paraît au premier `dbt build` : `is_incremental()` est faux, le
    bloc n'est pas compilé, le modèle passe. C'est le deuxième qui casse — et
    la cause est loin de l'endroit qui échoue. Autant le dire à l'écriture.

    Les jointures et les empilements font déjà ce contrôle, chacun à sa façon ;
    c'est le script Prepare qui ne l'avait pas.
    """
    if not bound_column or not produced or bound_column in produced:
        return
    raise RecipeError(
        f"La colonne incrémentale « {bound_column} » ne sort pas de ce script : la "
        f"borne « max({bound_column}) » est lue dans la table déjà écrite, où la "
        f"colonne doit donc figurer. Le premier build passerait, le suivant "
        f"non. Gardez « {bound_column} » dans le résultat sans la renommer, bornez "
        f"sur une colonne qui sort, ou passez la sortie en « table ». "
        f"Colonnes produites : {', '.join(produced)}."
    )


def _check_prepare_incremental_steps(
    out: dict, bound_column: str, steps: list[dict], deltas: list[dict]
) -> None:
    """Refuse les étapes dont le calcul ne survit pas au filtre incrémental.

    Le filtre est posé sur le CTE d'entrée, avant la première étape : une
    exécution incrémentale ne voit que l'arrivage. Deux façons d'en tirer un
    résultat faux, toutes deux muettes — le SQL reste valide et le build passe.

    Une fonction de fenêtre, d'abord. Son cadre ne porte plus que sur les
    lignes du jour : un cumul repart de zéro, un classement renumérote à
    partir de 1, `lag` ne trouve plus la ligne d'hier. L'agrégation sait déjà
    reprendre en entier tout groupe touché (`incremental_group_filter`) ; une
    fenêtre n'a pas d'équivalent — sans `partition by`, son cadre est la table
    entière, et il faudrait la relire toute pour calculer juste.

    La colonne de borne, ensuite. Le filtre compare sa valeur *en entrée* à
    `max(colonne)` lu dans la table déjà écrite, où elle est ressortie
    transformée : `id + 100` écrit dans `id`, et c'est `3 > 102` qui décide du
    sort des lignes nouvelles. Le contrôle de nom laisse passer ce cas — la
    colonne est bien là, c'est sa valeur qui a changé d'échelle. La jointure
    contrôle déjà la provenance de sa borne ; le script Prepare ne le faisait
    pas.
    """
    if (out.get("materialized") or "view") != "incremental":
        return

    def _reject_out_of_range(what: str) -> RecipeError:
        return RecipeError(
            f"{what} « {bound_column} », la colonne qui borne l'incrément : le filtre "
            f"compare la valeur qui *entre* dans ce script à « max({bound_column}) », "
            f"lu dans la table déjà écrite, où elle est ressortie transformée. "
            f"Les deux ne sont plus sur la même échelle, et des lignes "
            f"nouvelles peuvent être écartées sans qu'aucune erreur ne le dise. "
            f"Écrivez le résultat dans une autre colonne, bornez sur une "
            f"colonne que le script laisse intacte, ou passez la sortie en "
            f"« table »."
        )

    # La colonne qui porte la valeur de borne, suivie d'étape en étape. Un
    # renommage la déplace sans y toucher — c'est le nom qui voyage, pas la
    # valeur — et il ne doit donc rien déclencher.
    carrier = bound_column
    for step, delta in zip(steps, deltas, strict=True):
        label = describe_step(step)
        if step.get("type") == "window_function":
            raise RecipeError(
                f"« {label} » a besoin de lignes que le filtre incrémental "
                f"écarte : une exécution ne voit que ce qui est arrivé depuis "
                f"la dernière, et la fenêtre se recalcule sur ce seul arrivage "
                f"— un cumul repart de zéro, un classement renumérote à partir "
                f"de 1. Rien ne le signalerait : le SQL est valide et le build "
                f"passe. Passez la sortie en « table », ou retirez cette étape."
            )
        if step.get("type") == "dedup_key":
            _check_dedup_incremental(out, step, label, bound_column)
        if step.get("type") == "pivot":
            _check_pivot_incremental(out, step, label, bound_column)
        if not carrier:
            continue
        if step.get("type") == "rename":
            for r in (step.get("params") or {}).get("renames") or []:
                if r.get("from") == carrier and r.get("to"):
                    carrier = r["to"]
                    break
        elif carrier in delta["modified"]:
            raise _reject_out_of_range(f"« {label} » recalcule")
        if carrier in delta["deleted"]:
            carrier = ""

    # Le nom est peut-être encore là — `_check_prepare_incremental` l'a vérifié
    # — mais porté par une autre colonne : celle d'origine retirée, une
    # nouvelle créée sous le même nom, et `max(borne)` lit alors des valeurs
    # qui ne viennent pas de la borne d'entrée.
    if carrier != bound_column:
        raise _reject_out_of_range("Ce script reconstruit")


def _check_dedup_incremental(
    out: dict, step: dict, label: str, bound_column: str = ""
) -> None:
    """« Une ligne par clé » ne tient que si la sortie remplace par cette clé.

    L'étape promet une ligne par clé métier. Une exécution incrémentale ne voit
    que l'arrivage : elle dédoublonne *ce lot*, et c'est la matérialisation qui
    doit retirer la ligne déjà écrite pour la même clé. Trois façons de rater,
    toutes muettes.

    En `append`, rien n'est retiré : la ligne d'hier et celle du jour cohabitent
    sous la même clé. C'est le raisonnement de la fenêtre de reprise, appliqué
    au dédoublonnage.

    Avec une clé unique qui ne couvre pas la clé de dédoublonnage, dbt retire
    des lignes que l'arrivage ne réécrit pas : dédoublonner sur (client, jour)
    en ne remplaçant que par `client` efface les autres jours de ce client, et
    ne les réinsère pas. Ce n'est plus un doublon, c'est une perte.

    Avec une clé unique qui en porte plus, la ligne déjà écrite ne se retrouve
    plus — sa valeur de clé n'est pas dans l'arrivage — et un second
    exemplaire s'ajoute. C'est exactement ce que `_check_group_incremental`
    contrôle pour une agrégation ; regrouper et dédoublonner butent sur la même
    chose.

    Et une quatrième, qui n'est pas affaire de stratégie mais de tri : le
    classement ne porte que sur l'arrivage, jamais sur la ligne déjà gagnante.
    Avec « garder la première » sur une séquence croissante, la cible passe de
    (id=1, seq=1) à (1, 2) au deuxième build, là où un recalcul complet garde
    (1, 1) — la ligne gagnante a été oubliée parce qu'elle n'était pas dans le
    lot. Le tri n'est sûr que s'il désigne toujours la ligne la plus récente
    *au sens de la borne* : l'arrivage porte alors, par construction, une
    valeur supérieure à tout ce que la table contient déjà.
    """
    keys = [k for k in ((step.get("params") or {}).get("keys") or []) if str(k).strip()]
    if not keys:
        return

    strategy = strategy_of(out)
    remedy = (
        f"Choisissez « delete+insert » ou « merge » avec {_listed(keys)} comme "
        f"clé unique, ou passez la sortie en « table »."
    )
    if strategy == "microbatch":
        raise RecipeError(
            f"« {label} » garde une ligne par clé, mais « microbatch » réécrit "
            f"une tranche de temps, pas une clé : deux lignes d'une même clé "
            f"tombées dans deux tranches restent toutes les deux, et le "
            f"dédoublonnage ne vaut plus qu'à l'intérieur d'une tranche. {remedy}"
        )
    if strategy not in ("delete+insert", "merge"):
        raise RecipeError(
            f"« {label} » garde une ligne par clé, mais la stratégie "
            f"« {strategy} » n'en retire aucune : la ligne déjà écrite pour "
            f"cette clé reste en place, et celle de l'arrivage s'ajoute à "
            f"côté. {remedy}"
        )

    unique_keys = [k for k in (out.get("unique_key") or []) if str(k).strip()]
    missing = [k for k in keys if k not in unique_keys]
    if missing:
        raise RecipeError(
            f"Les clés uniques doivent couvrir le dédoublonnage de "
            f"« {label} » : {_listed(missing)} y "
            f"manque{'nt' if len(missing) > 1 else ''}. dbt retirerait alors "
            f"des lignes que l'arrivage ne réécrit pas — elles disparaîtraient "
            f"de la table sans qu'aucun build n'échoue."
        )

    extra = [k for k in unique_keys if k not in keys]
    if extra:
        raise RecipeError(
            f"Les clés uniques ne peuvent porter que le dédoublonnage de "
            f"« {label} » : {_listed(extra)} n'en "
            f"fai{'t' if len(extra) == 1 else 'sent'} pas partie. La ligne "
            f"déjà écrite ne porte pas la même valeur de clé que celle de "
            f"l'arrivage : dbt ne la retrouve pas, ne la retire pas, et la "
            f"table garde deux lignes pour la même clé métier."
        )

    # Sans borne, l'exécution relit tout l'historique : le classement voit
    # alors la ligne déjà gagnante, et n'importe quel tri est juste.
    if not bound_column:
        return
    sort_specs = [o for o in ((step.get("params") or {}).get("order_by") or []) if o]
    first = sort_specs[0] if sort_specs else {}
    if str(first.get("column") or "") == bound_column and first.get("descending"):
        return
    says = (
        f"« {first.get('column')} » "
        + ("décroissant" if first.get("descending") else "croissant")
        if first.get("column")
        else "aucun tri"
    )
    raise RecipeError(
        f"« {label} » trie par {says}, mais une exécution incrémentale ne "
        f"classe que l'arrivage : la ligne déjà gagnante n'en fait pas partie, "
        f"et elle serait remplacée par une ligne que le recalcul complet "
        f"n'aurait pas retenue. Le tri n'est sûr que s'il garde la ligne la "
        f"plus récente au sens de la borne — triez d'abord par « {bound_column} » "
        f"décroissant, ou passez la sortie en « table »."
    )


def _check_pivot_incremental(
    out: dict, step: dict, label: str, bound_column: str
) -> None:
    """« Pivoter » agrège aussi, et échappait à tous les garde-fous.

    L'étape regroupe par ses colonnes de clé et agrège le reste : c'est une
    agrégation, écrite autrement. Mais elle vit dans un script Prepare, où le
    filtre incrémental est posé *ligne par ligne* sur le CTE d'entrée, et non
    par groupe comme dans une recipe « Grouper ». Une exécution ne voit donc
    que l'arrivage, et le pivot d'un groupe déjà écrit est recalculé sur les
    seules lignes du jour : la reproduction rend une ligne là où le recalcul
    complet en rend deux.

    Deux façons que ça tienne, et une seule qui se vérifie ici : que le
    découpage du temps fasse partie du regroupement, si bien qu'un groupe ne
    puisse pas être à cheval sur deux exécutions. C'est exactement le
    raisonnement de `_check_group_microbatch`, appliqué à la borne classique.
    """
    if (out.get("materialized") or "view") != "incremental":
        return
    keys = [
        k
        for k in ((step.get("params") or {}).get("key_columns") or [])
        if str(k).strip()
    ]
    strategy = strategy_of(out)
    remedy = (
        "Ajoutez la colonne de temps au regroupement du pivot, ou passez la "
        "sortie en « table »."
    )
    if not keys:
        raise RecipeError(
            f"« {label} » sans colonne de clé produit une seule ligne pour tout "
            f"le dataset : en incrémental, celle du jour remplacerait celle "
            f"d'hier, calculée sur le seul arrivage. {remedy}"
        )
    if strategy == "microbatch":
        cfg = microbatch(out)
        col = str(cfg.get("event_time") or "").strip()
        if col and col not in keys:
            raise RecipeError(
                f"« {label} » regroupe par {_listed(keys)}, mais « microbatch » "
                f"réécrit une tranche de temps entière : un groupe à cheval sur "
                f"deux tranches serait pivoté deux fois, sur une moitié de ses "
                f"lignes chaque fois. Regroupez aussi par « {col} », ou passez "
                f"la sortie en « table »."
            )
        return
    if strategy not in ("delete+insert", "merge"):
        raise RecipeError(
            f"« {label} » produit une ligne par clé, mais la stratégie "
            f"« {strategy} » n'en retire aucune : la ligne déjà écrite pour "
            f"cette clé reste en place et celle de l'arrivage s'ajoute à côté. "
            f"Choisissez « delete+insert » ou « merge » avec {_listed(keys)} "
            f"comme clé unique, ou passez la sortie en « table »."
        )

    unique_keys = [k for k in (out.get("unique_key") or []) if str(k).strip()]
    missing = [k for k in keys if k not in unique_keys]
    if missing:
        raise RecipeError(
            f"Les clés uniques doivent couvrir le regroupement de « {label} » : "
            f"{_listed(missing)} y manque"
            f"{'nt' if len(missing) > 1 else ''}. Sans cela, dbt retire des "
            f"lignes que l'arrivage ne réécrit pas."
        )
    extra = [k for k in unique_keys if k not in keys]
    if extra:
        raise RecipeError(
            f"Les clés uniques ne peuvent porter que le regroupement de "
            f"« {label} » : {_listed(extra)} n'en "
            f"fai{'t' if len(extra) == 1 else 'sent'} pas partie. La ligne "
            f"déjà écrite ne se retrouverait plus, et un second exemplaire "
            f"s'ajouterait pour la même clé."
        )

    # La borne, enfin : c'est elle qui décide si un groupe peut être à cheval.
    if bound_column and bound_column not in keys:
        raise RecipeError(
            f"« {label} » regroupe par {_listed(keys)}, mais le filtre "
            f"incrémental ne laisse entrer que les lignes dont « {bound_column} » "
            f"dépasse le maximum déjà écrit : un groupe qui reçoit des lignes "
            f"à deux exécutions serait recalculé sur le seul arrivage, et "
            f"remplacerait le pivot complet par un pivot partiel. Rien ne le "
            f"signalerait. Ajoutez « {bound_column} » au regroupement, retirez la "
            f"colonne de repère, ou passez la sortie en « table »."
        )


def _listed(names: list[str]) -> str:
    return ", ".join(f"« {n} »" for n in names)


def _check_join_incremental(
    spec: dict, aliases: list[str], columns: dict[str, list[dict]]
) -> None:
    """Refuse les jointures incrémentales dont le calcul ne peut pas être juste.

    Le filtre incrémental ne borne que la *première* entrée : c'est elle qui
    mène la jointure, les autres ne servent qu'à compléter ses lignes. Deux
    conditions pour que ça tienne.

    D'abord le type de jointure. Avec `right` ou `full`, une ligne de droite
    déjà matérialisée ressort comme non appariée dès que sa contrepartie de
    gauche est filtrée : l'exécution suivante la réémet sans qu'aucune source
    n'ait bougé, et `append` la duplique.

    Ensuite la colonne de borne. `max(col)` est lu dans la table déjà écrite,
    mais `col` est comparé dans le CTE de la première entrée : les deux ne
    parlent du même chose que si la colonne de sortie vient de cette entrée,
    sous le même nom. Sinon le modèle casse au `dbt build`, ou pire il compare
    deux colonnes différentes.
    """
    out = spec.get("output") or {}
    col = incremental_column(out)
    if not col:
        return

    joins = spec.get("joins") or []
    forbidden: dict[str, str] = {}
    for n, alias in enumerate(aliases[1:]):
        jtype = ((joins[n] if n < len(joins) else {}).get("type") or "left").lower()
        if jtype in ("right", "full"):
            forbidden[alias] = jtype
    if forbidden:
        what = ", ".join(f"« {a} » ({t})" for a, t in forbidden.items())
        raise RecipeError(
            f"Une sortie incrémentale ne borne que « {aliases[0]} », le dataset "
            f"qui mène la jointure. Avec une jointure {what}, les lignes déjà "
            f"écrites ressortent comme non appariées à chaque exécution et se "
            f"dupliquent. Passez en jointure « inner » ou « left », ou passez "
            f"la sortie en « table »."
        )

    selected = spec.get("select") or []
    if selected:
        output = next(
            (s for s in selected if (s.get("as") or s.get("column")) == col), None
        )
        arrival = (output or {}).get("from")
        if output is None or arrival != aliases[0] or output.get("column") != col:
            origin_text = (
                f"de « {arrival} »" if arrival else "d'aucune colonne de sortie"
            )
            raise RecipeError(
                f"La colonne incrémentale « {col} » vient {origin_text} : la borne "
                f"« max({col}) » est comparée dans « {aliases[0]} », le dataset "
                f"qui mène la jointure. Sortez « {col} » depuis « {aliases[0]} » "
                f"sans la renommer, ou passez la sortie en « table »."
            )
    else:
        present_columns = {c["name"] for c in (columns.get(aliases[0]) or [])}
        if present_columns and col not in present_columns:
            raise RecipeError(
                f"La colonne incrémentale « {col} » manque à « {aliases[0]} », le "
                f"dataset qui mène la jointure : c'est là que la borne est "
                f"comparée. Choisissez une colonne de « {aliases[0]} », ou passez "
                f"la sortie en « table »."
            )


def _type_of(columns: dict[str, list[dict]], alias: str, col: str) -> str | None:
    """Le type déclaré d'une colonne d'entrée, s'il est connu."""
    if not col:
        return None
    for c in columns.get(alias) or []:
        if c.get("name") == col:
            return c.get("type")
    return None


def _join_output_columns(
    spec: dict, aliases: list[str], columns: dict[str, list[dict]]
) -> list[str]:
    """Les colonnes que la jointure produit, sélection explicite ou `a.*`."""
    selected = spec.get("select") or []
    if selected:
        return [s.get("as") or s.get("column") for s in selected if s.get("column")]
    return [name for _, name in _join_star_columns(aliases, columns)]


def _join_star_columns(
    aliases: list[str], columns: dict[str, list[dict]]
) -> list[tuple[str, str]]:
    """Le mode « toutes les colonnes », en projections explicites.

    `a.*, b.*` laisse l'entrepôt trancher les homonymes : DuckDB rend `id` et
    `ID_1`, un autre refuse la requête, un troisième garde la première. Le
    compilateur, lui, annonçait `id` deux fois — et l'analyse d'impact comme le
    contrôle de la colonne incrémentale raisonnaient sur cette annonce fausse.

    On nomme donc nous-mêmes : la première occurrence garde son nom, les
    suivantes sont préfixées par leur dataset. Les noms annoncés sont alors
    ceux que l'entrepôt écrira, et ils ne dépendent plus de lui. Comme
    ailleurs, la comparaison est insensible à la casse : c'est l'entrepôt qui
    normalise les identifiants.
    """
    out: list[tuple[str, str]] = []
    held: set[str] = set()
    for a in aliases:
        for c in columns.get(a) or []:
            name = c["name"]
            if name.casefold() not in held:
                held.add(name.casefold())
                out.append((f"{a}.{q(name)}", name))
                continue
            candidate = f"{a}_{name}"
            n = 1
            while candidate.casefold() in held:
                n += 1
                candidate = f"{a}_{name}_{n}"
            held.add(candidate.casefold())
            out.append((f"{a}.{q(name)} as {q(candidate)}", candidate))
    return out


JOIN_TYPES = ("inner", "left", "right", "full", "cross")


def join_config(spec: dict, n: int) -> tuple[str, list[dict]]:
    """(type, paires de clés) de la n-ième jointure — n vaut 1 pour la première."""
    joins = spec.get("joins") or []
    cfg = joins[n - 1] if len(joins) >= n else {}
    jtype = (cfg.get("type") or "left").lower()
    if jtype not in JOIN_TYPES:
        raise RecipeError(f"Type de jointure inconnu : {jtype}")
    return jtype, list(cfg.get("on") or [])


def join_from_lines(spec: dict, aliases: list[str]) -> list[str]:
    """Le `from … join …`, partagé par la compilation et le diagnostic."""
    from_lines = [f"    from {aliases[0]}"]
    for n, alias in enumerate(aliases[1:], start=1):
        jtype, keys = join_config(spec, n)
        if jtype == "cross":
            from_lines.append(f"    cross join {alias}")
            continue
        if not keys:
            raise RecipeError(f"Jointure sur {alias} : aucune clé indiquée.")
        conds = " and ".join(
            f"{aliases[0]}.{q(k.get('left'))} = {alias}.{q(k.get('right'))}"
            for k in keys
        )
        from_lines.append(f"    {jtype} join {alias}\n        on {conds}")
    return from_lines


def compile_join(spec: dict, columns: dict[str, list[dict]]):
    inputs = spec.get("inputs") or []
    if len(inputs) < 2:
        raise RecipeError("Une recipe Join prend au moins deux datasets.")
    aliases = input_aliases(inputs)
    _check_join_incremental(spec, aliases, columns)
    output = spec.get("output") or {}
    bound_column = incremental_column(output)
    inc = incremental_filter(
        output,
        _type_of(columns, aliases[0], bound_column),
        # Seule l'entrée pilote est bornée : c'est elle qui mène la jointure.
        # Une dimension dont le libellé change laisse donc l'ancienne valeur
        # dans la cible tant que la ligne pilote ne repasse pas la borne. Ce
        # comportement convient à beaucoup de modèles — et il ne se voyait
        # nulle part.
        note=(
            (
                f"seul « {aliases[0]} » est borné : les autres entrées ne sont "
                f"relues\nque pour les lignes qui passent ce filtre. Une valeur "
                f"modifiée de leur\ncôté ne remontera qu'au prochain passage de sa "
                f"ligne pilote."
            )
            if bound_column and len(aliases) > 1
            else ""
        ),
    )
    ctes = [
        source_cte(a, i, inc if n == 0 else "")
        for n, (a, i) in enumerate(zip(aliases, inputs, strict=True))
    ]

    from_lines = join_from_lines(spec, aliases)

    selected = spec.get("select") or []
    if selected:
        parts = []
        seen: dict[str, str] = {}
        for s in selected:
            src = s.get("from")
            col = s.get("column")
            alias_out = s.get("as") or col
            if src not in aliases:
                raise RecipeError(f"Jointure : dataset « {src} » inconnu.")
            # Comparaison insensible à la casse, comme « Préparer » et
            # « Grouper » : `a.id` et `b.ID` compilaient sans un mot, l'entrepôt
            # renommait la seconde (`ID_1` sur DuckDB), et `output_columns()`
            # annonçait ensuite une colonne qui n'existe pas — ce dont
            # l'analyse d'impact et le contrôle de la colonne incrémentale se
            # servent pour raisonner.
            key = str(alias_out).casefold()
            if key in seen:
                precision = (
                    ""
                    if seen[key][0] == alias_out
                    else (
                        f" — « {seen[key][0]} » et « {alias_out} » ne diffèrent que "
                        f"par la casse, que l'entrepôt ignore"
                    )
                )
                raise RecipeError(
                    f"Deux colonnes de sortie s'appellent « {alias_out} » "
                    f"({seen[key][1]} et {src}.{col}){precision}. Renommez-en une."
                )
            seen[key] = (alias_out, f"{src}.{col}")
            expr = f"{src}.{q(col)}"
            parts.append(
                expr
                if alias_out == col
                else f"{expr} as {q(validate_name(alias_out, 'colonne'))}"
            )
        cols = ",\n        ".join(parts)
    else:
        explicit_columns = _join_star_columns(aliases, columns)
        # Sans colonnes connues — l'entrepôt n'a pas répondu —, on ne peut pas
        # nommer : `a.*` reste le seul SQL possible, et l'atelier n'annonce
        # alors aucune colonne de sortie.
        cols = (
            ",\n        ".join(expr for expr, _ in explicit_columns)
            if explicit_columns
            else ",\n        ".join(f"{a}.*" for a in aliases)
        )

    check_microbatch_output(
        output, _join_output_columns(spec, aliases, columns), "la jointure"
    )

    body = f"    select\n        {cols}\n\n" + "\n".join(from_lines)
    out = free_cte("jointure", set(aliases))
    ctes.append(f"-- jointure\n{out} as (\n\n{body}\n\n)")
    return "with " + ",\n\n".join(ctes) + final_select(out)


AGGREGATIONS = {
    "count": "Compter",
    "count_distinct": "Compter les valeurs distinctes",
    "sum": "Somme",
    "avg": "Moyenne",
    "min": "Minimum",
    "max": "Maximum",
    "median": "Médiane",
}


def _aggregate(fn: str, col: str, where: str = "") -> str:
    """Une mesure, filtrée ou non, écrite dans un SQL que tout entrepôt lit.

    `filter (where …)` est la forme standard, mais ni BigQuery, ni Redshift, ni
    Snowflake ne la connaissent. Le `case` équivaut exactement — un agrégat
    ignore les null — et se lit partout, donc il n'y a qu'une écriture à tenir.
    """
    arg = "*" if col == "*" else q(col)
    if where:
        # `count(*)` compte des lignes : on compte alors une constante, ce qui
        # ne retient que les lignes retenues par le filtre.
        what = "1" if arg == "*" else arg
        arg = f"case when {where} then {what} end"
    if fn == "median":
        return median(arg)
    if fn == "count_distinct":
        return f"count(distinct {arg})"
    return f"{fn}({arg})"


def _group_output_columns(keys: list[str], aggs: list[dict]) -> list[str]:
    """Les colonnes que « Grouper » produit : ses clés, puis ses mesures.

    L'unicité se vérifie ici, une fois pour la compilation et une fois pour ce
    que le compilateur annonce. Deux mesures nommées `total` compilaient sans
    un mot : DuckDB rendait `total` et `total_1`, `output_columns()` annonçait
    `total` deux fois, et l'analyse d'impact assurait ensuite que la colonne
    existait toujours en aval — alors que la seconde mesure y avait changé de
    nom. La jointure et le renommage refusent déjà ce cas.
    """
    names = list(keys) + [
        validate_name(a.get("alias", ""), "mesure") for a in (aggs or [])
    ]
    _check_unique_columns([], names, "Grouper")
    return names


def compile_group(spec: dict, columns: dict[str, list[dict]]):
    inputs = spec.get("inputs") or []
    if len(inputs) != 1:
        raise RecipeError("Une recipe Group prend exactement un dataset.")
    alias = input_alias(inputs[0], 0)
    keys = [k for k in (spec.get("group_by") or []) if str(k).strip()]
    aggs = spec.get("aggregations") or []
    if not aggs:
        raise RecipeError("Group : ajoutez au moins une mesure.")

    lines = [q(k) for k in keys]
    approx = False
    for a in aggs:
        fn = a.get("fn", "count")
        if fn not in AGGREGATIONS:
            raise RecipeError(f"Agrégation inconnue : {fn}")
        col = a.get("column") or "*"
        if col == "*" and fn != "count":
            raise RecipeError(
                f"« {AGGREGATIONS[fn]} » porte sur une colonne : « * » ne compte "
                f"que des lignes, et seul « count » sait le faire."
            )
        where = (a.get("filter") or "").strip()
        if where:
            check_predicate(where, "filtre de mesure")
        expr = _aggregate(fn, col, where)
        approx = approx or (fn == "median" and median_is_approximate())
        lines.append(f"{expr} as {q(validate_name(a.get('alias', ''), 'mesure'))}")

    _group_output_columns(keys, aggs)

    header = ""
    if approx:
        or_ = (
            f"« {current_dialect().adapter} »"
            if current_dialect().adapter
            else "cet entrepôt"
        )
        header = (
            f"-- « médiane » est approchée sur {or_} : il n'a pas de médiane "
            f"exacte.\n        "
        )
    body = (
        "    select\n        "
        + header
        + ",\n        ".join(lines)
        + f"\n\n    from {alias}"
    )
    if keys:
        body += "\n    group by " + ", ".join(str(i + 1) for i in range(len(keys)))

    output = spec.get("output") or {}
    _check_group_microbatch(output, keys)
    check_microbatch_output(
        output,
        keys + [str(a.get("alias") or "") for a in aggs],
        "l'agrégation",
    )

    inc = here = ""
    if incremental_column(output):
        _check_group_incremental(output, keys, aggs)
        # Deux noms libres : le filtre corrélé compare la table lue à
        # elle-même, et les deux côtés doivent pouvoir se nommer.
        held = {alias}
        here = free_cte("entree", held)
        inc = incremental_group_filter(
            output,
            keys,
            inputs[0],
            here,
            free_cte("recent", held),
            _type_of(columns, alias, incremental_column(output)),
        )

    out = free_cte("agrege", {alias})
    ctes = [
        source_cte(alias, inputs[0], inc, here),
        f"-- agrégation\n{out} as (\n\n{body}\n\n)",
    ]
    return "with " + ",\n\n".join(ctes) + final_select(out)


def _check_group_microbatch(out: dict, keys: list[str]) -> None:
    """Refuse une agrégation microbatch dont les groupes traversent les tranches.

    dbt réécrit une tranche de temps entière à chaque exécution. Un groupe qui
    reçoit des lignes dans deux tranches est donc calculé deux fois, sur une
    moitié de ses lignes chaque fois, et les deux totaux partiels se retrouvent
    côte à côte dans la table sans qu'aucun build n'échoue. Ça ne tient que si
    le découpage du temps fait lui-même partie du regroupement.
    """
    cfg = microbatch(out)
    if not cfg:
        return
    col = str(cfg.get("event_time") or "").strip()
    if not col or col in keys:
        return
    raise RecipeError(
        f"Une agrégation « microbatch » doit regrouper par sa colonne de temps : "
        f"« {col} » n'est pas une clé de regroupement. dbt réécrit une tranche "
        f"entière à chaque exécution — un groupe à cheval sur deux tranches "
        f"serait compté deux fois, en deux totaux partiels qui resteraient tous "
        f"les deux. Regroupez par « {col} » (un jour, un mois…), ou passez en "
        f"« delete+insert »."
    )


def _check_group_incremental(out: dict, keys: list[str], aggs: list[dict]) -> None:
    """Refuse les agrégations incrémentales qui ne peuvent pas être justes.

    Recalculer les groupes touchés suppose que le modèle sache remplacer les
    lignes qu'il a déjà écrites pour ces groupes. Sans cela, le total recalculé
    s'ajoute à l'ancien au lieu de le remplacer.
    """
    col = incremental_column(out)
    if not keys:
        raise RecipeError(
            "Une agrégation incrémentale a besoin d'au moins un regroupement : "
            "sans clé de groupe, il n'y a rien à recalculer par morceaux. "
            "Regroupez par une colonne, ou passez la sortie en « table »."
        )

    measures = {str(a.get("alias") or ""): a for a in aggs}
    if col not in keys and col not in measures:
        raise RecipeError(
            f"La colonne incrémentale « {col} » ne sort pas de l'agrégation : "
            f"elle doit être une clé de groupe, ou une mesure "
            f"(« max({col}) as {col} » par exemple). Sinon la borne "
            f"« max({col}) » ne trouve rien à lire dans la table."
        )

    # Le nom de la colonne de sortie ne suffit pas : c'est son *calcul* qui
    # doit garder le sens de la colonne d'entrée. Le filtre compare la valeur
    # qui entre — `seq` d'une ligne — à `max(seq)` lu dans la table, où `seq`
    # peut être n'importe quelle mesure portant ce nom. Avec `sum(seq) as seq`
    # et deux lignes à 10 et 20, la table contient 30 : une ligne nouvelle à 25
    # est alors écartée par `seq > 30`, deux builds réussissent, et le total
    # final est faux de moitié. Seul un `max` non filtré de la colonne
    # elle-même reste sur la même échelle que l'entrée.
    if col in measures:
        a = measures[col]
        fn = a.get("fn", "count")
        source = a.get("column") or "*"
        filter = (a.get("filter") or "").strip()
        if fn != "max" or source != col or filter:
            computation = f"{AGGREGATIONS.get(fn, fn)} de « {source} »"
            raise RecipeError(
                f"La borne incrémentale « {col} » est une mesure calculée "
                f"({computation}"
                + (", filtrée" if filter else "")
                + f") : le filtre compare la valeur qui *entre* dans "
                f"l'agrégation à « max({col}) » lu dans la table, qui ne "
                f"porte alors plus la même grandeur. Des lignes nouvelles "
                f"seraient écartées sans qu'aucun build n'échoue. Écrivez "
                f"« max({col}) as {col} », sans filtre, ou bornez sur une clé "
                f"de groupe, ou passez la sortie en « table »."
            )

    strategy = strategy_of(out)
    if strategy not in ("delete+insert", "merge"):
        raise RecipeError(
            f"La stratégie « {strategy} » ajoute les lignes sans retirer les "
            f"anciennes : un groupe recalculé viendrait s'empiler sur son "
            f"total précédent. Choisissez « delete+insert » ou « merge »."
        )

    unique_keys = [k for k in (out.get("unique_key") or []) if str(k).strip()]

    missing = [k for k in keys if k not in unique_keys]
    if missing:
        what = ", ".join(f"« {k} »" for k in missing)
        raise RecipeError(
            f"Les clés uniques doivent couvrir le regroupement : {what} y "
            f"manque{'nt' if len(missing) > 1 else ''}. Sans cela, dbt ne "
            f"retire pas le total précédent du groupe qu'on recalcule."
        )

    # Et rien de plus que le regroupement. Une clé unique qui porte aussi une
    # mesure — `max(ts) as ts` typiquement — ne retrouve pas la ligne déjà
    # écrite dès que cette mesure change : dbt cherche (client, ts_nouveau),
    # ne trouve rien à retirer, et insère un second total pour le même client.
    # Le premier reste, et la table contient deux totaux concurrents sans
    # qu'aucun build n'échoue.
    extra = [k for k in unique_keys if k not in keys]
    if extra:
        what = ", ".join(f"« {k} »" for k in extra)
        raise RecipeError(
            f"Les clés uniques d'une agrégation ne peuvent porter que le "
            f"regroupement : {what} n'en fai{'t' if len(extra) == 1 else 'sent'} "
            f"pas partie. Une mesure change d'une exécution à l'autre — dbt ne "
            f"retrouverait plus le total déjà écrit, et en ajouterait un second "
            f"pour le même groupe. Retirez {what} des clés uniques, ou "
            f"ajoutez-l{'a' if len(extra) == 1 else 'es'} au regroupement."
        )


def compile_stack(spec: dict, columns: dict[str, list[dict]]):
    inputs = spec.get("inputs") or []
    if len(inputs) < 2:
        raise RecipeError("Une recipe Stack prend au moins deux datasets.")
    aliases = input_aliases(inputs)
    op = "union all" if spec.get("mode", "all") == "all" else "union"

    # Empiler par position casse dès que deux datasets n'ont pas les mêmes
    # colonnes dans le même ordre : on aligne par nom, et ce qui manque d'un
    # côté devient null — c'est ce qu'on attend d'un empilement.
    order: list[str] = []
    per: dict[str, set[str]] = {}
    for a in aliases:
        names = [c["name"] for c in (columns.get(a) or [])]
        per[a] = set(names)
        for n in names:
            if n not in order:
                order.append(n)

    # Le filtre incrémental va sur *chaque* entrée : n'en borner qu'une laissait
    # les autres se réinsérer en entier à chaque exécution, et deux lignes en
    # devenaient trois au deuxième build.
    output = spec.get("output") or {}
    col = incremental_column(output)
    inc = incremental_filter(
        output,
        next(
            (t for a in aliases if (t := _type_of(columns, a, col)) is not None),
            None,
        ),
        # La borne est lue dans la table écrite, donc *commune* aux entrées.
        # Si l'une est arrivée à 100 et l'autre à 10, une ligne nouvelle de la
        # seconde à 11 ne passe jamais. Le calcul n'est juste que sous une
        # hypothèse d'alignement que l'atelier ne peut pas vérifier : autant
        # qu'elle soit écrite là où on la lira.
        note=(
            (
                f"cette borne est la même pour les {len(aliases)} entrées : elle "
                f"suppose qu'elles\navancent ensemble sur « {col} ». Une entrée en "
                f"retard sur une autre\nperdrait ses lignes — une fenêtre de reprise "
                f"les rattrape."
            )
            if col
            else ""
        ),
    )
    if inc:
        # Une entrée sans la colonne de borne n'est pas filtrable : plutôt que
        # de la réempiler en silence, on le dit avant d'écrire le modèle.
        silent = [a for a in aliases if per[a] and col not in per[a]]
        if silent:
            what = ", ".join(f"« {a} »" for a in silent)
            raise RecipeError(
                f"La colonne incrémentale « {col} » manque à {what} : sans "
                f"elle, ce dataset serait réempilé en entier à chaque "
                f"exécution. Ajoutez la colonne, ou passez la sortie en "
                f"« table »."
            )
    _check_stack_types(aliases, order, columns)
    check_microbatch_output(output, order, "l'empilement")
    ctes = [source_cte(a, i, inc) for a, i in zip(aliases, inputs, strict=True)]

    if order:
        selects = []
        for a in aliases:
            cols = ",\n        ".join(
                q(n) if n in per[a] else f"null as {q(n)}" for n in order
            )
            selects.append(f"    select\n        {cols}\n    from {a}")
        body = f"\n    {op}\n".join(selects)
    else:
        body = f"\n    {op}\n".join(f"    select * from {a}" for a in aliases)
    out = free_cte("empile", set(aliases))
    ctes.append(f"-- empilement\n{out} as (\n\n{body}\n\n)")
    return "with " + ",\n\n".join(ctes) + final_select(out)


def _check_stack_types(
    aliases: list[str], order: list[str], columns: dict[str, list[dict]]
) -> None:
    """Empiler aligne par nom : encore faut-il que les valeurs soient du même genre.

    `id` INTEGER d'un côté, `id` VARCHAR de l'autre : DuckDB rend une colonne
    texte contenant « 1 » et « abc », d'autres moteurs refusent la requête. Le
    résultat n'est faux nulle part en particulier, il est faux partout — et
    l'écran ne montrait que la *présence* des colonnes. « Dépivoter » pose
    déjà exactement ce refus ; l'empilement, qui fait la même union, ne
    l'avait pas.
    """
    for name in order:
        families: dict[str, list[str]] = {}
        for a in aliases:
            t = _type_of(columns, a, name)
            f = type_family(t)
            if not f:
                continue  # type inconnu : on ne juge pas
            families.setdefault(f, []).append(a)
        if len(families) > 1:
            detail = " ; ".join(
                f"{f} dans {_listed(sorted(ds))}" for f, ds in sorted(families.items())
            )
            raise RecipeError(
                f"La colonne « {name} » ne porte pas le même genre de valeur d'une "
                f"entrée à l'autre ({detail}). Empilées, l'entrepôt refuserait la "
                f"requête — ou pire, convertirait en silence. Alignez les types "
                f"avec « Changer le type » dans une recipe « Préparer » avant "
                f"d'empiler, ou renommez l'une des deux colonnes."
            )


def compile_sql(spec: dict, columns: dict[str, list[dict]]):
    sql = (spec.get("sql") or "").strip()
    if not sql:
        raise RecipeError("La recipe SQL est vide.")
    _check_sql_incremental(spec.get("output") or {})
    return sql + ("\n" if not sql.endswith("\n") else "")


def _check_sql_incremental(out: dict) -> None:
    """Une recipe SQL ne peut pas recevoir de borne : elle l'écrit elle-même.

    Les quatre autres compilateurs posent le `where` incrémental parce qu'ils
    savent où est le CTE d'entrée. `compile_sql` rend le texte de
    l'utilisateur tel quel : il n'a nulle part où poser quoi que ce soit. La
    colonne de repère et la fenêtre de reprise étaient pourtant offertes et
    décrites à l'écran — « chaque exécution ne lira que les lignes dont `ts`
    dépasse le maximum déjà présent » — et rien de tout cela n'arrivait dans le
    fichier. Avec `append`, le modèle réinsérait la table entière à chaque
    build : elle doublait, puis triplait, sans qu'aucun build n'échoue.

    On refuse donc le réglage plutôt que de le taire, avec le texte à écrire à
    la main. L'écran, de son côté, ne l'offre plus pour ce type de recipe.
    """
    if (out.get("materialized") or "view") != "incremental":
        return
    col = ((out.get("incremental") or {}).get("column") or "").strip()
    window = ((out.get("incremental") or {}).get("lookback") or {}).get("n")
    if not col and not window:
        return
    bound_column = col or "votre colonne de repère"
    raise RecipeError(
        f"Une recipe « SQL » écrit elle-même sa borne incrémentale : l'atelier "
        f"ne peut pas poser de filtre dans un SQL qu'il ne compose pas, et "
        f"« {bound_column} » n'arriverait donc jamais dans le fichier — en « append », "
        f"la table entière serait réinsérée à chaque exécution. Retirez la "
        f"colonne de repère et la fenêtre de reprise, et écrivez le filtre dans "
        f"votre requête :\n"
        f"    {{% if is_incremental() %}}\n"
        f"    where {col or 'ts'} > (select max({col or 'ts'}) from {{{{ this }}}})\n"
        f"    {{% endif %}}"
    )


COMPILERS = {
    "prepare": lambda s, c: compile_prepare(s, c)[0],
    "join": compile_join,
    "group": compile_group,
    "stack": compile_stack,
    "sql": compile_sql,
}


def output_columns(spec: dict, columns: dict[str, list[dict]]) -> list[str] | None:
    """Les colonnes que la recipe produit, sans rien demander à l'entrepôt.

    `None` quand on ne peut pas savoir : une recipe SQL est écrite à la main,
    et deviner ses colonnes demanderait de la compiler pour de vrai.
    """
    rtype = spec.get("type", "prepare")
    inputs = spec.get("inputs") or []
    try:
        if rtype == "prepare":
            _, state, _ = compile_prepare(spec, columns)
            return list(state.columns)
        if rtype == "join":
            aliases = input_aliases(inputs)
            return _join_output_columns(spec, aliases, columns)
        if rtype == "group":
            keys = [k for k in (spec.get("group_by") or []) if str(k).strip()]
            aggs = [a for a in (spec.get("aggregations") or []) if a.get("alias")]
            return _group_output_columns(keys, aggs)
        if rtype == "stack":
            order: list[str] = []
            for n, inp in enumerate(inputs):
                for c in columns.get(input_alias(inp, n)) or []:
                    if c["name"] not in order:
                        order.append(c["name"])
            return order
    except RecipeError:
        return None
    return None


def _column_origins(spec: dict, columns: dict[str, list[dict]]) -> dict[str, str]:
    """Sortie → identité stable, celle qui ne bouge pas quand le nom change.

    Une colonne renommée est la *même* colonne : ce qui l'identifie est ce dont
    elle sort — la colonne d'entrée dont elle descend, la mesure qui la calcule
    — et pas le nom qu'elle porte à l'arrivée. C'est ce qui permet de dire
    « amount_eur est devenue montant_eur » plutôt que « une colonne a disparu,
    une autre est apparue ».

    `{}` quand on ne peut pas savoir : une recipe SQL est écrite à la main.
    """
    output = output_columns(spec, columns)
    if output is None:
        return {}
    rtype = spec.get("type", "prepare")

    if rtype == "prepare":
        # Les renommages se composent : `a → b` puis `b → c` fait descendre `c`
        # de `a`, et une étape désactivée ne compte pas — le SQL non plus ne la
        # compte pas.
        string: dict[str, str] = {}
        for step in spec.get("steps") or []:
            if not step.get("enabled", True) or step.get("type") != "rename":
                continue
            for r in (step.get("params") or {}).get("renames") or []:
                src, dst = r.get("from"), r.get("to")
                if not src or not dst or src == dst:
                    continue
                string[dst] = string.pop(src, src)
        return {c: string.get(c, c) for c in output}

    if rtype == "join":
        selection = spec.get("select") or []
        if selection:
            return {
                (s.get("as") or s.get("column")): f"{s.get('from') or ''}.{s['column']}"
                for s in selection
                if s.get("column")
            }
        return {c: c for c in output}

    if rtype == "group":
        origins = {k: k for k in (spec.get("group_by") or []) if str(k).strip()}
        for a in spec.get("aggregations") or []:
            alias = str(a.get("alias") or "")
            if alias:
                origins[alias] = (
                    f"{a.get('fn', 'count')}({a.get('column') or '*'})"
                    f"[{(a.get('filter') or '').strip()}]"
                )
        return origins

    return {c: c for c in output}


def renamed_columns(
    before: dict, after: dict, columns: dict[str, list[dict]]
) -> dict[str, str]:
    """Les colonnes que cette version renomme, ancien nom → nouveau nom.

    Seulement celles dont on est sûr : même identité des deux côtés, l'ancien
    nom vraiment disparu de la sortie, le nouveau vraiment neuf. Une identité
    qui apparaît deux fois ne dit rien — deux mesures identiques à l'alias près
    ne se distinguent pas — et on préfère ne rien proposer que proposer à
    côté.
    """
    if before.get("type") != after.get("type"):
        return {}
    before_origins, after_origins = (
        _column_origins(before, columns),
        _column_origins(after, columns),
    )
    if not before_origins or not after_origins:
        return {}

    def without_duplicates(m: dict[str, str]) -> dict[str, str]:
        seen: dict[str, str | None] = {}
        for name, origin in m.items():
            seen[origin] = None if origin in seen else name
        return {o: n for o, n in seen.items() if n is not None}

    by_origin = without_duplicates(after_origins)
    out: dict[str, str] = {}
    for origin, name in without_duplicates(before_origins).items():
        fresh = by_origin.get(origin)
        if (
            fresh
            and fresh != name
            and name not in after_origins
            and fresh not in before_origins
        ):
            out[name] = fresh
    return out


def compile_recipe(spec: dict, columns: dict[str, list[dict]]) -> str:
    """Recipe → SQL dbt complet, config incluse."""
    rtype = spec.get("type", "prepare")
    fn = COMPILERS.get(rtype)
    if fn is None:
        meta = RECIPE_TYPES.get(rtype) or {}
        # Un type du contrat public qui n'a pas de compilateur est un raccourci
        # d'interface, pas un type de recipe : il faut dire par quoi le
        # remplacer, et non « non géré ».
        if meta.get("alias_of"):
            step = meta.get("alias_step")
            raise RecipeError(
                f"« {meta.get('label', rtype)} » est un raccourci de l'écran, "
                f"pas un type de recipe : envoyez une recipe "
                f"« {RECIPE_TYPES[meta['alias_of']]['label']} »"
                + (f" avec une étape « {step} »." if step else ".")
            )
        raise RecipeError(f"Type de recipe non géré : {rtype}")
    body = fn(spec, columns)
    _check_unique_keys_in_output(spec, columns)

    header = config_block(spec.get("output") or {})
    banner = (
        f"-- Recipe « {RECIPE_TYPES.get(rtype, {}).get('label', rtype)} » "
        f"de l'atelier Pliq.\n"
        f"-- Script visuel : {RECIPE_DIR}/{spec.get('name')}.yml\n"
        f"-- Ce fichier reste la source de vérité : il s'édite aussi à la main.\n\n"
    )
    return header + banner + body


def _check_unique_keys_in_output(spec: dict, columns: dict[str, list[dict]]) -> None:
    """Une clé unique qui ne sort pas du script casse au deuxième build.

    Le premier build crée la table — `is_incremental()` est faux, dbt ne
    cherche aucune clé. C'est le suivant qui échoue, en cherchant dans le
    résultat une colonne qu'une étape a retirée ou renommée entre-temps. Le
    contrôle existait pour la colonne de *borne* ; il manquait pour les clés,
    alors que c'est le même piège et la même distance entre la faute et
    l'erreur.

    `output_columns` rend `None` quand on ne peut pas savoir — une recipe SQL,
    des colonnes d'entrée que l'entrepôt n'a pas données : on ne refuse alors
    rien, faute de quoi comparer.
    """
    out = spec.get("output") or {}
    if (out.get("materialized") or "view") != "incremental":
        return
    keys = [str(k).strip() for k in (out.get("unique_key") or []) if str(k).strip()]
    if not keys:
        return
    try:
        produced = output_columns(spec, columns)
    except RecipeError:
        return  # le refus vrai vient d'ailleurs, et il est plus précis
    if not produced:
        return
    produced_folded = {c.casefold() for c in produced}
    missing = [k for k in keys if k.casefold() not in produced_folded]
    if not missing:
        return
    raise RecipeError(
        f"{_listed(missing)} ne sort pas de ce script, et sert pourtant de "
        f"clé unique : dbt la cherchera dans le résultat pour savoir quelles "
        f"lignes remplacer. Le premier build passerait — la table est créée —, "
        f"le suivant non. Gardez "
        f"{'ces colonnes' if len(missing) > 1 else 'cette colonne'} dans le "
        f"résultat, ou changez les clés uniques. "
        f"Colonnes produites : {', '.join(produced)}."
    )


def rename_banner(sql: str, old: str, new: str) -> str:
    """Le renvoi vers le script visuel, dans l'en-tête du SQL généré.

    Un renommage ne recompile pas le modèle : son SQL ne porte pas son nom, dbt
    le tient du nom de fichier. Sauf cette ligne-là, qui renverrait sinon vers
    un script visuel qui n'existe plus.
    """
    return sql.replace(
        f"-- Script visuel : {RECIPE_DIR}/{old}.yml",
        f"-- Script visuel : {RECIPE_DIR}/{new}.yml",
    )
