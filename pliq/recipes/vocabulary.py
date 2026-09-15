"""Les valeurs fermées que dbt impose, et que l'interface propose.

Des listes, rien d'autre. Elles sont lues des deux côtés de la pile — le
compilateur borne ce qu'il écrit, l'interface borne ce qu'elle offre — et
ne dépendent de rien : elles vivent donc sous tout le monde.
"""

from __future__ import annotations

MATERIALIZATIONS = ("view", "table", "incremental", "ephemeral")
INCREMENTAL_STRATEGIES = ("append", "delete+insert", "merge", "microbatch")
# Ces stratégies remplacent des lignes existantes : sans clé, elles ne savent
# pas lesquelles.
STRATEGIES_NEEDING_KEY = ("delete+insert", "merge")
ON_SCHEMA_CHANGE = ("append_new_columns", "sync_all_columns", "ignore", "fail")

# Toutes les stratégies ne sont pas disponibles partout. L'atelier les
# proposait toutes, et choisissait `delete+insert` pour tout le monde : une
# recipe se configurait normalement, puis échouait au `dbt build` avec un
# message de dbt sur une stratégie que l'adaptateur ne connaît pas.
#
# La matrice est celle de la documentation dbt :
# https://docs.getdbt.com/docs/build/incremental-strategy
# Une famille absente de ce tableau n'est pas contrainte : mieux vaut laisser
# passer une combinaison qu'on ne connaît pas que refuser une qui marche.
STRATEGIES_BY_FAMILY = {
    "duckdb": ("append", "delete+insert", "merge", "microbatch"),
    "postgres": ("append", "delete+insert", "microbatch"),
    "redshift": ("append", "delete+insert", "merge", "microbatch"),
    "snowflake": ("append", "delete+insert", "merge", "microbatch"),
    "bigquery": ("merge", "microbatch"),
    "trino": ("append", "merge"),
}

# Le défaut par famille : la première stratégie qui remplace des lignes par
# clé, puisque c'est ce que l'atelier écrit dans la quasi-totalité des cas.
DEFAULT_STRATEGY_BY_FAMILY = {
    "bigquery": "merge",
    "trino": "merge",
}

DEFAULT_STRATEGY = "delete+insert"

# `microbatch` ne se règle pas comme les autres stratégies : dbt y découpe
# lui-même le travail en tranches de temps, et refuse de parser le projet tant
# qu'il ne sait pas sur quelle colonne (`event_time`), depuis quand (`begin`)
# et à quelle taille de tranche (`batch_size`). Les valeurs sont celles de dbt
# — `dbt.artifacts.resources.types.BatchSize` — et non un choix de l'atelier.
BATCH_SIZES = ("hour", "day", "month", "year")

# La fenêtre de reprise d'un incrémental classique. Une ligne arrivée en retard
# porte une date antérieure au maximum déjà écrit : un `> max(col)` strict la
# laisse dehors pour toujours, sans qu'aucun build n'échoue. Reculer la borne
# de quelques jours la rattrape.
LOOKBACK_UNITS = ("hour", "day")

# Les opérateurs du filtre « sur une valeur », et comment ils se lisent.
#
# Une seule liste, et c'est l'enjeu : l'interface en portait une copie, dans
# son propre ordre, avec ses propres libellés, et un test se chargeait de
# vérifier que les deux restaient d'accord. Un test qui surveille une
# duplication ne la remplace pas — il en fait une dette qu'on entretient.
# `/api/processors` sert celle-ci, l'écran la déroule, et `describe_step` la
# relit pour écrire la phrase de la carte : le libellé du menu et celui de la
# carte ne peuvent plus diverger.
#
# L'ordre est celui du menu déroulant : égalités, appartenance, texte,
# comparaisons, vide.
FILTER_OPERATORS = {
    "eq": "vaut",
    "ne": "ne vaut pas",
    "in": "est dans",
    "not_in": "n'est pas dans",
    "contains": "contient",
    "starts_with": "commence par",
    "ends_with": "finit par",
    "gt": ">",
    "gte": "≥",
    "lt": "<",
    "lte": "≤",
    "empty": "est vide",
    "not_empty": "n'est pas vide",
}

# Les opérateurs qui comparent à *une* valeur. L'écran n'offre qu'un champ pour
# eux, et le compilateur refuse la seconde valeur plutôt que de la jeter en
# silence — c'est la même liste des deux côtés, pour la même raison que
# `FILTER_OPERATORS`.
SINGLE_VALUE_OPERATORS = (
    "eq",
    "ne",
    "contains",
    "starts_with",
    "ends_with",
    "gt",
    "gte",
    "lt",
    "lte",
)

# Les opérateurs qui ne comparent à rien : l'écran n'affiche aucun champ.
NO_VALUE_OPERATORS = ("empty", "not_empty")

# Ce qu'un pivot fait des valeurs qui tombent dans la même case. Le libellé dit
# le geste plutôt que la fonction : « en garder une (max) » se comprend sans
# savoir ce que `max` fait d'un texte.
PIVOT_AGGREGATIONS = {
    "max": "en garder une (max)",
    "min": "en garder une (min)",
    "sum": "les additionner",
    "avg": "en faire la moyenne",
    "count": "les compter",
}

# Les types de recipe, tels que `/api/processors` les sert.
#
# `alias_of` dit qu'un type n'a pas de compilateur à lui : c'est un raccourci
# d'interface, que l'écran réécrit dans le type visé avant d'envoyer quoi que
# ce soit. Sans cette mention, un client qui lit le contrat public — ce que le
# produit l'invite à faire — croyait `distinct` utilisable, et `compile_recipe`
# lui répondait « Type de recipe non géré ». `topn`, lui, n'était implémenté
# ni offert nulle part : il est retiré plutôt que documenté.
RECIPE_TYPES = {
    "prepare": {"label": "Préparer", "icon": "broom", "inputs": "1"},
    "join": {"label": "Joindre", "icon": "join", "inputs": "2+"},
    "group": {"label": "Grouper", "icon": "group", "inputs": "1"},
    "stack": {"label": "Empiler", "icon": "stack", "inputs": "2+"},
    "distinct": {
        "label": "Dédoublonner",
        "icon": "distinct",
        "inputs": "1",
        "alias_of": "prepare",
        "alias_step": "distinct_rows",
    },
    "sql": {"label": "SQL", "icon": "sql", "inputs": "1+"},
}

# Les types qu'un client peut réellement envoyer à `compile_recipe`.
COMPILABLE_RECIPE_TYPES = tuple(
    t for t, meta in RECIPE_TYPES.items() if not meta.get("alias_of")
)

# Les réglages d'étape qui portent des listes d'objets, et non des noms.
#
# `check_spec_shape` juge la charpente d'une recipe sans rien savoir des
# étapes ; il lui manquait l'étage du dessous. La plupart des listes de
# `params` sont des listes de colonnes — des textes — et un objet qui s'y
# glisse finit en `RecipeError` de l'étape, qui sait le dire. Celles-ci sont
# l'inverse : le compilateur y lit `item.get(...)` sans se demander ce qu'est
# `item`, si bien qu'un `null` ou un texte y devenait `None.get(...)`,
# c'est-à-dire un 500 — une panne pour ce qui n'est qu'une saisie à refuser.
#
# Les déclarer ici plutôt que d'écrire un schéma par type d'étape : il y a
# quarante processeurs et une seule de ces listes, et un schéma par type
# serait une seconde description des formulaires, qui dériverait. Un test
# relit `processors.py` et échoue si un processeur parcourt une liste de
# `params` comme des objets sans la nommer ici.
OBJECT_LIST_PARAMS: dict[str, tuple[str, ...]] = {
    "rename": ("renames",),
}
