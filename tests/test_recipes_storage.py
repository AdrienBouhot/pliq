"""Le script visuel sur le disque : .pliq/recipes/<nom>.yml.

Deux invariants : il vit hors des `model-paths` (dbt ne doit jamais le lire), et
le perdre ne coûte que la décomposition visuelle.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pliq import recipes as rcp
from pliq.recipes import RecipeError

SPEC = {
    "name": "orders_prepared",
    "type": "prepare",
    "inputs": [{"ref": "stg_orders", "alias": "stg_orders"}],
    "output": {"layer": "marts", "materialized": "view"},
    "steps": [
        {
            "id": "a1",
            "type": "round",
            "enabled": True,
            "params": {"column": "amount_eur", "decimals": 0},
        }
    ],
}


def test_le_script_est_ecrit_hors_des_model_paths(project_dir: Path):
    rel = rcp.save_recipe(project_dir, SPEC)
    assert rel == ".pliq/recipes/orders_prepared.yml"
    assert (project_dir / rel).exists()
    assert "models" not in rel


def test_aller_retour_sans_perte(project_dir: Path):
    rcp.save_recipe(project_dir, SPEC)
    reread = rcp.load_recipe(project_dir, "orders_prepared")
    assert reread == SPEC


def test_la_doc_dbt_ne_se_range_pas_dans_le_script(project_dir: Path):
    """La description et les tests de colonnes vivent dans le YAML dbt.

    En garder une copie ici faisait deux vérités : réenregistrer une recipe
    sans toucher à ses tests réappliquait sa copie et annulait ce que la fiche
    dataset venait d'écrire dans le schema.yml.
    """
    rcp.save_recipe(
        project_dir,
        {
            **SPEC,
            "output": {
                **SPEC["output"],
                "description": "Commandes",
                "columns": [{"name": "amount_eur", "tests": [{"name": "not_null"}]}],
            },
        },
    )
    written = (project_dir / ".pliq/recipes/orders_prepared.yml").read_text()
    assert "Commandes" not in written
    assert "not_null" not in written
    # Le reste de la sortie, lui, est bien la vérité de la recipe.
    reread = rcp.load_recipe(project_dir, "orders_prepared")
    assert reread["output"] == {"layer": "marts", "materialized": "view"}


def test_le_script_relu_est_serialisable_en_json(project_dir: Path):
    """L'API renvoie le spec au navigateur : aucun objet ruamel ne doit survivre."""
    rcp.save_recipe(project_dir, SPEC)
    reread = rcp.load_recipe(project_dir, "orders_prepared")
    assert json.loads(json.dumps(reread)) == SPEC


def test_lister_les_scripts(project_dir: Path):
    rcp.save_recipe(project_dir, SPEC)
    rcp.save_recipe(project_dir, {**SPEC, "name": "autre"})
    assert sorted(rcp.list_recipes(project_dir)) == ["autre", "orders_prepared"]


def test_lister_sans_dossier_ne_plante_pas(project_dir: Path):
    assert rcp.list_recipes(project_dir) == {}


def test_supprimer(project_dir: Path):
    rcp.save_recipe(project_dir, SPEC)
    rcp.delete_recipe(project_dir, "orders_prepared")
    assert rcp.load_recipe(project_dir, "orders_prepared") is None
    rcp.delete_recipe(project_dir, "orders_prepared")  # idempotent


def test_un_nom_de_fichier_hostile_ne_sort_pas_du_dossier(project_dir: Path):
    with pytest.raises(RecipeError):
        rcp.save_recipe(project_dir, {**SPEC, "name": "../../evade"})
    assert rcp.load_recipe(project_dir, "../../evade") is None
    rcp.delete_recipe(project_dir, "../../evade")


def test_un_pliq_en_lien_symbolique_ne_fait_pas_ecrire_dehors(
    project_dir: Path, tmp_path: Path
):
    """Le nom est validé, le chemin réel ne l'était pas.

    Aucun nom de recipe ne peut sortir du dossier — pas de séparateur, pas de
    `..`. Mais un `.pliq` posé en lien symbolique fait sortir le chemin sans
    qu'aucun nom ne soit en cause : l'atelier écrivait alors hors du projet,
    sans erreur. `files.safe_path` confine déjà les SQL et les YAML dbt ainsi ;
    le stockage des recipes y échappait.
    """
    outside_of = tmp_path / "voisin"
    outside_of.mkdir()
    (project_dir / ".pliq").symlink_to(outside_of, target_is_directory=True)

    with pytest.raises(RecipeError, match="sort du projet"):
        rcp.save_recipe(project_dir, SPEC)
    assert list(outside_of.rglob("*.yml")) == [], "rien ne doit être écrit dehors"

    # Lecture et suppression se taisent plutôt que de suivre le lien.
    assert rcp.load_recipe(project_dir, "orders_prepared") is None
    rcp.delete_recipe(project_dir, "orders_prepared")
    assert rcp.list_recipes(project_dir) == {}


def test_un_seul_yml_en_lien_symbolique_ne_passe_pas_davantage(
    project_dir: Path, tmp_path: Path
):
    """Le dossier est bien dans le projet, mais pas le fichier qu'il désigne."""
    target = tmp_path / "ailleurs.yml"
    target.write_text("name: orders_prepared\ntype: sql\nsql: select 1\n")
    folder = project_dir / rcp.RECIPE_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "orders_prepared.yml").symlink_to(target)

    assert rcp.load_recipe(project_dir, "orders_prepared") is None
    with pytest.raises(RecipeError, match="sort du projet"):
        rcp.save_recipe(project_dir, SPEC)
    assert "select 1" in target.read_text(), "le fichier visé ne doit pas bouger"


def test_un_pliq_ordinaire_continue_de_marcher(project_dir: Path):
    """Le confinement ne doit rien coûter au cas normal."""
    rel = rcp.save_recipe(project_dir, SPEC)
    assert rel == ".pliq/recipes/orders_prepared.yml"
    assert rcp.load_recipe(project_dir, "orders_prepared") == SPEC


def test_un_script_illisible_est_ignore_plutot_que_fatal(project_dir: Path):
    f = project_dir / rcp.RECIPE_DIR / "casse.yml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("steps: [ {")
    assert rcp.load_recipe(project_dir, "casse") is None
    assert rcp.list_recipes(project_dir) == {}


# ------------------------------------------------------- suivre un renommage
#
# Renommer un modèle ne sert à rien si ce qui le lit continue de nommer
# l'ancien : le projet parse encore, et casse au `dbt build`.


@pytest.mark.parametrize(
    "before,after",
    [
        ("select * from {{ ref('a') }}", "select * from {{ ref('b') }}"),
        ('select * from {{ ref("a") }}', 'select * from {{ ref("b") }}'),
        ("{{ ref( 'a' ) }}", "{{ ref('b') }}"),
        ("{{ ref('a', v=2) }}", "{{ ref('b', v=2) }}"),
        # dbt accepte la version en nombre comme en chaîne.
        ("{{ ref('a', version='2') }}", "{{ ref('b', version='2') }}"),
        ("{{ ref('demo', 'a') }}", "{{ ref('demo', 'b') }}"),
    ],
)
def test_un_ref_suit_le_renommage_sous_toutes_ses_ecritures(before: str, after: str):
    assert rcp.rename_ref_in_sql(before, "a", "b", package="demo") == after


@pytest.mark.parametrize(
    "untouched",
    [
        # Un autre paquet a son propre `a` : ce n'est pas le nôtre.
        "{{ ref('dbt_utils', 'a') }}",
        # Un nom qui commence pareil n'est pas le même nom.
        "{{ ref('ab') }}",
        "{{ ref('a_bis') }}",
        # Une colonne qui s'appelle comme le modèle reste une colonne.
        "select a, count(*) from t group by a",
        # Un ref construit au vol : on ne devine pas ce qu'il vaudra, et le
        # laisser intact vaut mieux que de le réécrire de travers.
        "{{ ref(var('modele')) }}",
    ],
)
def test_ce_qui_n_est_pas_notre_ref_n_est_pas_touche(untouched: str):
    assert rcp.rename_ref_in_sql(untouched, "a", "b", package="demo") == untouched


def test_le_script_d_aval_suit_dans_sa_structure():
    spec = {
        "name": "aval",
        "type": "prepare",
        "inputs": [{"ref": "a", "alias": "a"}, {"source_name": "raw", "table": "a"}],
    }
    assert rcp.rename_ref_in_spec(spec, "a", "b", "demo") is True
    assert spec["inputs"][0]["ref"] == "b"
    # L'alias est le nom du CTE, déjà écrit dans le `.sql` qu'on ne recompile
    # pas : le changer ferait dire au script autre chose que ce que le SQL fait.
    assert spec["inputs"][0]["alias"] == "a"
    # Une source n'est pas un modèle : `source('raw', 'a')` ne bouge pas.
    assert spec["inputs"][1]["table"] == "a"


def test_une_recipe_sql_suit_dans_son_jinja():
    spec = {"name": "aval", "type": "sql", "sql": "select * from {{ ref('a') }}"}
    assert rcp.rename_ref_in_spec(spec, "a", "b", "demo") is True
    assert spec["sql"] == "select * from {{ ref('b') }}"


def test_l_homonyme_d_un_paquet_n_est_pas_renomme():
    """Renommer `a` du projet ne doit rien changer au `a` d'un paquet.

    Les deux portent le même nom et ne sont pas le même dataset : une entrée
    qui nomme son paquet désigne un modèle qui, lui, n'a pas bougé.
    """
    spec = {
        "name": "aval",
        "type": "prepare",
        "inputs": [
            {"ref": "a", "alias": "a", "package": "vendor"},
            {"ref": "a", "alias": "a_local"},
        ],
    }
    assert rcp.rename_ref_in_spec(spec, "a", "b", "demo") is True
    assert spec["inputs"][0]["ref"] == "a", "le modèle du paquet n'a pas changé de nom"
    assert spec["inputs"][0]["package"] == "vendor"
    assert spec["inputs"][1]["ref"] == "b"


def test_un_script_qui_ne_lit_pas_le_modele_n_est_pas_reecrit():
    spec = {"name": "ailleurs", "type": "prepare", "inputs": [{"ref": "z"}]}
    assert rcp.rename_ref_in_spec(spec, "a", "b", "demo") is False
    assert spec["inputs"][0]["ref"] == "z"


# ------------------------------------- repérer qui lit une colonne renommée
#
# Même histoire, un cran plus bas : renommer une colonne casse l'aval qui la
# lit, et l'atelier doit savoir le dire avant d'écrire. Il ne le *suit* plus —
# la réécriture textuelle renommait des colonnes d'une autre portée SQL — mais
# le repérage reste, et il doit rester large : une colonne se nomme dans une
# formule, un filtre, une clé de regroupement, et jusque dans une chaîne de
# caractères qu'aucune réécriture n'aurait su reprendre.


def test_un_nom_cache_dans_une_chaine_se_signale():
    """`{{ dbt_utils.star(from=ref('x'), except=['amount_eur']) }}` casse le
    build comme le reste, et l'atelier doit le dire."""
    jinja = "{{ dbt_utils.star(from=ref('x'), except=['amount_eur']) }}"
    assert rcp.sql_mentions_column(jinja, "amount_eur") is True


def test_le_script_d_aval_est_signale_sur_ses_formules():
    spec = {
        "name": "aval",
        "type": "prepare",
        "inputs": [{"ref": "amont", "alias": "amont"}],
        "steps": [
            {
                "type": "formula",
                "params": {"into": "ttc", "expression": "amount_eur * 1.2"},
            }
        ],
    }
    assert rcp.mentions_column(spec, "amount_eur") is True


def test_une_valeur_de_filtre_n_est_pas_une_lecture_de_colonne():
    """C'est ce qu'on compare, pas ce qu'on lit — et le nom du modèle non plus."""
    spec = {
        "name": "aval",
        "type": "prepare",
        "inputs": [{"ref": "amount_eur", "alias": "amount_eur"}],
        "steps": [
            {
                "type": "filter_value",
                "params": {
                    "column": "statut",
                    "operator": "eq",
                    "values": ["amount_eur"],
                },
            }
        ],
    }
    assert rcp.mentions_column(spec, "amount_eur") is False


def test_un_composant_de_date_n_est_pas_une_colonne():
    """`parts: [year, month]` nomme des pas de temps, pas des colonnes."""
    spec = {
        "name": "aval",
        "type": "prepare",
        "steps": [{"type": "date_parts", "params": {"column": "d", "parts": ["year"]}}],
    }
    assert rcp.mentions_column(spec, "year") is False


def test_un_script_qui_ne_lit_pas_la_colonne_n_est_pas_signale():
    spec = {"name": "ailleurs", "type": "prepare", "steps": [{"type": "distinct"}]}
    assert rcp.mentions_column(spec, "amount_eur") is False


# ---------------------------------------- reconnaître un renommage de colonne

COLS = {
    "src": [{"name": "id", "type": "int"}, {"name": "amount_eur", "type": "double"}]
}
BASE = {
    "name": "m",
    "type": "prepare",
    "inputs": [{"ref": "src", "alias": "src"}],
    "steps": [],
}


def _renamed(*pairs: tuple[str, str]) -> dict:
    renames = [{"from": a, "to": b} for a, b in pairs]
    return {
        **BASE,
        "steps": [
            {
                "id": "r",
                "enabled": True,
                "type": "rename",
                "params": {"renames": renames},
            }
        ],
    }


def test_une_colonne_renommee_est_reconnue_comme_telle():
    """Une colonne renommée est la même colonne, pas une disparue et une neuve."""
    assert rcp.renamed_columns(BASE, _renamed(("amount_eur", "montant")), COLS) == {
        "amount_eur": "montant"
    }


def test_un_renommage_de_renommage_remonte_au_nom_enregistre():
    """L'aval connaît `montant` : c'est de celui-là qu'il faut lui parler."""
    before = _renamed(("amount_eur", "montant"))
    after = _renamed(("amount_eur", "total"))
    assert rcp.renamed_columns(before, after, COLS) == {"montant": "total"}


def test_une_colonne_supprimee_n_est_pas_un_renommage():
    deleted = {
        **BASE,
        "steps": [
            {
                "id": "k",
                "enabled": True,
                "type": "keep_delete",
                "params": {"action": "delete", "columns": ["amount_eur"]},
            }
        ],
    }
    assert rcp.renamed_columns(BASE, deleted, COLS) == {}
