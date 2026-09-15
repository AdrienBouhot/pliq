"""La phrase qu'on lit sur la carte d'une étape.

Elle est écrite ici et nulle part ailleurs : l'interface l'affiche telle
quelle, et le `.sql` la reprend en commentaire au-dessus du CTE.
"""

from __future__ import annotations

import re

from .processors import PROCESSORS, WINDOW_FUNCTIONS
from .vocabulary import FILTER_OPERATORS

# --------------------------------------------------------------- description


# Un libellé finit en commentaire SQL, derrière un seul `--`. Une expression
# multiligne — `id +\n1`, parfaitement valide — posait donc sa seconde ligne
# *hors* du commentaire, juste avant le CTE, et le fichier entier devenait
# invalide. On replie le libellé sur une ligne plutôt que de toucher à
# l'expression : c'est le commentaire qui doit s'adapter, jamais le SQL.
_WHITESPACE_RE = re.compile(r"\s+")


def _one_line(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", str(text)).strip()


def describe_step(step: dict, columns: list[str] | None = None) -> str:
    """Phrase courte affichée sur la carte d'étape, et en commentaire du CTE.

    Toujours sur une seule ligne : c'est la même phrase des deux côtés, et le
    `.sql` ne supporte pas qu'elle en prenne deux.
    """
    return _one_line(_describe_step(step, columns))


def _describe_step(step: dict, columns: list[str] | None = None) -> str:
    t = step.get("type")
    p = step.get("params") or {}
    proc = PROCESSORS.get(t)
    if t == "rename":
        n = len(p.get("renames") or [])
        if n == 1:
            r = p["renames"][0]
            return f"Renommer {r.get('from')} en {r.get('to')}"
        return f"Renommer {n} colonnes"
    if t == "keep_delete":
        cols = p.get("columns") or []
        verb = "Conserver" if p.get("action") == "keep" else "Supprimer"
        return f"{verb} {len(cols)} colonne{'s' if len(cols) > 1 else ''}"

    if t == "formula":
        return f"Calculer {p.get('into')} = {p.get('expression')}"
    if t == "if_then_else":
        return f"Calculer {p.get('into')} par condition"
    if t == "filter_value":
        action = {"remove": "Retirer", "keep": "Garder", "clear": "Vider"}.get(
            p.get("action", "remove"), "Filtrer"
        )
        vals = ", ".join(str(v) for v in (p.get("values") or []))
        op = _op_text(p.get("operator"))
        return f"{action} les lignes où {p.get('column')} {op} {vals}".strip()
    if t == "filter_formula":
        verb = "Garder" if p.get("action", "keep") == "keep" else "Retirer"
        return f"{verb} les lignes où {p.get('condition')}"
    if t == "remove_empty":
        return f"Supprimer les lignes où {p.get('column')} est vide"
    if t == "fill_empty":
        return f"Remplir {p.get('column')} vide par « {p.get('value')} »"
    if t == "find_replace":
        return (
            f"Remplacer « {p.get('find')} » par « {p.get('replace')} » "
            f"dans {p.get('column')}"
        )
    if t == "text_transform":
        mode = {
            "upper": "majuscules",
            "lower": "minuscules",
            "trim": "sans espaces",
            "capitalize": "capitalisé (1re lettre)",
            "trim_lower": "minuscules sans espaces",
        }
        return f"Mettre {p.get('column')} en {mode.get(p.get('mode'), p.get('mode'))}"
    if t == "change_type":
        return f"Convertir {p.get('column')} en {p.get('to')}"
    if t == "round":
        return f"Arrondir {p.get('column')} à {p.get('decimals', 0)} décimale(s)"
    if t == "parse_date":
        return f"Parser {p.get('column')} en date"
    if t == "extract_date_parts":
        return f"Extraire {', '.join(p.get('parts') or [])} de {p.get('column')}"
    if t == "concat_columns":
        return f"Concaténer {len(p.get('columns') or [])} colonnes dans {p.get('into')}"
    if t == "split_column":
        return f"Découper {p.get('column')} sur « {p.get('separator')} »"
    if t == "distinct_rows":
        return "Dédoublonner les lignes"
    if t == "sort":
        # Le sens du tri est tout ce que l'étape fait : l'omettre du
        # commentaire du `.sql` en retirait le seul renseignement utile.
        direction = " (décroissant)" if p.get("descending") else ""
        return f"Trier par {p.get('column')}{direction}"
    if t == "dedup_key":
        keys = ", ".join(p.get("keys") or []) or "?"
        sort_spec = (p.get("order_by") or [{}])[0] or {}
        direction = "plus grand" if sort_spec.get("descending") else "plus petit"
        return f"Une ligne par {keys} — {direction} {sort_spec.get('column') or '?'}"
    if t == "window_function":
        # Sans la parenthèse qui explique les ex æquo : elle sert à choisir
        # dans la liste, pas à relire le script.
        what = re.sub(
            r"\s*\(.*\)\s*$", "", str(WINDOW_FUNCTIONS.get(p.get("fn"), p.get("fn")))
        )
        by = ", ".join(p.get("partition_by") or [])
        return f"Calculer {p.get('into')} : {what}" + (f", par {by}" if by else "")
    if t == "unpivot":
        n = len(p.get("columns") or [])
        return (
            f"Dépivoter {n} colonnes en {p.get('name_into') or 'variable'} / "
            f"{p.get('value_into') or 'valeur'}"
        )
    if t == "pivot":
        n = len(p.get("values") or [])
        return f"Pivoter {p.get('name_column')} en {n} colonne{'s' if n > 1 else ''}"
    return proc["label"] if proc else str(t)


def _op_text(op: str | None) -> str:
    """Le libellé d'un opérateur, tel que le menu déroulant l'affiche.

    La même liste que celle servie à l'interface : la phrase de la carte et
    l'entrée du menu ne peuvent donc plus se contredire.
    """
    return FILTER_OPERATORS.get(op or "eq", op or "")
