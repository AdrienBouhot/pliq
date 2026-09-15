"""Lecture de l'entrepôt — entièrement via dbt.

Pliq ne parle jamais à un entrepôt en direct : toute lecture passe par
`dbt show`, donc par l'adaptateur que le projet a choisi. Deux conséquences :

* l'atelier marche partout où dbt marche, sans qu'on ait à connaître le driver ;
* le SQL qu'on envoie est du vrai Jinja dbt — `ref()`, `source()`, macros,
  `var()`, `is_incremental()` — compilé par dbt avec le manifest du projet,
  plutôt que par une réimplémentation maison à coups d'expressions régulières.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import math
import re
from typing import Any

from .config import Settings
from .dbt_service import DbtService, RunInProgress, ShowError


class WarehouseError(RuntimeError):
    pass


# agate ne distingue que quelques familles de types. On les rend sous des noms
# SQL parlants, que `profiling.storage_of` sait déjà lire.
AGATE_TO_SQL = {
    "Text": "VARCHAR",
    "Integer": "INTEGER",
    "Number": "DECIMAL",
    "Boolean": "BOOLEAN",
    "Date": "DATE",
    "DateTime": "TIMESTAMP",
    "TimeDelta": "INTERVAL",
}


# Une requête a rarement plus de colonnes. Au-delà, la réponse de l'adaptateur
# — une ligne par colonne — serait tronquée : on la jette plutôt que de rendre
# une liste amputée, et c'est le repli qui répond. On en demande une de plus
# que le maximum, sans quoi une requête à exactement ce nombre de colonnes
# serait indiscernable d'une réponse coupée, et perdrait ses vrais types.
_MAX_COLUMNS = 2000


def _rank(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# Combien de requêtes gardent leurs colonnes en mémoire. Une recipe en compte
# une par entrée ; quelques dizaines couvrent l'édition d'un script et tout ce
# qu'on ouvre autour, sans que le cache devienne un endroit où quelque chose
# dort longtemps.
_COLUMNS_CACHE = 128


class Warehouse:
    def __init__(self, settings: Settings, svc: DbtService):
        self.settings = settings
        self.svc = svc
        # Les colonnes d'une requête, retenues le temps que le manifeste ne
        # bouge pas. Un aperçu relit les colonnes de chaque entrée à chaque
        # frappe, et chacune est un aller-retour `dbt show` complet : une
        # jointure à deux entrées en payait trois par aperçu. Rien de tout
        # cela ne change tant que le projet n'est pas reparsé.
        #
        # La clé porte la génération du manifeste : un enregistrement reparse,
        # donc la génération bouge, donc le cache ne peut pas servir des
        # colonnes d'avant. C'est plus sûr qu'une durée de vie — un `ref()` qui
        # change de cible n'attend pas la fin d'un délai.
        self._columns: dict[tuple[int, str], list[dict]] = {}

    # ------------------------------------------------------------- lecture

    def query(self, sql: str, limit: int = 100) -> dict[str, Any]:
        """Exécute une requête et retourne `{columns, rows}`.

        `sql` peut contenir du Jinja dbt : c'est dbt qui le compile.
        """
        try:
            table = self.svc.show(sql, limit=limit)
        except RunInProgress:
            # Ce n'est pas une erreur d'entrepôt : l'entrepôt n'a rien vu
            # passer. La déguiser en `WarehouseError` la rendait en 400 « la
            # requête est mauvaise », alors que la requête est bonne et que
            # c'est le moment qui ne l'est pas. Elle remonte telle quelle, et
            # le serveur la rend en 409.
            raise
        except ShowError as exc:
            raise WarehouseError(_readable(str(exc))) from None
        except Exception as exc:  # noqa: BLE001 — dbt lève large
            raise WarehouseError(_readable(str(exc))) from None
        return _payload(table)

    def preview_relation(self, relation: str, limit: int = 100) -> dict[str, Any]:
        if not relation:
            raise WarehouseError(
                "Ce dataset n'existe pas encore dans l'entrepôt. "
                "Lancez « dbt build » pour le construire."
            )
        return self.query(f"select * from {relation}", limit=limit)

    def columns_of_sql(self, sql: str) -> list[dict]:
        """Colonnes d'une requête, et les types que l'entrepôt leur donne.

        Ces types ne servent pas qu'à l'affichage : c'est sur eux que le
        compilateur décide d'écrire `code = 123` ou `code = '123'`. Les déduire
        des valeurs d'un échantillon était donc un pari, et il se perdait
        silencieusement — une ligne suffisait à le trancher, et une première
        ligne à NULL ne tranche rien du tout.

        On les demande donc à l'adaptateur, qui les tient du curseur : c'est le
        seul endroit où le type est celui de la colonne et pas celui de ce
        qu'elle contenait ce jour-là.

        Le résultat est gardé tant que le manifeste ne bouge pas : c'est la
        lecture que l'aperçu refait à chaque frappe, et elle ne peut pas
        changer entre deux parses.
        """
        key = (self.svc.generation, sql)
        cached_value = self._columns.get(key)
        if cached_value is not None:
            # Une copie : l'appelant range ces dictionnaires dans un état de
            # colonnes et le compilateur les lit partout. Rendre l'objet du
            # cache ferait qu'une modification chez lui vaudrait pour tous les
            # aperçus suivants.
            return [dict(c) for c in cached_value]

        try:
            columns = self._columns_from_adapter(sql)
        except WarehouseError:
            columns = None
        if not columns:
            columns = self._columns_from_sample(sql)

        # Les générations précédentes ne servent plus à rien : le manifeste a
        # changé, et personne ne les redemandera jamais.
        for stale_key in [k for k in self._columns if k[0] != self.svc.generation]:
            del self._columns[stale_key]
        if len(self._columns) >= _COLUMNS_CACHE:
            self._columns.pop(next(iter(self._columns)))
        self._columns[key] = [dict(c) for c in columns]
        return columns

    # Ce que dbt sait faire et que nous n'avons pas le droit de faire nous-mêmes :
    # ouvrir le curseur et lire sa description. `adapter` est dans le contexte
    # Jinja de tout ce que dbt compile, `dbt show --inline` compris, donc la
    # question part par le même chemin que toutes nos autres lectures.
    #
    # Le `{% set %}` en bloc porte la requête telle quelle : elle est rendue
    # comme le reste — `ref()`, `source()`, macros — au lieu d'avoir à être
    # échappée dans une chaîne Jinja, ce qu'aucun échappement ne ferait
    # proprement pour du Jinja qui doit rester du Jinja.
    #
    # `union all` ne promet aucun ordre : la position voyage avec la ligne.
    # La requête prend la place du jalon : `str.format` n'est pas utilisable
    # ici, il lirait chaque `{%` de Jinja comme un champ à remplacer.
    _MILESTONE = "-- pliq:requete --"

    _TYPES_TEMPLATE = """\
{%- set pliq_requete -%}
-- pliq:requete --
{%- endset -%}
{%- set pliq_colonnes = adapter.get_column_schema_from_query(pliq_requete) -%}
{%- if pliq_colonnes -%}
{%- for c in pliq_colonnes %}
select {{ loop.index }} as pliq_pos,
       '{{ c.name | replace("'", "''") }}' as pliq_nom,
       '{{ c.dtype | replace("'", "''") }}' as pliq_type
{%- if not loop.last %}
union all
{%- endif %}
{%- endfor %}
{%- else -%}
select 0 as pliq_pos, '' as pliq_nom, '' as pliq_type where 1 = 0
{%- endif -%}
"""

    def _columns_from_adapter(self, sql: str) -> list[dict] | None:
        """Les colonnes vues par le curseur, ou None si ce chemin n'aboutit pas.

        Un adaptateur qui ne sait pas répondre rend une liste vide plutôt qu'une
        erreur — `get_column_schema_from_query` est déclarée `available.parse`.
        On le traite comme un échec : c'est l'échantillon qui reprendra la main,
        en disant cette fois qu'il ne sait pas.
        """
        template = self._TYPES_TEMPLATE.replace(self._MILESTONE, sql.strip())
        try:
            data = self.query(template, limit=_MAX_COLUMNS + 1)
        except WarehouseError:
            return None
        idx = {c["name"].lower(): i for i, c in enumerate(data["columns"])}
        if not {"pliq_pos", "pliq_nom", "pliq_type"} <= set(idx):
            return None
        lines = data["rows"]
        if len(lines) > _MAX_COLUMNS:
            # La limite borne les lignes du résultat, et il y en a une par
            # colonne : au-delà, les dernières colonnes manqueraient. Rendre la
            # liste amputée serait pire que ne pas savoir les typer — l'atelier
            # ne verrait plus ces colonnes du tout. Le repli, lui, les voit
            # toutes : il lit la requête elle-même.
            return None
        lines = sorted(lines, key=lambda r: _rank(r[idx["pliq_pos"]]))
        return [
            {
                "name": str(r[idx["pliq_nom"]]),
                "type": str(r[idx["pliq_type"]] or "").upper(),
            }
            for r in lines
        ]

    def _columns_from_sample(self, sql: str) -> list[dict]:
        """Le repli : les colonnes d'un échantillon d'une ligne.

        agate ne voit que des valeurs, et une colonne dont la valeur lue est
        NULL ne lui en montre aucune : le type qu'il rend alors est le premier
        de sa liste, pas celui de la colonne. On rend donc le type inconnu —
        `typed_lit` met alors la valeur entre apostrophes, ce que tout entrepôt
        sait relire sur une colonne numérique, là où le pari inverse comparait
        un texte à un nombre.
        """
        data = self.query(sql, limit=1)
        sample = data["rows"][0] if data["rows"] else []
        return [
            ({**col, "type": ""} if i >= len(sample) or sample[i] is None else col)
            for i, col in enumerate(data["columns"])
        ]

    def columns_of(self, schema: str, name: str) -> list[dict]:
        """Colonnes d'une table de l'entrepôt (pour pré-remplir un YAML)."""
        return self.columns_of_sql(f"select * from {q(schema)}.{q(name)}")

    def count(self, relation: str) -> int | None:
        if not relation:
            return None
        try:
            data = self.query(f"select count(*) as n from {relation}", limit=1)
        except WarehouseError:
            return None
        try:
            return int(data["rows"][0][0])
        except (IndexError, TypeError, ValueError):
            return None

    # ----------------------------------------------------------- inventaire

    def tables(self) -> dict[str, Any]:
        """Ce que l'entrepôt contient réellement, schéma par schéma.

        Sert à déclarer une source : on ne demande pas à l'utilisateur de taper
        un nom de table, on lui montre celles qui existent. `information_schema`
        est du SQL standard, donc portable d'un entrepôt à l'autre.
        """
        # Une ligne de plus que la borne annoncée : si elle revient, c'est
        # que l'entrepôt en avait davantage. Sans cette sentinelle, la
        # troncature était muette — l'écran présentait 5 000 tables comme
        # « l'inventaire », et une table absente restait inexplicable.
        data = self.query(_INVENTORY_SQL, limit=INVENTORY_LIMIT + 1)
        truncated = len(data["rows"]) > INVENTORY_LIMIT
        if truncated:
            data["rows"] = data["rows"][:INVENTORY_LIMIT]
        idx = {c["name"].lower(): i for i, c in enumerate(data["columns"])}

        def cell(row, key):
            i = idx.get(key)
            return row[i] if i is not None else None

        database = ""
        out = []
        for row in data["rows"]:
            catalog = cell(row, "table_catalog")
            if catalog and not database:
                database = str(catalog)
            kind = str(cell(row, "table_type") or "")
            ncol = cell(row, "n_columns")
            out.append(
                {
                    "database": str(catalog or ""),
                    "schema": str(cell(row, "table_schema") or ""),
                    "name": str(cell(row, "table_name") or ""),
                    "type": "view" if "VIEW" in kind.upper() else "table",
                    # Le nombre de lignes n'a pas d'équivalent portable : chaque
                    # entrepôt le range ailleurs. On l'omet plutôt que de le deviner.
                    "rows": None,
                    "columns": int(ncol) if isinstance(ncol, (int, float)) else None,
                }
            )
        return {
            "database": database,
            "tables": out,
            "truncated": truncated,
            "limit": INVENTORY_LIMIT,
        }


# `information_schema` est normalisé (SQL:2003) et présent sur DuckDB, Postgres,
# Snowflake et Redshift. Le compte de colonnes vient de la même source, ce qui
# évite un second aller-retour.
#
# La jointure porte sur le catalogue autant que sur le schéma et le nom : deux
# bases attachées peuvent avoir chacune leur `main.orders`, et joindre sans le
# catalogue additionnait leurs colonnes — trois pour chacune, là où l'une en a
# une et l'autre deux. Un nom de table n'identifie une table qu'à trois niveaux.
# Borne de l'inventaire. Elle existe pour qu'un entrepôt à cent mille tables
# ne fasse pas tomber l'écran ; la réponse dit désormais quand elle a mordu.
INVENTORY_LIMIT = 5000

_INVENTORY_SQL = """
select
    t.table_catalog,
    t.table_schema,
    t.table_name,
    t.table_type,
    count(c.column_name) as n_columns
from information_schema.tables t
left join information_schema.columns c
    on c.table_catalog = t.table_catalog
   and c.table_schema = t.table_schema
   and c.table_name = t.table_name
where lower(t.table_schema) not in ('information_schema', 'pg_catalog')
group by 1, 2, 3, 4
order by 2, 3
"""


# ------------------------------------------------------------------ helpers


def _payload(table) -> dict[str, Any]:
    """Table agate → `{columns, rows}` sérialisable en JSON."""
    columns = [
        {
            "name": str(name),
            "type": AGATE_TO_SQL.get(type(col).__name__, type(col).__name__.upper()),
        }
        for name, col in zip(table.column_names, table.column_types, strict=True)
    ]
    rows = [[_cell(v) for v in row] for row in table.rows]
    return {"columns": columns, "rows": rows}


# Au-delà, ni un float ni un `Number` JavaScript ne représentent un entier
# exactement : `JSON.parse` rendrait 9007199254740993 comme 9007199254740992,
# et deux identifiants distincts s'afficheraient à l'identique. On rend alors la
# valeur en texte, ce que l'affichage et le profilage (qui travaillent sur des
# chaînes) lisent sans rien perdre. Vaut pour les `Decimal` comme pour les
# entiers natifs : c'est le JSON qui est en cause, pas le type Python.
_EXACT_INT = 2**53


def _cell(value: Any) -> Any:
    """Rend une valeur d'entrepôt sérialisable en JSON."""
    # `bool` d'abord, et pas avec les entiers : c'en est un en Python, et le
    # faire passer par la règle ci-dessous en ferait un jour du texte.
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value if abs(value) < _EXACT_INT else str(value)
    if isinstance(value, float):
        # `nan`, `inf` et `-inf` sont des flottants Python parfaitement
        # ordinaires — une division par zéro ou un `log(0)` en produit sur
        # plusieurs entrepôts — mais pas du JSON : `json.dumps` les écrit
        # `NaN`/`Infinity`, que `JSON.parse` refuse. La réponse entière
        # devenait illisible, et l'écran affichait « l'atelier ne répond
        # plus » au lieu d'une case. Même règle que pour les `Decimal`.
        return value if math.isfinite(value) else str(value)
    if isinstance(value, decimal.Decimal):
        return _decimal_cell(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return str(value)


def _decimal_cell(value: decimal.Decimal) -> Any:
    """Un Decimal rendu en JSON sans altérer ce qu'on affichera.

    Passer systématiquement par float abîme les valeurs que le binaire ne sait
    pas coder — un identifiant NUMERIC(38,0) de Snowflake, par exemple.
    """
    if not value.is_finite():
        return str(value)
    if value == value.to_integral_value():
        whole = int(value)
        return whole if abs(whole) < _EXACT_INT else str(whole)
    f = float(value)
    # Si le float relit exactement la même valeur décimale, il est fidèle ;
    # sinon seul le texte l'est.
    return f if decimal.Decimal(repr(f)) == value else str(value)


# Ce qu'une erreur dbt traîne derrière elle et qui n'explique rien : la pile
# Python. Le test portait sur la ligne déjà `strip()`ée, ce qui rendait celui
# de l'indentation — le seul qui reconnaisse une ligne de code recopiée —
# inopérant, et laissait passer n'importe quel fragment de pile.
_STACK = ('File "', "Traceback", "During handling", "The above exception")

# Une flèche de position, un rappel de la requête : ce sont les compléments du
# diagnostic, jamais le diagnostic. S'arrêter sur l'un d'eux rendait « ^ » tout
# seul, et l'utilisateur n'apprenait rien de son erreur de syntaxe.
_CARET = set("^~- ")
_CONTEXT_RE = re.compile(r"^\s*(LINE\s+\d+\s*:|line\s+\d+\s*:|\.\.\.)")

# Le diagnostic et ce qui l'éclaire, pas la requête entière recopiée.
_MAX_CONTEXT = 4
# Un diagnostic tient sur une ou deux lignes ; au-delà, c'est l'emboîtage.
_MAX_DIAGNOSTIC = 3


def _is_context(line: str) -> bool:
    bare = line.strip()
    return bool(bare) and (
        set(bare) <= _CARET or bool(_CONTEXT_RE.match(line)) or bare.endswith("^")
    )


def _readable(message: str) -> str:
    """Garde la partie utile d'une erreur dbt : le diagnostic, et son contexte.

    La dernière ligne parlante n'est pas toujours celle qui parle. Un entrepôt
    qui refuse du SQL écrit d'abord le problème, puis recopie la requête et
    pointe l'endroit d'une flèche ; garder la dernière rendait « ^ » tout seul,
    et l'utilisateur n'apprenait rien de son erreur de syntaxe.

    On repart donc de la dernière ligne qui n'est pas du contexte, on lui rend
    les lignes qui la précèdent au même niveau d'indentation — un diagnostic
    tient souvent sur deux lignes, « Catalog Error… » puis « Did you mean… » —
    et on rend le contexte avec, puisque c'est lui qui situe l'erreur. Le
    niveau d'indentation est ce qui sépare le diagnostic de l'emboîtage que dbt
    met autour, et qui ne nomme que sa propre plomberie.
    """
    lines = [ln.rstrip() for ln in str(message).splitlines() if ln.strip()]
    if not lines:
        return "dbt n'a pas pu lire l'entrepôt."
    useful = [ln for ln in lines if not ln.lstrip().startswith(_STACK)] or lines

    end = len(useful)
    while end > 1 and _is_context(useful[end - 1]):
        end -= 1
    start = end - 1
    indent = _indent_width(useful[start])
    while (
        start > 0
        and end - start < _MAX_DIAGNOSTIC
        and _indent_width(useful[start - 1]) == indent
    ):
        start -= 1

    # Le contexte est désindenté de la marge du diagnostic, et pas ligne à
    # ligne : la flèche ne pointe juste que si elle garde sa position relative
    # au SQL qu'elle vise.
    block = [
        ln[indent:] if _indent_width(ln) >= indent else ln.lstrip()
        for ln in useful[start:end]
    ]
    context = [_unindent(ln, indent) for ln in useful[end : end + _MAX_CONTEXT]]
    return "\n".join([*block, *context]).rstrip()


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip())


def _unindent(line: str, indent: int) -> str:
    return line[indent:] if not line[:indent].strip() else line.lstrip()


def q(ident: str) -> str:
    """Cite un identifiant. Les doubles quotes sont le standard SQL."""
    return '"' + str(ident).replace('"', '""') + '"'
