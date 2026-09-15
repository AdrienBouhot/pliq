"""Types de stockage, significations devinées, barres de qualité."""

from __future__ import annotations

import datetime as dt

import pytest

from pliq import profiling


@pytest.mark.parametrize(
    "sql_type, expected",
    [
        ("VARCHAR", "string"),
        ("TEXT", "string"),
        ("BOOLEAN", "boolean"),
        ("TIMESTAMP", "date"),
        ("DATE", "date"),
        ("DECIMAL(18,2)", "decimal"),
        ("DOUBLE", "double"),
        ("BIGINT", "bigint"),
        ("INTEGER", "int"),
        (None, "string"),
    ],
)
def test_type_de_stockage(sql_type, expected):
    assert profiling.storage_of(sql_type) == expected


def _column(name, type_, values_list):
    return profiling.profile(
        [{"name": name, "type": type_}], [[v] for v in values_list]
    )[0]


def test_la_signification_vient_du_type_quand_il_est_franc():
    assert _column("d", "DATE", ["2024-01-01"])["meaning"] == "date"
    assert _column("n", "BIGINT", [1, 2])["meaning"] == "integer"
    assert _column("b", "BOOLEAN", [True])["meaning"] == "boolean"
    assert _column("x", "DECIMAL(9,2)", [1.5])["meaning"] == "decimal"


@pytest.mark.parametrize(
    "values_list, expected",
    [
        (["a@b.fr", "c@d.com"], "email"),
        (["https://x.fr", "http://y.com"], "url"),
        (["10.0.0.1", "192.168.1.2"], "ip"),
        (["2024-01-01", "2024-02-01 10:00"], "date"),
        (["FR", "ES", "IT"], "country"),
        (["1", "2", "3"], "integer"),
        (["1.5", "2,5"], "decimal"),
        (["true", "non", "1"], "boolean"),
        (["Dupont", "Martin"], "text"),
    ],
)
def test_la_signification_d_une_colonne_texte_se_devine(values_list, expected):
    assert _column("c", "VARCHAR", values_list)["meaning"] == expected


def test_une_signification_demande_une_majorite_franche():
    """Une seule adresse mail au milieu de noms ne fait pas une colonne Email."""
    assert _column("c", "VARCHAR", ["a@b.fr"] + ["Dupont"] * 5)["meaning"] == "text"


def test_la_barre_de_qualite_separe_vide_valide_invalide():
    col = _column("c", "VARCHAR", ["a@b.fr", "a@b.fr", "pas un mail", None, "  "])
    assert col["meaning"] == "text"

    col = _column(
        "mail",
        "VARCHAR",
        [
            "a@b.fr",
            "c@d.fr",
            "e@f.fr",
            "g@h.fr",
            "i@j.fr",
            "x@y.fr",
            "z@w.fr",
            "q@r.fr",
            "s@t.fr",
            "pas un mail",
            None,
        ],
    )
    assert col["meaning"] == "email"
    assert (col["n_ok"], col["n_nok"], col["n_empty"]) == (9, 1, 1)
    assert col["ok"] + col["nok"] + col["empty"] == pytest.approx(100.0, abs=0.2)


def test_une_colonne_vide_ne_divise_pas_par_zero():
    col = profiling.profile([{"name": "c", "type": "VARCHAR"}], [])[0]
    assert col["n_ok"] == 0 and col["ok"] == 0.0


def test_le_libelle_de_signification_est_traduit():
    assert _column("c", "VARCHAR", ["a@b.fr"] * 3)["meaning_label"] == "Email"


# ------------------------------------------------------------- suggestions


def _suggestions(col: dict) -> dict[str, dict]:
    return {s["processor"]: s for s in profiling.suggestions(col)}


def test_les_suggestions_dependent_de_la_signification():
    text = _suggestions({"name": "status", "meaning": "text", "storage": "string"})
    assert "text_transform" in text and "round" not in text
    assert text["text_transform"]["params"]["column"] == "status"

    count = _suggestions({"name": "montant", "meaning": "decimal", "storage": "double"})
    assert "round" in count and "text_transform" not in count

    date = _suggestions({"name": "d", "meaning": "date", "storage": "date"})
    assert "extract_date_parts" in date and "parse_date" not in date

    date_text = _suggestions({"name": "d", "meaning": "text", "storage": "string"})
    assert "parse_date" in date_text


def test_une_colonne_a_trous_propose_de_les_combler():
    without_gap = _suggestions({"name": "c", "meaning": "text", "storage": "string"})
    with_gaps = _suggestions(
        {"name": "c", "meaning": "text", "storage": "string", "n_empty": 3}
    )
    assert "fill_empty" not in without_gap
    assert "fill_empty" in with_gaps and "remove_empty" in with_gaps


def test_les_actions_universelles_sont_toujours_la():
    for meaning in ("text", "integer", "date", "boolean"):
        props = _suggestions({"name": "c", "meaning": meaning, "storage": "string"})
        assert {"rename", "keep_delete", "change_type", "filter_value"} <= set(props)


def test_les_suggestions_pointent_de_vrais_processeurs():
    from pliq import recipes as rcp

    for meaning in (
        "text",
        "integer",
        "decimal",
        "date",
        "datetime",
        "boolean",
        "email",
        "url",
        "ip",
        "country",
    ):
        for s in profiling.suggestions(
            {"name": "c", "meaning": meaning, "storage": "string", "n_empty": 1}
        ):
            assert s["processor"] in rcp.PROCESSORS, s["processor"]


# ------------------------------------------- dates et adresses : la vraie chose
#
# Les barres de qualité reposaient sur des formes écrites à la main. Une forme
# dit ce à quoi une valeur ressemble, jamais ce qu'elle vaut : `2026-99-99` a la
# forme d'une date sans en être une, et `999.999.999.999` celle d'une IPv4. À
# l'inverse la forme retenue ignorait ce que les entrepôts sérialisent vraiment
# — fraction de seconde et décalage horaire — et comptait faux des valeurs
# parfaitement bonnes. Les deux erreurs se voyaient dans la même barre.


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-13",
        "2026-09-13 10:30",
        "2026-09-13 10:30:00",
        "2026-09-13T10:30:00",
        "2026-09-13T10:30:00.123456",
        "2026-09-13T10:30:00.123456+00:00",
        "2026-09-13T10:30:00Z",
        "2026-09-13T10:30:00+0200",
        # Nanosecondes : Snowflake et BigQuery en rendent, `fromisoformat` non.
        "2026-09-13 10:30:00.123456789",
    ],
)
def test_un_horodatage_tel_que_l_entrepot_le_serialise_est_valide(value):
    assert profiling.is_temporal(value), value


@pytest.mark.parametrize(
    "value",
    [
        "2026-99-99",
        "2026-02-30",
        "2026-13-01",
        # Forme compacte : indistinguable d'un entier à huit chiffres, et
        # `date.fromisoformat` ne l'accepte qu'à partir de Python 3.11 — la
        # même colonne aurait changé de sens selon la version.
        "20260913",
        "pas une date",
        "",
    ],
)
def test_une_date_impossible_est_comptee_invalide(value):
    assert not profiling.is_temporal(value), value


@pytest.mark.parametrize(
    "value", ["192.168.1.2", "10.0.0.1", "255.255.255.255", "2001:db8::1", "::1"]
)
def test_une_vraie_adresse_ip_est_valide(value):
    assert profiling.is_ip(value), value


@pytest.mark.parametrize(
    "value", ["999.999.999.999", "256.1.1.1", "1.2.3", "1.2.3.4.5", "1", ""]
)
def test_une_adresse_ip_impossible_est_comptee_invalide(value):
    assert not profiling.is_ip(value), value


def test_un_horodatage_a_fraction_de_seconde_ne_noircit_plus_la_barre():
    """Le cas qui rendait la barre franchement mensongère : une colonne
    TIMESTAMP parfaitement saine, affichée invalide à cent pour cent."""
    col = _column(
        "t",
        "TIMESTAMP",
        ["2026-09-13T10:30:00.123456+00:00", "2026-09-13T10:30:00Z"],
    )
    assert col["nok"] == 0 and col["ok"] == 100.0


def test_une_date_rendue_comme_objet_est_valide():
    """Selon le pilote, l'entrepôt rend un `datetime` plutôt qu'une chaîne :
    la juger sur son format d'affichage n'aurait aucun sens."""
    col = _column(
        "t", "TIMESTAMP", [dt.datetime(2026, 9, 13, 10, 30), dt.date(2026, 9, 13)]
    )
    assert col["nok"] == 0


def test_une_colonne_d_horodatages_iso_est_reconnue_comme_date():
    """La reconnaissance souffrait du même défaut que la validation : une
    colonne texte d'horodatages sérialisés passait pour du texte libre."""
    col = _column("d", "VARCHAR", ["2026-09-13T10:30:00.123456+00:00"] * 5)
    assert col["meaning"] == "date" and col["nok"] == 0


def test_la_reconnaissance_et_la_validation_suivent_le_meme_critere():
    """Deviner avec une forme approximative puis valider pour de bon donnerait
    une colonne « Adresse IP » invalide à cent pour cent — un diagnostic que
    personne ne peut lire."""
    col = _column("c", "VARCHAR", ["999.999.999.999"] * 10)
    assert col["meaning"] == "text" and col["nok"] == 0
