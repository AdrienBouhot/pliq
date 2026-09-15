"""Le `{{ config(...) }}` d'un modèle, et toute la machinerie incrémentale.

C'est la partie qui décide *ce que dbt fera* du modèle, par opposition aux
compilateurs qui décident ce qu'il contient. L'incrémental y tient la plus
grande place, parce que c'est là que les erreurs ne se voient qu'au deuxième
build.
"""

from __future__ import annotations

from datetime import datetime

from .errors import RecipeError
from .names import validate_name
from .sql import q
from .refs import ref_sql
from .dialect import current_dialect
from .writers import date_minus
from .vocabulary import (
    BATCH_SIZES,
    DEFAULT_STRATEGY_BY_FAMILY,
    DEFAULT_STRATEGY,
    INCREMENTAL_STRATEGIES,
    LOOKBACK_UNITS,
    MATERIALIZATIONS,
    ON_SCHEMA_CHANGE,
    STRATEGIES_NEEDING_KEY,
    STRATEGIES_BY_FAMILY,
)


def default_strategy() -> str:
    """La stratégie incrémentale par défaut de l'entrepôt du projet.

    `delete+insert` partout n'était pas un défaut, c'était un pari : BigQuery
    ne la connaît pas, et le modèle échouait au `dbt build` alors que rien
    dans l'atelier n'avait signalé quoi que ce soit.
    """
    return DEFAULT_STRATEGY_BY_FAMILY.get(current_dialect().family, DEFAULT_STRATEGY)


def strategy_of(out: dict) -> str:
    """La stratégie que dbt appliquera, défaut de l'entrepôt compris."""
    return (out.get("incremental_strategy") or "").strip() or default_strategy()


def strategies_available() -> tuple[str, ...]:
    """Les stratégies incrémentales disponibles sur l'entrepôt du projet."""
    return STRATEGIES_BY_FAMILY.get(current_dialect().family, INCREMENTAL_STRATEGIES)


def check_strategy_supported(strategy: str) -> None:
    """Refuse une stratégie que l'adaptateur du projet ne sait pas exécuter."""
    available = STRATEGIES_BY_FAMILY.get(current_dialect().family)
    if not available or strategy in available:
        return
    warehouse = (
        f"« {current_dialect().adapter} »"
        if current_dialect().adapter
        else "cet entrepôt"
    )
    raise RecipeError(
        f"La stratégie « {strategy} » n'existe pas sur {warehouse} : dbt "
        f"refuserait le modèle à la construction. Au choix ici : "
        f"{', '.join(f'« {s} »' for s in available)}."
    )


def _py_lit(value) -> str:
    """Une valeur, écrite en littéral Jinja que le parseur relit telle quelle.

    Le `{{ config(...) }}` est du Jinja avant d'être du SQL : une apostrophe
    dans un tag — « chiffre d'affaires » — ferme la chaîne au milieu du mot et
    le fichier ne parse plus. Les tags sont la seule valeur du bloc qui ne
    passe ni par `validate_name` ni par une liste fermée : c'est du texte libre
    saisi dans l'atelier, il doit être sérialisé, pas interpolé.

    `repr` d'une `str` Python donne exactement le littéral que Jinja attend,
    apostrophes et antislashs échappés.
    """
    return repr(str(value))


def _py_list(values: list) -> str:
    return "[" + ", ".join(_py_lit(v) for v in values) + "]"


def config_block(out: dict) -> str:
    """Le `{{ config(...) }}` du modèle, incrémental compris."""
    mat = out.get("materialized") or "view"
    if mat not in MATERIALIZATIONS:
        raise RecipeError(f"Matérialisation inconnue : {mat}")

    entries: list[str] = []
    # Un « view » choisi dans l'interface s'écrit, sinon la couche le
    # matérialise en table via dbt_project.yml et le choix est perdu. Une
    # recipe qui ne dit rien, elle, laisse la main au projet.
    if mat != "view" or out.get("materialized"):
        entries.append(f"materialized='{mat}'")

    if mat == "incremental":
        strategy = strategy_of(out)
        if strategy not in INCREMENTAL_STRATEGIES:
            raise RecipeError(f"Stratégie incrémentale inconnue : {strategy}")
        check_strategy_supported(strategy)
        entries.append(f"incremental_strategy='{strategy}'")

        if strategy == "microbatch":
            # Pas de `unique_key` : microbatch réécrit une tranche de temps
            # entière, il ne rapproche pas les lignes une à une.
            entries.extend(microbatch_entries(out))
        else:
            keys = [validate_name(k, "clé") for k in (out.get("unique_key") or []) if k]
            if strategy in STRATEGIES_NEEDING_KEY and not keys:
                raise RecipeError(
                    f"La stratégie « {strategy} » a besoin d'au moins une clé unique : "
                    f"sans elle, dbt ne sait pas quelles lignes remplacer."
                )
            if keys:
                entries.append(
                    "unique_key="
                    + (_py_list(keys) if len(keys) > 1 else _py_lit(keys[0]))
                )

        osc = out.get("on_schema_change") or "append_new_columns"
        if osc not in ON_SCHEMA_CHANGE:
            raise RecipeError(f"Valeur on_schema_change inconnue : {osc}")
        entries.append(f"on_schema_change='{osc}'")

    tags = [t for t in (out.get("tags") or []) if str(t).strip()]
    if tags:
        entries.append("tags=" + _py_list(tags))

    if not entries:
        return ""
    if len(entries) == 1:
        return "{{ config(" + entries[0] + ") }}\n\n"
    return "{{ config(\n    " + ",\n    ".join(entries) + "\n) }}\n\n"


def microbatch(out: dict) -> dict:
    """Les réglages microbatch de la sortie, vide si ce n'est pas la stratégie."""
    if (out.get("materialized") or "view") != "incremental":
        return {}
    if strategy_of(out) != "microbatch":
        return {}
    return out.get("microbatch") or {}


def microbatch_entries(out: dict) -> list[str]:
    """Les réglages que dbt exige d'un modèle microbatch.

    L'atelier proposait la stratégie sans jamais les écrire : le modèle produit
    ne parsait pas. Les règles rejouées ici sont exactement celles de dbt
    (`check_valid_microbatch_config`) — on les dit en clair, avant d'écrire le
    fichier, plutôt que de laisser dbt les découvrir après.
    """
    cfg = out.get("microbatch") or {}

    col = str(cfg.get("event_time") or "").strip()
    if not col:
        raise RecipeError(
            "La stratégie « microbatch » a besoin d'une colonne de temps : "
            "c'est elle qui découpe le travail en tranches, et c'est elle que "
            "dbt réécrit tranche par tranche. Choisissez-la dans « Sortie », "
            "ou prenez une autre stratégie."
        )
    validate_name(col, "colonne de temps")

    begin = str(cfg.get("begin") or "").strip()
    if not begin:
        raise RecipeError(
            "La stratégie « microbatch » a besoin d'une date de départ : dbt ne "
            "construit aucune tranche antérieure. Format ISO — 2024-01-01, ou "
            "2024-01-01 00:00:00."
        )
    try:
        datetime.fromisoformat(begin)
    except ValueError:
        raise RecipeError(
            f"Date de départ invalide : « {begin} ». dbt attend une date ISO — "
            f"2024-01-01, ou 2024-01-01 00:00:00."
        ) from None

    size = str(cfg.get("batch_size") or "").strip()
    if size not in BATCH_SIZES:
        raise RecipeError(
            f"Taille de tranche inconnue : « {size or '(vide)'} ». dbt en "
            f"accepte quatre : {', '.join(BATCH_SIZES)}."
        )

    entries = [f"event_time='{col}'", f"begin='{begin}'", f"batch_size='{size}'"]

    lookback = cfg.get("lookback")
    if lookback not in (None, ""):
        try:
            n = int(lookback)
        except (TypeError, ValueError):
            raise RecipeError(
                f"Reprise de tranches invalide : « {lookback} ». C'est un "
                f"nombre entier de tranches à recalculer en plus de la dernière."
            ) from None
        if n < 0:
            raise RecipeError("La reprise de tranches ne peut pas être négative.")
        entries.append(f"lookback={n}")

    if cfg.get("concurrent_batches") is not None:
        entries.append(
            "concurrent_batches=" + ("True" if cfg["concurrent_batches"] else "False")
        )
    return entries


def check_microbatch_output(out: dict, columns: list[str], what: str) -> None:
    """La colonne de temps d'un microbatch doit sortir du modèle.

    dbt s'en sert deux fois : pour découper le travail, et pour retirer de la
    table la tranche qu'il s'apprête à réécrire. Absente de la sortie, le
    `delete` de la matérialisation ne trouve pas sa colonne et le build casse.
    """
    cfg = microbatch(out)
    col = str(cfg.get("event_time") or "").strip()
    if not col or not columns or col in columns:
        return
    raise RecipeError(
        f"La colonne de temps « {col} » ne sort pas de {what} : dbt s'en sert "
        f"pour retirer la tranche qu'il réécrit, elle doit donc figurer dans "
        f"le résultat. Colonnes produites : {', '.join(columns)}."
    )


def lookback_window(out: dict) -> tuple[int, str] | None:
    """La fenêtre de reprise d'un incrémental classique, si elle est réglée."""
    cfg = (out.get("incremental") or {}).get("lookback") or {}
    raw_text = cfg.get("n")
    if raw_text in (None, ""):
        return None
    try:
        n = int(raw_text)
    except (TypeError, ValueError):
        raise RecipeError(
            f"Fenêtre de reprise invalide : « {raw_text} ». C'est un nombre entier."
        ) from None
    if n < 0:
        raise RecipeError("La fenêtre de reprise ne peut pas être négative.")
    if n == 0:
        return None
    # Relire une fenêtre suppose de pouvoir remplacer ce qu'on relit. En
    # `append`, les lignes reviennent s'ajouter à celles déjà écrites : la
    # fenêtre ne rattrape pas les retards, elle fabrique des doublons.
    if strategy_of(out) == "append":
        raise RecipeError(
            "Une fenêtre de reprise ne peut pas aller avec la stratégie "
            "« append » : les lignes relues seraient réinsérées à côté de "
            "celles déjà écrites, sans rien remplacer. Passez en "
            "« delete+insert » ou « merge », ou retirez la fenêtre."
        )
    unit = str(cfg.get("unit") or "day").strip().lower()
    if unit not in LOOKBACK_UNITS:
        raise RecipeError(
            f"Unité de reprise inconnue : « {unit} ». "
            f"Au choix : {', '.join(LOOKBACK_UNITS)}."
        )
    return n, unit


def incremental_predicate(
    out: dict,
    qualifier: str = "",
    joins_it: str = " or ",
    col_type: str | None = None,
) -> str:
    """La condition qui borne les lignes à relire, sans `where` ni Jinja.

    `qualifier` préfixe la colonne quand la condition part dans une
    sous-requête corrélée, où deux tables portent le même nom de colonne.
    `joins_it` sépare les deux moitiés : sur une ligne, ou sur deux quand la
    condition tient toute la place d'un `where`. `col_type` ne sert qu'à la
    fenêtre de reprise, et seulement sur BigQuery.
    """
    col = incremental_column(out)
    if not col:
        return ""
    inclusive = (out.get("incremental") or {}).get("operator") == "gte"
    # Une borne large relit les lignes assises sur le maximum déjà écrit. C'est
    # la fenêtre de reprise, de largeur zéro — et en `append`, elle fabrique les
    # mêmes doublons : les lignes reviennent s'ajouter à celles qui sont là. Il
    # n'y a pas besoin d'une étape pour que ça arrive, un script vide suffit.
    if inclusive and strategy_of(out) == "append":
        raise RecipeError(
            "Une borne « ≥ » relit les lignes déjà écrites qui portent la "
            "dernière valeur, et « append » les ajoute une seconde fois : la "
            "table gagne un doublon à chaque exécution, sans qu'aucun build "
            "n'échoue. Prenez la borne « > », ou une stratégie qui remplace "
            "par clé — « delete+insert » ou « merge »."
        )
    op = ">=" if inclusive else ">"
    ref = f"{qualifier}.{q(col)}" if qualifier else q(col)
    bound_column = f"(select max({q(col)}) from {{{{ this }}}})"
    window = lookback_window(out)
    if window:
        # La borne recule : une ligne arrivée en retard porte une date
        # antérieure au maximum déjà écrit, et un `> max` strict la laisserait
        # dehors définitivement. Les lignes relues une seconde fois sont
        # remplacées par la clé unique — sauf en `append`, que l'interface
        # signale.
        bound_column = date_minus(bound_column, window[0], window[1], col_type)
    # Une table incrémentale vide n'a pas de maximum : sans le test `is null`,
    # le filtre compare à NULL, ne laisse jamais passer une ligne, et la table
    # reste vide pour toujours.
    return joins_it.join([f"{bound_column} is null", f"{ref} {op} {bound_column}"])


def incremental_where(
    out: dict, indent: str = "    ", col_type: str | None = None
) -> str:
    """Le `where` qui borne les lignes à relire, sans le bloc Jinja autour."""
    predicate = incremental_predicate(
        out, joins_it=f"\n{indent}   or ", col_type=col_type
    )
    return f"{indent}where {predicate}\n" if predicate else ""


def incremental_filter(out: dict, col_type: str | None = None, note: str = "") -> str:
    """Le bloc qui évite de relire tout l'historique à chaque exécution.

    `note` ajoute une ligne de commentaire au-dessus du filtre. Elle sert à
    dire, dans le fichier que l'utilisateur relit, l'hypothèse sous laquelle
    la borne est juste — un empilement l'applique à toutes ses entrées, et
    cette hypothèse-là ne se voit nulle part ailleurs.
    """
    where = incremental_where(out, col_type=col_type)
    if not where:
        return ""
    lines = "".join(f"    -- {line}\n" for line in (note.splitlines() if note else []))
    return (
        "\n    {% if is_incremental() %}\n"
        "    -- seulement ce qui est arrivé depuis la dernière exécution\n"
        f"{lines}"
        f"{where}"
        "    {% endif %}\n"
    )


def incremental_column(out: dict) -> str:
    """La colonne qui borne un modèle incrémental, vide s'il n'y en a pas.

    Vide aussi en `microbatch` : c'est dbt qui borne alors, tranche par
    tranche, à partir d'`event_time`. Un `where col > max(col)` écrit en plus
    ne calculerait pas la même chose — sur une tranche de rattrapage, `max`
    porte sur toute la table et dépasse déjà la fenêtre de la tranche, si bien
    que le filtre la viderait entièrement.
    """
    if (out.get("materialized") or "view") != "incremental":
        return ""
    if strategy_of(out) == "microbatch":
        return ""
    return ((out.get("incremental") or {}).get("column") or "").strip()


def incremental_group_filter(
    out: dict,
    keys: list[str],
    inp: dict,
    here: str,
    recent: str,
    col_type: str | None = None,
) -> str:
    """Le filtre incrémental d'une agrégation : par *groupe*, pas par ligne.

    Filtrer les lignes avant de regrouper donne un total qui ne porte que sur
    l'arrivage du jour. Avec `delete+insert`, ce total partiel écrase celui de
    la veille au lieu de s'y ajouter, et l'historique disparaît sans qu'aucun
    build n'échoue. On reprend donc en entier tout groupe qui a reçu une ligne
    depuis la dernière exécution, pour le recalculer sur la totalité de ses
    lignes.

    Le rapprochement s'écrit `exists` et pas `(a, b) in (select a, b …)` :
    comparer un couple à une sous-requête est refusé par Redshift, et demande
    un `select as struct` sur BigQuery. Un `exists` corrélé, lui, se lit
    partout.

    L'égalité n'est pas rendue insensible aux null exprès : dbt, lui, rapproche
    les clés de `delete+insert` à l'égalité simple. Un groupe de clé nulle
    serait donc recalculé et inséré sans que l'ancien soit retiré — un doublon.
    Il reste figé, comme dbt le laisse.
    """
    predicate = incremental_predicate(out, qualifier=recent, col_type=col_type)
    if not predicate:
        return ""
    equalities = "\n          and ".join(
        f"{recent}.{q(k)} = {here}.{q(k)}" for k in keys
    )
    return (
        "\n    {% if is_incremental() %}\n"
        "    -- les groupes touchés depuis la dernière exécution, en entier\n"
        "    -- un groupe dont la clé est nulle n'est pas rapproché : SQL ne\n"
        "    -- considère pas null égal à null, et dbt ne le fait pas non plus\n"
        "    -- pour ses clés uniques. Un tel groupe reste figé sur sa valeur\n"
        "    -- déjà écrite. Posez un test « not_null » sur les clés de\n"
        "    -- regroupement pour que ce cas se voie.\n"
        "    where exists (\n"
        "        select 1\n"
        f"        from {ref_sql(inp)} as {recent}\n"
        f"        where {equalities}\n"
        f"          and ({predicate})\n"
        "    )\n"
        "    {% endif %}\n"
    )
