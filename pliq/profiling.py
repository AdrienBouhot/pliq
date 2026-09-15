"""Types de stockage, « significations » et barres de qualité.

Reprend la distinction d'un ETL visuel : le *type de stockage* est celui de
l'entrepôt (varchar, bigint…), la *signification* est ce que la colonne veut
dire (Email, Date, Code pays…) et se devine à partir des valeurs.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import re
from typing import Any

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
URL_RE = re.compile(r"^https?://\S+$", re.I)
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
INT_RE = re.compile(r"^-?\d+$")
DEC_RE = re.compile(r"^-?\d*[.,]?\d+([eE][-+]?\d+)?$")
BOOL_VALUES = {"true", "false", "0", "1", "yes", "no", "oui", "non", "t", "f"}

# --------------------------------------------------------- dates et adresses
#
# Une expression régulière dit la *forme*, jamais la validité : `2026-99-99` a
# la forme d'une date et n'en est pas une, `999.999.999.999` a la forme d'une
# IPv4 et n'en est pas une. À l'inverse, une forme écrite à la main rejetait ce
# que les entrepôts sérialisent vraiment — fraction de seconde et décalage
# horaire. Les barres de qualité affichaient donc les deux erreurs à la fois.
#
# On garde donc une forme, mais seulement pour cadrer ce qu'on accepte de
# tenter, et c'est un vrai analyseur qui tranche.

# La forme compacte (« 20260913 ») est volontairement hors du cadre : elle ne
# se distingue pas d'un entier à huit chiffres, et `date.fromisoformat` ne
# l'accepte qu'à partir de Python 3.11 — la même colonne aurait changé de
# signification selon la version de Python qui fait tourner l'atelier.
_ISO_SHAPE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:[Zz]|[+-]\d{2}:?\d{2})?)?$"
)
_FRACTION = re.compile(r"\.(\d+)")
_OFFSET_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")


def _iso_parsable(text: str) -> bool:
    """Vrai si ce texte est une date ou un horodatage ISO 8601 réel.

    `fromisoformat` de Python 3.10 est plus strict que la sérialisation des
    entrepôts : il refuse le « Z » final et n'accepte la fraction de seconde
    qu'à trois ou six chiffres. On normalise donc ces deux points avant de lui
    passer la main, plutôt que de réécrire un analyseur de dates.
    """
    s = text
    if s[-1:] in ("Z", "z"):
        s = s[:-1] + "+00:00"
    m = _FRACTION.search(s)
    if m:
        s = s[: m.start()] + "." + (m.group(1) + "000000")[:6] + s[m.end() :]
    s = _OFFSET_NO_COLON.sub(r"\1:\2", s)
    for parse in (dt.datetime.fromisoformat, dt.date.fromisoformat):
        try:
            parse(s)
            return True
        except ValueError:
            continue
    return False


def is_temporal(text: str) -> bool:
    text = (text or "").strip()
    return bool(_ISO_SHAPE.match(text)) and _iso_parsable(text)


def is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address((text or "").strip())
    except ValueError:
        return False
    return True


MEANINGS = {
    "text": "Texte",
    "integer": "Entier",
    "decimal": "Décimal",
    "date": "Date",
    "datetime": "Date & heure",
    "boolean": "Booléen",
    "email": "Email",
    "url": "URL",
    "ip": "Adresse IP",
    "country": "Code pays",
}


def _ref(name: str) -> str:
    """Le nom d'une colonne, cité pour l'entrepôt du projet.

    Deux suggestions recollaient le nom tel quel dans du SQL : sur une colonne
    nommée `order`, `date` ou « chiffre d'affaires », l'étape proposée en un
    clic était du SQL invalide, et le refus arrivait deux écrans plus loin,
    dans le vocabulaire de l'entrepôt. Le moteur cite déjà les identifiants
    partout ailleurs — c'est la même fonction.

    L'import est local : `recipes` est un paquet lourd, et `profiling` est lu
    par des routes qui n'en ont pas besoin.
    """
    from .recipes import q

    return q(name)


def _is_empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


# La reconnaissance d'un type se fait sur son *nom*, pas sur un morceau de son
# nom : `INTERVAL` contient « INT », `STRUCT(a VARCHAR)` contient « VARCHAR »,
# `INTEGER[]` contient « INT ». Une colonne `INTERVAL` était donc lue comme un
# entier, et la barre de qualité l'affichait 100 % invalide sur des données
# parfaitement saines — exactement le genre d'alerte qui apprend à ignorer les
# alertes. `writers` porte déjà cette reconnaissance, et les deux modules
# doivent trancher pareil.
_STORAGE_BY_NAME = {
    "boolean": ("BOOL", "BOOLEAN", "BIT"),
    "date": (
        "DATE",
        "DATETIME",
        "DATETIME2",
        "SMALLDATETIME",
        "TIMESTAMP",
        "TIMESTAMPTZ",
        "TIMESTAMP_NTZ",
        "TIMESTAMP_LTZ",
        "TIMESTAMP_TZ",
        "TIMESTAMP WITH TIME ZONE",
        "TIMESTAMP WITHOUT TIME ZONE",
        "DATETIMEOFFSET",
    ),
    "decimal": ("DECIMAL", "NUMERIC", "NUMBER", "DEC", "MONEY", "SMALLMONEY"),
    "double": (
        "DOUBLE",
        "DOUBLE PRECISION",
        "FLOAT",
        "FLOAT4",
        "FLOAT8",
        "FLOAT64",
        "REAL",
    ),
    "bigint": ("BIGINT", "HUGEINT", "INT8", "INT64", "UBIGINT", "UINT64"),
    "int": (
        "INT",
        "INTEGER",
        "INT2",
        "INT4",
        "INT16",
        "INT32",
        "SMALLINT",
        "TINYINT",
        "UINT8",
        "UINT16",
        "UINT32",
        "UTINYINT",
        "USMALLINT",
        "UINTEGER",
    ),
    "string": (
        "CHAR",
        "VARCHAR",
        "NVARCHAR",
        "BPCHAR",
        "NCHAR",
        "CHARACTER",
        "CHARACTER VARYING",
        "TEXT",
        "NTEXT",
        "STRING",
        "CLOB",
        "LONGTEXT",
        "MEDIUMTEXT",
        "TINYTEXT",
        "VARCHAR2",
        "NVARCHAR2",
    ),
}


def storage_of(sql_type: str | None) -> str:
    from .recipes.writers import COMPOSITE, _type_label, type_family

    raw_text = (sql_type or "").strip()
    if not raw_text:
        return "string"
    if type_family(raw_text) == COMPOSITE:
        return COMPOSITE
    name = _type_label(raw_text)
    for storage, names in _STORAGE_BY_NAME.items():
        if name in names:
            return storage
    return name.lower() or "string"


def _meaning_from_type(sql_type: str | None) -> str | None:
    """La signification qu'un type impose, quand il en impose une.

    Un type composite n'en impose aucune : demander à une barre de qualité si
    un `STRUCT` « ressemble à un entier » n'a pas de sens, et y répondre
    « non » revenait à peindre en rouge une colonne juste.
    """
    if not sql_type:
        return None
    storage = storage_of(sql_type)
    if storage == "date":
        # `DATE` et `TIMESTAMP` partagent un stockage, pas une signification :
        # l'une porte une heure, l'autre non, et les barres de qualité ne
        # valident pas la même forme.
        return "datetime" if _is_datetime(sql_type) else "date"
    return {
        "boolean": "boolean",
        "decimal": "decimal",
        "double": "decimal",
        "bigint": "integer",
        "int": "integer",
    }.get(storage)


def _is_datetime(sql_type: str | None) -> bool:
    from .recipes.writers import _type_label

    return _type_label(sql_type or "").startswith(("TIMESTAMP", "DATETIME"))


def _guess_text_meaning(values: list[str]) -> str:
    """Signification d'une colonne texte, si une majorité franche se dégage."""
    if not values:
        return "text"
    tests = [
        ("email", lambda v: bool(EMAIL_RE.match(v))),
        ("url", lambda v: bool(URL_RE.match(v))),
        ("ip", is_ip),
        ("date", is_temporal),
        ("country", lambda v: bool(COUNTRY_RE.match(v))),
    ]
    # La reconnaissance et la validation se font sur le même critère : sans ça,
    # une colonne devinée « Adresse IP » par une forme approximative se
    # retrouvait ensuite invalide à cent pour cent.
    for name, recognizes in tests:
        hits = sum(1 for v in values if recognizes(v))
        if hits / len(values) >= 0.9:
            return name
    if all(INT_RE.match(v) for v in values):
        return "integer"
    if all(DEC_RE.match(v) for v in values):
        return "decimal"
    if all(v.lower() in BOOL_VALUES for v in values):
        return "boolean"
    return "text"


def _is_valid(value: Any, meaning: str) -> bool:
    # Une date rendue comme objet par le pilote est valide par construction :
    # la passer par `str()` la ferait juger sur le format d'affichage.
    if isinstance(value, (dt.datetime, dt.date)):
        return meaning in ("date", "datetime", "text")
    text = str(value).strip()
    if meaning == "text":
        return True
    if meaning == "integer":
        return bool(INT_RE.match(text))
    if meaning == "decimal":
        return bool(DEC_RE.match(text))
    if meaning in ("date", "datetime"):
        return is_temporal(text)
    if meaning == "boolean":
        return text.lower() in BOOL_VALUES
    if meaning == "email":
        return bool(EMAIL_RE.match(text))
    if meaning == "url":
        return bool(URL_RE.match(text))
    if meaning == "ip":
        return is_ip(text)
    if meaning == "country":
        return bool(COUNTRY_RE.match(text))
    return True


def profile(columns: list[dict], rows: list[list[Any]]) -> list[dict]:
    """Enrichit chaque colonne d'une signification et d'une barre de qualité."""
    out = []
    total = len(rows) or 1

    for i, col in enumerate(columns):
        raw = [r[i] for r in rows] if rows else []
        empties = sum(1 for v in raw if _is_empty(v))
        filled = [v for v in raw if not _is_empty(v)]

        meaning = _meaning_from_type(col.get("type"))
        if meaning is None:
            meaning = _guess_text_meaning([str(v).strip() for v in filled])

        invalid = sum(1 for v in filled if not _is_valid(v, meaning))
        valid = len(filled) - invalid

        out.append(
            {
                "name": col["name"],
                "type": col.get("type"),
                "storage": storage_of(col.get("type")),
                "meaning": meaning,
                "meaning_label": MEANINGS.get(meaning, "Texte"),
                "ok": round(100 * valid / total, 1),
                "empty": round(100 * empties / total, 1),
                "nok": round(100 * invalid / total, 1),
                "n_ok": valid,
                "n_empty": empties,
                "n_nok": invalid,
            }
        )
    return out


def suggestions(col: dict) -> list[dict]:
    """Étapes proposées dans le menu d'une colonne, selon sa signification."""
    meaning = col.get("meaning", "text")
    storage = col.get("storage", "string")
    out: list[dict] = []

    def add(label: str, processor: str, params: dict, category: str):
        out.append(
            {
                "label": label,
                "processor": processor,
                "params": params,
                "category": category,
            }
        )

    name = col["name"]

    add(
        "Filtrer sur une valeur…",
        "filter_value",
        {"column": name, "operator": "eq", "values": [], "action": "remove"},
        "Filtrer",
    )
    # « Une ligne par client » se décide en regardant la colonne du client :
    # c'est ici que le geste commence, pas dans la bibliothèque.
    add(
        "Garder une ligne par cette colonne…",
        "dedup_key",
        {"keys": [name], "order_by": [], "ties": "one"},
        "Lignes",
    )

    if col.get("n_empty"):
        add(
            "Supprimer les lignes sans valeur",
            "remove_empty",
            {"column": name},
            "Nettoyage",
        )
        add(
            "Remplir les cellules vides…",
            "fill_empty",
            {"column": name, "value": ""},
            "Nettoyage",
        )

    if meaning in ("text", "email", "url", "country"):
        add(
            "Mettre en minuscules",
            "text_transform",
            {"column": name, "mode": "lower"},
            "Texte",
        )
        add(
            "Mettre en majuscules",
            "text_transform",
            {"column": name, "mode": "upper"},
            "Texte",
        )
        add(
            "Supprimer les espaces autour",
            "text_transform",
            {"column": name, "mode": "trim"},
            "Texte",
        )
        add(
            "Rechercher & remplacer…",
            "find_replace",
            {"column": name, "find": "", "replace": ""},
            "Texte",
        )
        add(
            "Découper la colonne…",
            "split_column",
            {"column": name, "separator": ",", "count": 2},
            "Texte",
        )

    if meaning in ("integer", "decimal"):
        add("Arrondir…", "round", {"column": name, "decimals": 0}, "Nombres")
        add(
            "Filtrer sur un intervalle…",
            "filter_formula",
            {"condition": f"{_ref(name)} > 0", "action": "keep"},
            "Nombres",
        )

    if meaning in ("date", "datetime"):
        add(
            "Extraire les composants de date",
            "extract_date_parts",
            {"column": name, "parts": ["year", "month"]},
            "Date",
        )
    elif storage == "string":
        add("Parser en date…", "parse_date", {"column": name, "format": ""}, "Date")

    if meaning == "boolean":
        add(
            "Inverser le booléen",
            "formula",
            {"into": name, "expression": f"not {_ref(name)}"},
            "Booléen",
        )

    add(
        "Changer le type…",
        "change_type",
        {"column": name, "to": "varchar"},
        "Nettoyage",
    )
    add("Renommer…", "rename", {"renames": [{"from": name, "to": name}]}, "Colonnes")
    add(
        "Supprimer cette colonne",
        "keep_delete",
        {"columns": [name], "action": "delete"},
        "Colonnes",
    )

    return out
