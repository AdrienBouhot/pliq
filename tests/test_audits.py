"""Les défauts que les deux audits du 15 septembre 2026 ont reproduits.

Un test par constat corrigé, nommé par son identifiant d'audit, et écrit dans
le sens de la correction : c'est ce fichier qui dit qu'un défaut ne revient
pas. Les audits eux-mêmes assertaient le *défaut*, pour être rejouables ; ces
tests-ci assertent ce qui doit désormais se produire.

Les constats qui tenaient à l'interface sont dans `test_frontend.py`, et ceux
qui tenaient à un fichier du projet dans `test_files.py` : on ne déplace pas un
test vers ce fichier sous prétexte qu'un audit l'a nommé.
"""

from __future__ import annotations

import json
import math
import os

import pytest
import yaml

from pliq import full_check, profiling, projects
from pliq import recipes as rcp
from pliq import warehouse
from pliq.config import ConfigError, Settings, declared_paths
from pliq.recipes import RecipeError

from .conftest import Bench, prepare_spec


def refusal(bench: Bench, spec: dict) -> str:
    with pytest.raises(RecipeError) as exc:
        bench.compile(spec)
    return str(exc.value)


# ------------------------------------------------ configuration du projet


def test_b01_un_model_paths_en_chaine_ne_se_lit_pas_caractere_par_caractere(
    settings: Settings,
):
    """`model-paths: models` — sans crochets — est un YAML que dbt accepte.

    La boucle itérait sur les *caractères* de la chaîne : l'atelier écrivait
    ses modèles dans `m/`, un dossier que dbt ne lit jamais. L'écran annonçait
    « enregistré », le parse suivant ne voyait rien, et le renommage comme le
    contrôle d'homonymie cherchaient au même mauvais endroit.
    """
    (settings.project_dir / "dbt_project.yml").write_text(
        "name: demo\nprofile: demo\nmodel-paths: models\nseed-paths: seeds\n"
    )
    settings._cache.clear()
    assert settings.model_dirs() == ["models"]
    assert "seeds" in settings.code_dirs()
    assert "s" not in settings.code_dirs()


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        ("models", ["models"]),
        (["a", "b"], ["a", "b"]),
        (None, ["défaut"]),
        ("", ["défaut"]),
        ("transform/models/", ["transform/models"]),
        (["a", "a"], ["a"]),
    ],
)
def test_b01_normalisation_des_chemins_declares(raw_text, expected):
    assert declared_paths(raw_text, ["défaut"]) == expected


def test_b09_un_dbt_project_yml_qui_n_est_pas_une_table_est_un_refus(
    settings: Settings,
):
    """Un document YAML valide dont la racine est une liste faisait un 500.

    `safe_load(...) or {}` ne rattrapait que le document vide ; le `.get()`
    suivant levait un `AttributeError` jusqu'à la route, sans un mot sur le
    fichier à réparer.
    """
    (settings.project_dir / "dbt_project.yml").write_text("- a\n- b\n")
    settings._cache.clear()
    with pytest.raises(ConfigError, match=r"dbt_project\.yml"):
        assert settings.project_name


def test_b09_un_profiles_yml_qui_n_est_pas_une_table_est_un_refus(
    settings: Settings,
):
    (settings.profiles_dir / "profiles.yml").write_text("- x\n")
    settings._cache.clear()
    with pytest.raises(ConfigError, match=r"profiles\.yml"):
        settings.profile()


def test_b23_les_yaml_du_projet_ne_sont_relus_qu_une_fois(
    settings: Settings, monkeypatch
):
    """Un seul `GET /api/project` ouvrait 15 fois `dbt_project.yml`.

    Et rendait 6 fois `profiles.yml` par le moteur Jinja de dbt, dont quatre
    pour le seul `active_target()`. Le cache est invalidé par le `mtime` : un
    fichier modifié sous l'atelier reste relu.
    """
    reads = []
    real = Settings._yml_cache

    def count_them(self, key, f):
        reads.append(key)
        return real(self, key, f)

    monkeypatch.setattr(Settings, "_yml_cache", count_them)
    settings._cache.clear()
    for _ in range(5):
        assert settings.project_name
        assert settings.active_target()
    # Le cache est interrogé à chaque accès ; ce qu'on compte, c'est qu'il ne
    # relise pas le disque à chaque fois.
    assert len(settings._cache) <= 2

    before = settings.project_yml
    (settings.project_dir / "dbt_project.yml").write_text(
        "name: renomme\nprofile: demo\n"
    )
    # Le `mtime` suffit à invalider : la nouvelle valeur doit être lue.
    assert settings.project_yml is not before or settings.project_name == "renomme"


# ---------------------------------------------------------------- registre


def test_b15_le_registre_survit_a_une_forme_inattendue(tmp_path, monkeypatch):
    """`{"projects": null}` cassait `remember` : la clé existe, `setdefault`
    ne la remplace pas, et la boucle suivante parcourait `None`."""
    registry = tmp_path / "projects.json"
    monkeypatch.setattr(projects, "REGISTRY", registry)
    registry.write_text(json.dumps({"projects": None, "last": 12}))
    assert projects._read_registry() == {"projects": [], "last": None}

    project = tmp_path / "demo"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: demo\n")
    projects.remember(project, active=True)
    assert str(project.resolve()) in projects._read_registry()["projects"]


def test_b15_un_registre_illisible_est_mis_de_cote_et_non_efface(tmp_path, monkeypatch):
    registry = tmp_path / "projects.json"
    monkeypatch.setattr(projects, "REGISTRY", registry)
    registry.write_text("{ ceci n'est pas du JSON")
    assert projects._read_registry() == {"projects": [], "last": None}
    assert (tmp_path / "projects.json.invalide").exists()


def test_b14_l_ecriture_est_atomique(tmp_path):
    """Un temporaire voisin puis `os.replace` : un lecteur voit l'ancien
    fichier entier ou le nouveau, jamais un fichier à moitié écrit."""
    from pliq.atomic import write_text_atomically

    target = tmp_path / "m.sql"
    write_text_atomically(target, "select 1\n")
    assert target.read_text() == "select 1\n"
    write_text_atomically(target, "select 2\n")
    assert target.read_text() == "select 2\n"
    # Aucun temporaire ne survit à l'écriture.
    assert [p.name for p in tmp_path.iterdir()] == ["m.sql"]


@pytest.mark.parametrize("mode", [0o644, 0o664, 0o600, 0o640])
def test_b14_l_ecriture_atomique_garde_le_mode_du_fichier(tmp_path, mode):
    """Réécrire un fichier ne change pas qui peut le lire.

    `mkstemp` crée en 0600 et `os.replace` emporte ce mode : un modèle en
    0644 repassait en 0600 à chaque enregistrement, et un `dbt build` lancé
    par un autre compte ne le lisait plus. Le mode restrictif se garde aussi :
    c'est le mode *de la destination* qu'on reconduit, pas un mode choisi.
    """
    from pliq.atomic import write_text_atomically

    target = tmp_path / "m.sql"
    target.write_text("select 1\n")
    os.chmod(target, mode)
    write_text_atomically(target, "select 2\n")
    assert target.stat().st_mode & 0o777 == mode
    assert target.read_text() == "select 2\n"


def test_b14_un_fichier_neuf_suit_l_umask_et_non_le_temporaire(tmp_path):
    """Un modèle qui vient de naître se lit comme n'importe quel fichier.

    Le cas manquait au contrôle précédent : sans destination à interroger,
    l'écriture retombait sur le mode du temporaire — 0600 — et *tout* ce que
    l'atelier produisait naissait illisible pour les autres comptes.
    """
    from pliq import atomic

    fresh = tmp_path / "neuf.sql"
    atomic.write_text_atomically(fresh, "select 1\n")
    assert fresh.stat().st_mode & 0o777 == atomic._NEW_FILE_MODE
    assert atomic._NEW_FILE_MODE == 0o666 & ~_current_umask()


def _current_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


# ------------------------------------------------------------- entrepôt


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_b08_un_flottant_non_fini_reste_serialisable(value: float):
    """`json.dumps` écrivait `NaN`, que `JSON.parse` refuse : la réponse
    entière devenait illisible, et l'écran affichait « l'atelier ne répond
    plus » au lieu d'une case. `_decimal_cell` traitait déjà le cas."""
    cell_value = warehouse._cell(value)
    assert isinstance(cell_value, str)
    json.dumps({"v": cell_value}, allow_nan=False)


def test_b08_un_flottant_fini_reste_un_nombre():
    assert warehouse._cell(1.5) == 1.5
    assert math.isfinite(warehouse._cell(1.5))


# --------------------------------------------------------- types de colonne


@pytest.mark.parametrize(
    ("sql_type", "family"),
    [
        ("INTERVAL", "interval"),
        ("INTEGER[]", "composite"),
        ("STRUCT(a INTEGER)", "composite"),
        ("STRUCT(a VARCHAR)", "composite"),
        ("MAP(VARCHAR, INTEGER)", "composite"),
        ("ARRAY<DATE>", "composite"),
        ("JSON", "composite"),
        ("VARIANT", "composite"),
        ("VARCHAR(50)", "texte"),
        ("DECIMAL(18, 2)", "nombre"),
        ("TIMESTAMP WITH TIME ZONE", "date"),
        ("BIGINT", "nombre"),
        ("BOOLEAN", "booléen"),
    ],
)
def test_b12_le_type_est_reconnu_sur_son_nom_et_non_sur_une_sous_chaine(
    sql_type: str, family: str
):
    """`INTERVAL` contient « INT », `STRUCT(a VARCHAR)` contient « CHAR ».

    Trois conséquences visibles : une colonne saine affichée 100 % invalide
    par la barre de qualité, un `trim()` posé sur un STRUCT que l'entrepôt
    refuse, et un profil complet qui n'écartait aucun des types qu'il annonce
    écarter.
    """
    assert rcp.type_family(sql_type) == family


def test_b12_aucun_trim_sur_un_type_composite():
    """`(s is null or trim(s) = '')` sur un STRUCT est refusé par DuckDB."""
    with rcp.using_dialect("duckdb"):
        assert rcp.empty_sql('"s"', "STRUCT(a VARCHAR)") == '"s" is null'
        assert "trim" in rcp.empty_sql('"s"', "VARCHAR")


def test_b12_le_profil_complet_ecarte_vraiment_les_types_non_agregeables():
    _, plan, discarded = full_check.profile_measures(
        [
            {"name": "j", "type": "JSON"},
            {"name": "t", "type": "INTEGER[]"},
            {"name": "s", "type": "STRUCT(a INTEGER)"},
            {"name": "n", "type": "INTEGER"},
        ]
    )
    assert sorted(discarded["type"]) == ["j", "s", "t"]
    assert [c["name"] for c in plan] == ["n"]


def test_b12_une_colonne_interval_n_est_pas_lue_comme_un_entier():
    assert profiling.storage_of("INTERVAL") != "int"
    assert profiling._meaning_from_type("INTERVAL") is None
    assert profiling._meaning_from_type("INTEGER[]") is None


# ------------------------------------------------------------ « vide »


def test_b05_est_vide_veut_dire_la_meme_chose_partout():
    """Une cellule d'espaces était « vide » pour trois écrans sur quatre.

    `_predicate` écrivait sa propre définition au lieu d'appeler `empty_sql`,
    alors que `writers` dit en toutes lettres que cette définition doit être
    unique — et pourquoi.
    """
    with rcp.using_dialect("duckdb"):
        state = rcp.ColumnState(["pays"], {"pays": "VARCHAR"})
        empty = rcp.processors._predicate(
            state, {"column": "pays", "operator": "empty"}
        )
        assert empty == rcp.empty_sql(rcp.q("pays"), "VARCHAR")
        predicate = rcp.processors._predicate(
            state, {"column": "pays", "operator": "not_empty"}
        )
        assert predicate == rcp.filled_sql(rcp.q("pays"), "VARCHAR")


def test_b05_une_cellule_d_espaces_est_vide_pour_le_filtre(bench: Bench):
    bench.table(
        "pays_bruts",
        "id integer, pays varchar",
        [(1, None), (2, ""), (3, "   "), (4, "fr")],
    )
    spec = prepare_spec(
        [
            {
                "type": "filter_value",
                "params": {"column": "pays", "operator": "empty", "action": "keep"},
            }
        ],
        ref="pays_bruts",
    )
    _, lines = bench.run(spec)
    assert sorted(r[0] for r in lines) == [1, 2, 3]


# ------------------------------------------------------- filtre à une valeur


@pytest.mark.parametrize("op", ["eq", "ne", "contains", "starts_with", "gt", "lte"])
def test_b03_un_operateur_a_une_valeur_refuse_la_seconde(bench: Bench, op: str):
    """Onze opérateurs sur douze ne lisaient que `values[0]`.

    La carte de l'étape, elle, affichait toute la liste : l'écran annonçait un
    filtre qui n'était pas celui qui s'exécutait.
    """
    bench.table("statuts", "id integer, statut varchar", [(1, "a"), (2, "b"), (3, "c")])
    spec = prepare_spec(
        [
            {
                "type": "filter_value",
                "params": {
                    "column": "statut",
                    "operator": op,
                    "values": ["a", "b"],
                    "action": "keep",
                },
            }
        ],
        ref="statuts",
    )
    message = refusal(bench, spec)
    assert "une seule valeur" in message
    assert "est dans" in message


def test_b03_est_dans_accepte_toujours_plusieurs_valeurs(bench: Bench):
    bench.table("statuts", "id integer, statut varchar", [(1, "a"), (2, "b"), (3, "c")])
    spec = prepare_spec(
        [
            {
                "type": "filter_value",
                "params": {
                    "column": "statut",
                    "operator": "in",
                    "values": ["a", "b"],
                    "action": "keep",
                },
            }
        ],
        ref="statuts",
    )
    _, lines = bench.run(spec)
    assert sorted(r[0] for r in lines) == [1, 2]


# --------------------------------------------------------------- jointure


def test_b06_deux_colonnes_que_seule_la_casse_separe_sont_refusees(bench: Bench):
    """`a.id` et `b.ID` compilaient sans un mot, et DuckDB rendait `ID_1`.

    Le compilateur annonçait donc une colonne qui n'existe pas, et tout ce qui
    s'appuie sur `output_columns()` raisonnait dessus.
    """
    bench.table("gauche", "id integer, v integer", [(1, 10)])
    bench.table("droite", '"ID" integer, w integer', [(1, 20)])
    spec = {
        "name": "jointe",
        "type": "join",
        "inputs": [
            {"ref": "gauche", "alias": "gauche"},
            {"ref": "droite", "alias": "droite"},
        ],
        "joins": [{"type": "inner", "on": [{"left": "id", "right": "ID"}]}],
        "select": [
            {"from": "gauche", "column": "id"},
            {"from": "droite", "column": "ID"},
        ],
        "output": {"materialized": "view"},
    }
    assert "casse" in refusal(bench, spec)


def test_a16_le_mode_toutes_les_colonnes_nomme_lui_meme_les_homonymes(bench: Bench):
    """`a.*, b.*` laissait l'entrepôt trancher : DuckDB rend `id` et `ID_1`.

    Le compilateur annonçait `id` deux fois, et l'analyse d'impact raisonnait
    sur cette annonce fausse.
    """
    bench.table("commandes", "id integer, client_id integer", [(1, 7)])
    bench.table("clients", "id integer, nom varchar", [(7, "Alice")])
    spec = {
        "name": "jointe",
        "type": "join",
        "inputs": [
            {"ref": "commandes", "alias": "commandes"},
            {"ref": "clients", "alias": "clients"},
        ],
        "joins": [{"type": "inner", "on": [{"left": "client_id", "right": "id"}]}],
        "output": {"materialized": "view"},
    }
    announced = rcp.output_columns(spec, bench.input_columns(spec))
    actual, _ = bench.run(spec)
    assert announced == actual
    assert "clients_id" in actual


# ------------------------------------------------------------- alias de CTE


@pytest.mark.parametrize("word", ["order", "select", "group", "from"])
def test_a12_un_alias_qui_est_un_mot_reserve_est_prefixe(word: str):
    """Une source parfaitement légitime nommée `order` produisait
    `with order as (...)` : une erreur de syntaxe, plusieurs écrans après."""
    with rcp.using_dialect("duckdb"):
        assert rcp.input_alias({"ref": word}, 0) == f"_{word}"


def test_a13_deux_alias_que_seule_la_casse_separe_sont_refuses():
    """`A` et `a` passaient la vérification, et DuckDB refusait les deux CTE."""
    with rcp.using_dialect("duckdb"), pytest.raises(RecipeError, match="casse"):
        rcp.input_aliases([{"ref": "A"}, {"ref": "a"}])


def test_a13_un_cte_libre_evite_aussi_les_homonymes_de_casse():
    assert rcp.free_cte("texte", {"Texte"}) == "texte_2"


# -------------------------------------------------------- commentaires SQL


def test_a14_une_expression_multiligne_ne_casse_pas_le_commentaire(bench: Bench):
    """Le libellé était inséré après un seul `--` : une formule valide écrite
    sur deux lignes posait sa seconde ligne *hors* du commentaire, juste avant
    le CTE, et le fichier entier devenait invalide."""
    bench.table("src", "id integer", [(1,)])
    spec = prepare_spec(
        [{"type": "formula", "params": {"into": "calc", "expression": "id +\n1"}}],
        ref="src",
    )
    sql = bench.compile(spec)
    comments = [line for line in sql.splitlines() if line.lstrip().startswith("-- 1 ·")]
    # Un seul commentaire, entier, sur une seule ligne : c'est le libellé qui
    # se replie, jamais l'expression SQL qu'on modifierait alors.
    assert comments == ["-- 1 · Calculer calc = id + 1"]
    assert "id +\n1" in sql  # l'expression, elle, est rendue telle quelle
    # Et le SQL reste exécutable, ce qui n'était pas le cas quand la seconde
    # ligne du libellé atterrissait hors du commentaire.
    _, lines = bench.run(spec)
    assert lines == [(1, 2)]


def test_b13_le_libelle_d_etape_vient_du_serveur_et_dit_le_sens_du_tri():
    """Le sens du tri est tout ce que l'étape fait, et c'est justement lui qui
    manquait au commentaire du `.sql`."""
    assert (
        rcp.describe_step(
            {"type": "sort", "params": {"column": "d", "descending": True}}
        )
        == "Trier par d (décroissant)"
    )
    assert (
        rcp.describe_step(
            {"type": "keep_delete", "params": {"columns": ["a"], "action": "keep"}}
        )
        == "Conserver 1 colonne"
    )


def test_b13_le_delta_d_une_etape_porte_sa_phrase(bench: Bench):
    bench.table("src", "id integer", [(1,)])
    spec = prepare_spec(
        [{"type": "sort", "params": {"column": "id", "descending": True}}],
        ref="src",
    )
    _, _, deltas = rcp.compile_prepare(spec, bench.input_columns(spec))
    assert deltas[0]["label"] == "Trier par id (décroissant)"


# ------------------------------------------------------------ validation SQL


@pytest.mark.parametrize("text", ["1; select 2", "true; drop table t", "select 2"])
def test_b18_une_expression_est_une_expression_et_une_seule(text: str):
    """`parse_one` rend la première instruction et ne dit rien de la seconde.

    Ce n'est pas une faille — l'atelier offre déjà d'écrire du SQL arbitraire
    — mais une promesse plus faible que celle affichée.
    """
    with rcp.using_dialect("duckdb"), pytest.raises(RecipeError):
        rcp.check_expr(text, "test")


def test_b18_une_condition_ne_peut_pas_ajouter_une_clause():
    """`true limit 0` faisait passer une clause du `select` de validation pour
    une condition : validée, elle ramenait zéro ligne."""
    with rcp.using_dialect("duckdb"), pytest.raises(RecipeError, match="clause SQL"):
        rcp.check_predicate("true limit 0", "test")


def test_b18_un_point_virgule_dans_un_litteral_reste_permis():
    with rcp.using_dialect("duckdb"):
        assert rcp.check_expr("'a;b'", "test") == "'a;b'"
        assert rcp.check_predicate("x = ';'", "test") == "x = ';'"


def test_b19_le_controle_complet_supporte_un_sql_termine_par_un_point_virgule():
    """Enveloppé tel quel, le `;` se retrouvait *à l'intérieur* de la
    parenthèse, et l'entrepôt rendait une erreur de syntaxe brute."""
    wrapper = full_check.aggregate_over("select 1 as a;", "count(*) as n")
    assert ";" not in wrapper


# ------------------------------------------------------------- suggestions


def test_b16_les_suggestions_citent_les_noms_de_colonne():
    """Sur une colonne `order`, l'étape proposée en un clic était du SQL
    invalide, et le refus arrivait deux écrans plus loin."""
    with rcp.using_dialect("duckdb"):
        proposed = profiling.suggestions(
            {"name": "order", "storage": "int", "meaning": "integer"}
        )
        interval = next(p for p in proposed if p["params"].get("condition"))
        assert interval["params"]["condition"] == '"order" > 0'

        # `q()` ne cite que ce qui en a besoin : un nom sage reste nu, et
        # c'est bien ce qu'on veut lire dans le `.sql`.
        boolean = profiling.suggestions(
            {"name": "est valide", "storage": "boolean", "meaning": "boolean"}
        )
        reversed_map = next(p for p in boolean if p["params"].get("expression"))
        assert reversed_map["params"]["expression"] == 'not "est valide"'


# --------------------------------------------------- validation des formes


@pytest.mark.parametrize(
    "spec",
    [
        {"steps": [{"type": "round", "params": ["oops"]}]},
        {"steps": [{"type": 3}]},
        {"output": {"unique_key": "id"}},
        {"output": {"incremental": 3}},
        {"joins": "x"},
        {"select": [1]},
    ],
)
def test_a22_une_forme_imbriquee_invalide_est_un_refus_et_non_une_panne(spec):
    """`spec: dict` laisse entrer n'importe quel objet JSON, et le premier
    `.get()` sur autre chose qu'un objet répondait 500."""
    with pytest.raises(RecipeError):
        rcp.check_spec_shape(spec)


def test_a22_un_decimal_n_est_pas_tronque_en_silence():
    """`int(1.9)` rendait 1 : la valeur écrite différait de celle qu'on lit."""
    with rcp.using_dialect("duckdb"):
        state = rcp.ColumnState(["v"], {"v": "DOUBLE"})
        with pytest.raises(RecipeError, match="entier"):
            rcp.PROCESSORS["round"]["fn"](state, {"column": "v", "decimals": 1.9})


# -------------------------------------------------------- scripts visuels


def test_a23_un_script_visuel_mal_forme_ne_rend_pas_le_flow_indisponible(tmp_path):
    """Le contrat est `dict | None`, et il était tenu par optimisme : un
    fichier dont la racine est une liste était rendu tel quel."""
    folder = tmp_path / rcp.RECIPE_DIR
    folder.mkdir(parents=True)
    (folder / "casse.yml").write_text("- a\n- b\n")
    (folder / "bonne.yml").write_text("name: bonne\ntype: prepare\n")

    assert rcp.load_recipe(tmp_path, "casse") is None
    assert "script de recipe" in rcp.readonly_reason(tmp_path, "casse")
    # Le reste du projet reste lisible, et l'erreur est nommée.
    readable, errors = rcp.recipes_and_errors(tmp_path)
    assert list(readable) == ["bonne"]
    assert "casse" in errors


def test_a23_absent_et_illisible_ne_se_disent_pas_pareil(tmp_path):
    assert rcp.load_recipe(tmp_path, "inconnue") is None
    assert rcp.readonly_reason(tmp_path, "inconnue") is None


# ------------------------------------------------------- types de recipe


def test_b25_un_raccourci_d_interface_le_dit():
    """`distinct` et `topn` étaient dans le contrat public de l'API sans avoir
    de compilateur : un client qui lit `/api/processors` les croyait
    utilisables."""
    assert "topn" not in rcp.RECIPE_TYPES
    assert rcp.RECIPE_TYPES["distinct"]["alias_of"] == "prepare"
    with pytest.raises(RecipeError, match="raccourci"):
        rcp.compile_recipe({"type": "distinct", "inputs": []}, {})


# ------------------------------------------------------------- incrémental
#
# Ces constats-là ne se voient qu'au *deuxième* build : le premier crée la
# table, `is_incremental()` est faux, et tout se passe bien. `Bench.build`
# rejoue la matérialisation de dbt pour que le second ait lieu ici.


def incremental_output(**extra) -> dict:
    base = {
        "materialized": "incremental",
        "incremental_strategy": "delete+insert",
        "on_schema_change": "append_new_columns",
    }
    base.update(extra)
    return base


def test_a02_une_borne_qui_est_une_mesure_calculee_est_refusee(bench: Bench):
    """`sum(seq) as seq` était accepté comme borne : le contrôle vérifiait le
    *nom* de la colonne de sortie, pas son calcul.

    Avec seq=10 et 20, la cible contient 30 ; une ligne nouvelle à seq=25 est
    alors écartée par `seq > 30`. Deux builds réussissent, et le total est
    faux de moitié.
    """
    bench.table("evts", "id integer, seq integer", [(1, 10), (1, 20)])
    spec = {
        "name": "par_id",
        "type": "group",
        "inputs": [{"ref": "evts", "alias": "evts"}],
        "group_by": ["id"],
        "aggregations": [{"fn": "sum", "column": "seq", "alias": "seq"}],
        "output": incremental_output(unique_key=["id"], incremental={"column": "seq"}),
    }
    message = refusal(bench, spec)
    assert "mesure calculée" in message
    assert "max(seq) as seq" in message


def test_a02_un_max_non_filtre_de_la_colonne_reste_accepte(bench: Bench):
    bench.table("evts", "id integer, seq integer", [(1, 10)])
    spec = {
        "name": "par_id",
        "type": "group",
        "inputs": [{"ref": "evts", "alias": "evts"}],
        "group_by": ["id"],
        "aggregations": [{"fn": "max", "column": "seq", "alias": "seq"}],
        "output": incremental_output(unique_key=["id"], incremental={"column": "seq"}),
    }
    assert "is_incremental()" in bench.compile(spec)


def test_a02_un_max_filtre_est_refuse(bench: Bench):
    """Un filtre sur la mesure de borne la sort de l'échelle de l'entrée."""
    bench.table("evts", "id integer, seq integer", [(1, 10)])
    spec = {
        "name": "par_id",
        "type": "group",
        "inputs": [{"ref": "evts", "alias": "evts"}],
        "group_by": ["id"],
        "aggregations": [
            {"fn": "max", "column": "seq", "alias": "seq", "filter": "seq > 0"}
        ],
        "output": incremental_output(unique_key=["id"], incremental={"column": "seq"}),
    }
    assert "filtrée" in refusal(bench, spec)


def test_a03_le_filtre_de_groupe_dit_ce_qu_il_advient_d_une_cle_nulle(
    bench: Bench,
):
    """SQL ne considère pas NULL égal à NULL, et dbt rapproche ses clés
    uniques à l'égalité simple : un groupe de clé nulle reste figé sur sa
    valeur déjà écrite, et aucun build n'échoue.

    La limite est réelle et tient à dbt ; ce qui manquait, c'est qu'elle soit
    écrite là où on la lira — dans le fichier.
    """
    bench.table("evts", "k varchar, seq integer, v integer", [("a", 1, 10)])
    spec = {
        "name": "par_k",
        "type": "group",
        "inputs": [{"ref": "evts", "alias": "evts"}],
        "group_by": ["k"],
        "aggregations": [
            {"fn": "sum", "column": "v", "alias": "total"},
            {"fn": "max", "column": "seq", "alias": "seq"},
        ],
        "output": incremental_output(unique_key=["k"], incremental={"column": "seq"}),
    }
    sql = bench.compile(spec)
    assert "clé est nulle" in sql
    assert "not_null" in sql


def test_a04_un_dedoublonnage_incremental_trie_a_l_envers_est_refuse(
    bench: Bench,
):
    """Le classement ne porte que sur l'arrivage, jamais sur la ligne déjà
    gagnante : avec « garder la première » sur une séquence croissante, la
    cible passe de (id=1, seq=1) à (1, 2) au deuxième build, là où un recalcul
    complet garde (1, 1)."""
    bench.table("evts", "id integer, seq integer", [(1, 1)])
    spec = prepare_spec(
        [
            {
                "type": "dedup_key",
                "params": {
                    "keys": ["id"],
                    "order_by": [{"column": "seq", "descending": False}],
                },
            }
        ],
        ref="evts",
        output=incremental_output(unique_key=["id"], incremental={"column": "seq"}),
    )
    message = refusal(bench, spec)
    assert "déjà gagnante" in message
    assert "seq » décroissant" in message


def test_a04_un_tri_par_la_borne_decroissante_reste_accepte(bench: Bench):
    bench.table("evts", "id integer, seq integer", [(1, 1)])
    spec = prepare_spec(
        [
            {
                "type": "dedup_key",
                "params": {
                    "keys": ["id"],
                    "order_by": [{"column": "seq", "descending": True}],
                },
            }
        ],
        ref="evts",
        output=incremental_output(unique_key=["id"], incremental={"column": "seq"}),
    )
    assert "is_incremental()" in bench.compile(spec)


def test_a05_un_pivot_incremental_dont_les_groupes_traversent_est_refuse(
    bench: Bench,
):
    """`pivot` agrège, mais ne passait par aucun des garde-fous.

    Le scénario de l'audit : une sortie groupée par `id` et `seq`, avec
    `unique_key = [id]`. dbt retire alors, pour chaque `id` réécrit, *toutes*
    ses lignes — la seconde exécution retire la ligne du premier groupe au lieu
    de conserver les deux. La reproduction rend une ligne là où le recalcul
    complet en rend deux. « Grouper » et « Dédoublonner » posaient déjà ce
    refus ; « Pivoter » y échappait.
    """
    bench.table(
        "evts", "id integer, seq integer, mois varchar, ca integer", [(1, 1, "jan", 10)]
    )
    spec = prepare_spec(
        [
            {
                "type": "pivot",
                "params": {
                    "key_columns": ["id", "seq"],
                    "name_column": "mois",
                    "value_column": "ca",
                    "values": ["jan", "fev"],
                    "aggregate": "sum",
                    "prefix": "ca_",
                },
            }
        ],
        ref="evts",
        output=incremental_output(unique_key=["id"], incremental={"column": "seq"}),
    )
    message = refusal(bench, spec)
    assert "clés uniques doivent couvrir le regroupement" in message
    assert "« seq »" in message


def test_a05_un_pivot_dont_la_borne_est_une_cle_de_groupe_reste_accepte(
    bench: Bench,
):
    bench.table(
        "evts",
        "id integer, jour integer, mois varchar, ca integer",
        [(1, 1, "jan", 10)],
    )
    spec = prepare_spec(
        [
            {
                "type": "pivot",
                "params": {
                    "key_columns": ["id", "jour"],
                    "name_column": "mois",
                    "value_column": "ca",
                    "values": ["jan"],
                    "aggregate": "sum",
                    "prefix": "ca_",
                },
            }
        ],
        ref="evts",
        output=incremental_output(
            unique_key=["id", "jour"], incremental={"column": "jour"}
        ),
    )
    assert "is_incremental()" in bench.compile(spec)


def test_a06_un_empilement_incremental_ecrit_l_hypothese_qu_il_fait(
    bench: Bench,
):
    """La même borne `max(seq)` est appliquée à toutes les entrées : si A est
    arrivé à 100 et B à 10, une ligne nouvelle de B à 11 disparaît. Le calcul
    n'est juste que sous une hypothèse d'alignement que l'atelier ne peut pas
    vérifier — elle doit donc être écrite là où on la lira."""
    bench.table("flux_a", "id integer, seq integer", [(1, 1)])
    bench.table("flux_b", "id integer, seq integer", [(2, 1)])
    spec = {
        "name": "empile",
        "type": "stack",
        "inputs": [
            {"ref": "flux_a", "alias": "flux_a"},
            {"ref": "flux_b", "alias": "flux_b"},
        ],
        "output": incremental_output(unique_key=["id"], incremental={"column": "seq"}),
    }
    sql = bench.compile(spec)
    assert "la même pour les 2 entrées" in sql
    assert "en retard sur une autre" in sql


def test_a09_une_cle_unique_absente_du_resultat_est_refusee(bench: Bench):
    """Le premier build crée la table ; c'est le suivant qui échoue, en
    cherchant une clé qu'une étape a retirée entre-temps."""
    bench.table("evts", "id integer, seq integer, v integer", [(1, 1, 10)])
    spec = prepare_spec(
        [{"type": "keep_delete", "params": {"columns": ["id"], "action": "delete"}}],
        ref="evts",
        output=incremental_output(unique_key=["id"]),
    )
    message = refusal(bench, spec)
    assert "clé unique" in message
    assert "Colonnes produites" in message


def test_a17_empiler_deux_types_incompatibles_est_refuse(bench: Bench):
    """Stack alignait les colonnes par nom et ignorait leur compatibilité :
    DuckDB rendait une colonne texte contenant « 1 » et « abc », d'autres
    moteurs refusent la requête."""
    bench.table("num", "id integer", [(1,)])
    bench.table("txt", "id varchar", [("abc",)])
    spec = {
        "name": "empile",
        "type": "stack",
        "inputs": [{"ref": "num", "alias": "num"}, {"ref": "txt", "alias": "txt"}],
        "output": {"materialized": "view"},
    }
    message = refusal(bench, spec)
    assert "même genre de valeur" in message
    assert "Changer le type" in message


@pytest.mark.parametrize(
    ("warehouse", "strategy"),
    [("bigquery", "delete+insert"), ("athena", "delete+insert"), ("postgres", "merge")],
)
def test_a11_une_strategie_absente_de_l_adaptateur_est_refusee(
    warehouse: str, strategy: str
):
    """Toutes les stratégies étaient proposées sans matrice de capacités, et
    `delete+insert` choisie partout : une recipe se configurait normalement,
    puis échouait au `dbt build`.

    Source : https://docs.getdbt.com/docs/build/incremental-strategy
    """
    with rcp.using_dialect(warehouse), pytest.raises(RecipeError, match="n'existe pas"):
        rcp.config_block(
            {
                "materialized": "incremental",
                "incremental_strategy": strategy,
                "unique_key": ["id"],
            }
        )


def test_a11_le_defaut_suit_l_entrepot():
    with rcp.using_dialect("bigquery"):
        assert rcp.default_strategy() == "merge"
        assert "delete+insert" not in rcp.strategies_available()
    with rcp.using_dialect("duckdb"):
        assert rcp.default_strategy() == "delete+insert"


def test_b02_une_recipe_sql_refuse_une_borne_qu_elle_n_ecrira_pas(bench: Bench):
    """L'onglet Sortie proposait « Colonne de repère » pour toutes les recipes,
    et `compile_sql` rend le SQL de l'utilisateur tel quel : rien n'arrivait
    dans le fichier. Avec `append`, la table doublait à chaque build."""
    bench.table("src", "id integer, ts integer", [(1, 1)])
    spec = {
        "name": "manuel",
        "type": "sql",
        "inputs": [{"ref": "src", "alias": "src"}],
        "sql": "select * from {{ ref('src') }}",
        "output": incremental_output(
            incremental_strategy="append", incremental={"column": "ts"}
        ),
    }
    message = refusal(bench, spec)
    assert "écrit elle-même sa borne" in message
    assert "is_incremental()" in message


def test_b02_une_recipe_sql_sans_borne_compile_toujours(bench: Bench):
    bench.table("src", "id integer", [(1,)])
    spec = {
        "name": "manuel",
        "type": "sql",
        "inputs": [{"ref": "src", "alias": "src"}],
        "sql": "select * from {{ ref('src') }}",
        "output": incremental_output(unique_key=["id"]),
    }
    assert "select * from" in bench.compile(spec)


# ------------------------------------------------- fichiers du projet


def test_a30_un_ref_qui_n_en_est_pas_un_n_est_pas_reecrit():
    """La regex n'était pas ancrée : elle réécrivait une chaîne SQL contenant
    `ref("old")`, un commentaire, et jusqu'à `my_ref("old")` — le texte métier
    ou l'argument d'une autre macro changeaient sans qu'un lien dbt le
    justifie."""
    unchanged = [
        "select 'un texte avec ref(\"old\") dedans' as x",
        '{{ my_ref("old") }}',
        '-- ref("old")',
        '{{ autre.ref("old") }}',
    ]
    for text in unchanged:
        assert rcp.rename_ref_in_sql(text, "old", "new") == text


def test_a30_un_vrai_ref_est_toujours_suivi():
    assert (
        rcp.rename_ref_in_sql("select * from {{ ref('old') }}", "old", "new")
        == "select * from {{ ref('new') }}"
    )


def test_a29_un_modele_python_voit_son_dbt_ref_suivre():
    """`CODE_SUFFIXES` excluait `.py` : renommer laissait `dbt.ref("old")`
    dans un modèle Python aval. Le renommage répondait succès, et c'est le
    build suivant qui butait."""
    assert (
        rcp.rename_ref_in_sql('df = dbt.ref("old")', "old", "new", language="python")
        == 'df = dbt.ref("new")'
    )
    # Et pas dans du SQL, où `dbt.ref()` n'est pas un lien dbt.
    assert rcp.rename_ref_in_sql('dbt.ref("old")', "old", "new") == 'dbt.ref("old")'


def test_a29_le_yaml_accepte_un_ref_nu():
    """dbt rend certains champs de propriétés comme des expressions Jinja sans
    délimiteurs : le `to:` d'un test de relation s'écrit `ref('autre')` tout
    court."""
    yml = "to: ref('old')\nfield: id\n"
    assert rcp.rename_ref_in_sql(yml, "old", "new", language="yaml") == (
        "to: ref('new')\nfield: id\n"
    )


def test_a31_les_configurations_du_fichier_racine_sont_signalees(settings):
    """`models: <projet>: <nom>:` n'est pas une référence, c'est une clé de
    configuration : la réécrire à l'aveugle déplacerait des réglages, la taire
    fait perdre au modèle renommé sa matérialisation ou ses tags."""
    from pliq import files

    (settings.project_dir / "dbt_project.yml").write_text(
        "name: demo\nprofile: demo\n"
        "models:\n  demo:\n    marts:\n      vieux:\n        +materialized: table\n"
    )
    settings._cache.clear()
    assert files.configs_naming(settings, "vieux") == ["models:demo:marts:vieux"]
    assert files.configs_naming(settings, "autre") == []


def test_a27_declarer_deux_fois_la_meme_table_n_ecrit_qu_une_entree(settings):
    """`existing` était construit avant la boucle et jamais enrichi : deux
    entrées identiques étaient écrites, annoncées toutes les deux ajoutées, et
    le parse dbt pouvait refuser la déclaration."""
    from pliq import datasets

    r = datasets.declare_source(
        settings,
        source_name="raw",
        schema="main",
        tables=[{"name": "commandes"}, {"name": "commandes"}],
    )
    assert r["added"] == ["commandes"]
    assert r["skipped"] == ["commandes"]
    content = yaml.safe_load((settings.project_dir / r["path"]).read_text())
    tables = content["sources"][0]["tables"]
    assert [t["name"] for t in tables] == ["commandes"]


def test_a28_un_tag_historique_de_premier_niveau_se_retire(settings):
    """La lecture acceptait `models[].tags`, l'écriture ne supprimait que
    `config.tags` : l'ancien tag restait et réapparaissait."""
    from pliq import files

    path = "models/schema.yml"
    (settings.project_dir / "models").mkdir(parents=True, exist_ok=True)
    (settings.project_dir / path).write_text(
        "version: 2\nmodels:\n  - name: m\n    tags: [vieux]\n"
    )
    assert files.read_doc(settings, path, "m")["tags"] == ["vieux"]

    files.upsert_doc(settings, path, "m", tags=["neuf"])
    entry = files.read_doc(settings, path, "m")
    assert entry["tags"] == ["neuf"]

    files.upsert_doc(settings, path, "m", tags=[])
    assert not files.read_doc(settings, path, "m").get("tags")
    assert "vieux" not in (settings.project_dir / path).read_text()


# --------------------------------------------------------------- inventaire


def test_a54_la_troncature_de_l_inventaire_est_annoncee(monkeypatch):
    """La limite était fixe et la réponse n'indiquait ni pagination ni
    troncature : l'interface présentait le nombre reçu comme le total."""

    class FakeRequest:
        def __init__(self, n):
            self.n = n

        def query(self, sql, limit):
            lines = [["db", "main", f"t{i}", "BASE TABLE", 1] for i in range(limit)]
            return {
                "columns": [
                    {"name": c}
                    for c in (
                        "table_catalog",
                        "table_schema",
                        "table_name",
                        "table_type",
                        "n_columns",
                    )
                ],
                "rows": lines,
            }

    fake = FakeRequest(0)
    data = warehouse.Warehouse.tables(fake)
    assert data["truncated"] is True
    assert len(data["tables"]) == warehouse.INVENTORY_LIMIT
    assert data["limit"] == warehouse.INVENTORY_LIMIT


def test_a10_la_regle_d_une_jointure_incrementale_est_ecrite(bench: Bench):
    """Seule l'entrée pilote est filtrée : une dimension dont le libellé change
    laisse `old` dans la cible tant que la ligne pilote ne repasse pas la
    borne. Le comportement convient à beaucoup de modèles — l'interface ne
    formulait simplement pas l'hypothèse."""
    bench.table("commandes", "id integer, client_id integer, ts integer", [(1, 7, 1)])
    bench.table("clients", "id integer, nom varchar", [(7, "Alice")])
    spec = {
        "name": "jointe",
        "type": "join",
        "inputs": [
            {"ref": "commandes", "alias": "commandes"},
            {"ref": "clients", "alias": "clients"},
        ],
        "joins": [{"type": "inner", "on": [{"left": "client_id", "right": "id"}]}],
        "select": [
            {"from": "commandes", "column": "id"},
            {"from": "commandes", "column": "ts"},
            {"from": "clients", "column": "nom"},
        ],
        "output": incremental_output(unique_key=["id"], incremental={"column": "ts"}),
    }
    sql = bench.compile(spec)
    assert "seul « commandes » est borné" in sql
    assert "ligne pilote" in sql
