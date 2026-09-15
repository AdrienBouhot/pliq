"""Les `ref()` et `source()` : les écrire, les relire, les faire suivre.

Un renommage doit retrouver ce qui nommait l'ancien modèle, dans le SQL écrit
à la main comme dans les scripts visuels d'aval.
"""

from __future__ import annotations

import re

from .names import dbt_name, jinja_str, validate_name


def ref_version(raw_text) -> str:
    """La version d'un `ref()`, écrite comme dbt l'accepte, ou « » s'il n'y en a pas.

    dbt prend `v=2` comme `v='2'` : un numéro reste un numéro, et tout le reste
    part entre apostrophes — une version peut s'appeler `beta`. Ce qui n'entre
    dans aucun des deux moules n'a rien à faire dans un `ref()`.
    """
    if raw_text in (None, ""):
        return ""
    text = str(raw_text).strip()
    if re.fullmatch(r"\d+", text):
        return text
    return jinja_str(validate_name(text, "version"))


def ref_sql(inp: dict) -> str:
    """Le Jinja qui désigne une entrée : `ref()` ou `source()`.

    Un `ref('orders')` nu ne dit pas *quel* `orders` : dbt résout d'abord dans
    le projet ouvert, et un modèle de paquet qui porte le même nom est perdu en
    route — l'atelier montrait celui du paquet et compilait celui du projet.
    Même chose pour les versions : `ref('customers')` rend la dernière, quelle
    que soit celle qui a été choisie. Le paquet et la version font partie de
    l'identité d'un dataset : quand l'entrée les porte, le `ref()` les écrit.
    """
    if inp.get("source_name"):
        return (
            f"{{{{ source({jinja_str(dbt_name(inp['source_name'], 'source'))}, "
            f"{jinja_str(dbt_name(inp.get('table', ''), 'table'))}) }}}}"
        )
    args = [jinja_str(validate_name(inp.get("ref", ""), "référence"))]
    package = str(inp.get("package") or "").strip()
    if package:
        args.insert(0, jinja_str(validate_name(package, "paquet")))
    version = ref_version(inp.get("version"))
    if version:
        args.append(f"v={version}")
    return "{{ ref(" + ", ".join(args) + ") }}"


# `ref('modele')`, sous les écritures que dbt accepte : les deux guillemets, le
# paquet en premier argument, la version en dernier. Ce qui n'entre pas dans ce
# moule — un `ref()` construit par une variable — n'est pas réécrit : mieux vaut
# le laisser intact que de deviner.
def ref_re(name: str) -> re.Pattern:
    return re.compile(
        # Ancré à gauche : sans ce contrôle, `my_ref("old")` — un nom de macro
        # parfaitement légitime — et `autre.ref("old")` étaient réécrits comme
        # s'ils désignaient notre modèle. Seul `dbt.` est admis comme
        # qualificateur : c'est ainsi qu'un modèle Python lit ses dépendances.
        r"(?<![A-Za-z0-9_.])(?P<prefixe>dbt\s*\.\s*)?"
        r"ref\(\s*(?:(?P<q1>['\"])(?P<pkg>[^'\"]+)(?P=q1)\s*,\s*)?"
        rf"(?P<q2>['\"]){re.escape(name)}(?P=q2)"
        # dbt accepte la version en nombre comme en chaîne : `v=2` et
        # `version='2'` désignent le même modèle, et le second ne doit pas
        # échapper au renommage sous prétexte qu'il porte des guillemets.
        r"(?P<version>\s*,\s*v(?:ersion)?\s*=\s*(?:[0-9]+|['\"][^'\"]*['\"]))?\s*\)"
    )


# Les zones d'un fichier dbt où du Jinja s'exécute : `{{ … }}` et `{% … %}`.
# `{# … #}` est un commentaire Jinja, et ce qu'il contient ne s'exécute pas.
_JINJA_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)


def _jinja_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _JINJA_RE.finditer(text)]


def rename_ref_in_sql(
    sql: str, old: str, new: str, package: str = "", language: str = "jinja"
) -> str:
    """Fait suivre à du code dbt le renommage d'un modèle qu'il lit.

    Un `ref()` qui nomme un paquet ne désigne le modèle d'ici que si ce paquet
    est le projet lui-même : `ref('dbt_utils', 'x')` et notre `x` sont deux
    modèles différents, et renommer le second ne doit pas toucher au premier.

    `language` dit où un `ref()` s'exécute, et c'est ce qui distingue un lien
    dbt d'un texte qui lui ressemble :

    * `jinja` — un modèle SQL : seuls les `ref()` posés *dans* un `{{ … }}` ou
      un `{% … %}` sont réécrits. Une chaîne SQL qui contient `ref("old")`, un
      commentaire, un libellé métier ne bougent plus ;
    * `yaml` — un fichier de propriétés : dbt y rend certains champs comme des
      expressions Jinja *sans délimiteurs* — le `to:` d'un test de relation
      s'écrit `ref('autre')` tout court. On y accepte donc le `ref()` nu ;
    * `python` — un modèle Python : `dbt.ref("old")`, qui n'est pas du Jinja.
      Le qualificateur `dbt.` est alors exigé.

    Dans les trois cas, l'ancrage à gauche du motif écarte `my_ref("old")` et
    `autre.ref("old")`, qui ne désignent pas notre modèle.
    """
    if not sql or old == new:
        return sql
    zones = _jinja_spans(sql) if language == "jinja" else []

    def inside_jinja(m: re.Match) -> bool:
        return any(start <= m.start() and m.end() <= end for start, end in zones)

    def replace(m: re.Match) -> str:
        prefix = m.group("prefixe") or ""
        if language == "jinja":
            # Un `dbt.ref()` dans du SQL n'est pas un lien dbt, et un `ref()`
            # hors Jinja n'en est pas un non plus : c'est du texte.
            if prefix or not inside_jinja(m):
                return m.group(0)
        elif language == "yaml":
            if prefix:
                return m.group(0)
        elif not prefix:
            return m.group(0)
        pkg = m.group("pkg")
        if pkg is not None and pkg != package:
            return m.group(0)
        q1, q2 = m.group("q1"), m.group("q2")
        head = f"{q1}{pkg}{q1}, " if pkg is not None else ""
        return f"{prefix}ref({head}{q2}{new}{q2}{m.group('version') or ''})"

    return ref_re(old).sub(replace, sql)


def rename_ref_in_spec(spec: dict, old: str, new: str, package: str = "") -> bool:
    """Fait suivre à un script visuel le renommage d'un modèle qu'il lit.

    Vrai si le script a changé. Les entrées sont structurées, donc réécrites
    comme telles ; seule une recipe SQL porte du Jinja, et c'est le seul endroit
    où l'on se rabat sur le texte.
    """
    if old == new:
        return False
    change = False

    for inp in spec.get("inputs") or []:
        if not isinstance(inp, dict) or inp.get("ref") != old:
            continue
        # Le modèle renommé est celui du projet ouvert. Une entrée qui nomme
        # son paquet désigne un autre dataset, homonyme : la réécrire ferait
        # lire au script d'aval un modèle qui n'a pas bougé de nom.
        own = str(inp.get("package") or "").strip()
        if own and package and own != package:
            continue
        inp["ref"] = new
        # L'alias, lui, ne bouge pas : c'est le nom du CTE, et il est déjà écrit
        # dans le `.sql` du modèle qu'on ne recompile pas ici. Le changer ferait
        # dire au script autre chose que ce que le SQL fait — et c'est le SQL
        # qui est la vérité.
        change = True

    sql = spec.get("sql")
    if isinstance(sql, str):
        fresh = rename_ref_in_sql(sql, old, new, package)
        if fresh != sql:
            spec["sql"] = fresh
            change = True

    return change


# ------------------------------------ repérer qui nomme une colonne renommée
#
# Renommer une colonne dans un modèle casse tout ce qui la nomme en aval. On
# sait le dire, et l'écran le dit avant d'écrire : c'est tout.
#
# L'atelier a su un temps le *suivre*, en réécrivant les modèles d'aval. Il ne
# le fait plus. La réécriture était textuelle, donc aveugle à la portée SQL :
# les deux branches d'un `union all` ont chacune la leur, et un `amount` non
# qualifié n'y désigne pas la même colonne. Renommer `a.amount` réécrivait les
# deux, et le modèle ne se construisait plus. Le faire juste demanderait de
# résoudre chaque référence par sa portée, donc de connaître les colonnes de
# toutes les relations d'amont — or l'entrepôt porte encore les anciennes tant
# que `dbt build` n'a pas tourné. Un renommage silencieusement faux est pire
# que pas de renommage : on avertit, et on laisse l'utilisateur ouvrir ses
# modèles.

# Les champs d'un script visuel qui ne désignent jamais une colonne : une valeur
# de filtre, un nom de modèle, un libellé. Les fouiller ferait passer « Paris »
# pour une colonne dès qu'une colonne s'appelle Paris.
NON_COLUMN_KEYS = frozenset(
    {
        "name",
        "ref",
        "alias",
        "table",
        "source_name",
        "package",
        "version",
        "type",
        "id",
        "label",
        "description",
        "values",
        "value",
        "find",
        "replace",
        "separator",
        "format",
        "prefix",
        "tags",
        "layer",
        "materialized",
        "mode",
        "action",
        "fn",
        "aggregate",
        "ties",
        "begin",
        "batch_size",
        # Les composants d'une date — `year`, `month`… — sont des mots-clés du
        # pas de temps, pas des colonnes, même quand une colonne porte ce nom.
        "parts",
    }
)


def column_texts(obj, outside=NON_COLUMN_KEYS) -> list[str]:
    """Les chaînes d'un script visuel qui peuvent nommer une colonne.

    On exclut plutôt qu'on n'inclut : une expression SQL libre — formule,
    condition, filtre de mesure — nomme des colonnes elle aussi, et c'est
    précisément là qu'un renommage casse sans prévenir.
    """
    if isinstance(obj, dict):
        return [
            s
            for k, v in obj.items()
            if str(k) not in outside
            for s in column_texts(v, outside)
        ]
    if isinstance(obj, list):
        return [s for v in obj for s in column_texts(v, outside)]
    return [obj] if isinstance(obj, str) else []


def sql_mentions_column(sql: str, column: str) -> bool:
    """Vrai si ce texte nomme cette colonne, où que ce soit.

    Volontairement plus large que ce qu'un renommage sait suivre : une colonne
    nommée dans une chaîne — l'`except` d'un `dbt_utils.star`, une clé de macro
    — casse le `dbt build` tout autant. Prévenir sans savoir réparer vaut mieux
    que de laisser découvrir la casse à la construction suivante.
    """
    if not sql or not column:
        return False
    return re.search(rf"\b{re.escape(column)}\b", sql) is not None


def mentions_column(spec: dict, column: str) -> bool:
    """Vrai si ce script visuel nomme cette colonne quelque part."""
    return any(sql_mentions_column(t, column) for t in column_texts(spec))
