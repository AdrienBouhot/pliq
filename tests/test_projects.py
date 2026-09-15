"""Le registre des projets, la fiche d'accueil, la création et la suppression."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pliq import projects
from pliq.projects import ProjectError

from .conftest import DBT_PROJECT_YML, write

# ------------------------------------------------------------------ création


def test_un_projet_cree_est_immediatement_constructible(tmp_path: Path):
    root = projects.create(tmp_path, "mon_projet")

    assert projects.is_project(root)
    for expected in (
        "dbt_project.yml",
        "profiles.yml",
        ".gitignore",
        "README.md",
        "models/sources.yml",
    ):
        assert (root / expected).exists(), expected
    for folder in (
        "models/staging",
        "models/intermediate",
        "models/marts",
        "seeds",
        "macros",
        "tests",
        "snapshots",
        "analyses",
    ):
        assert (root / folder).is_dir(), folder

    proj = yaml.safe_load((root / "dbt_project.yml").read_text())
    assert proj["name"] == "mon_projet" and proj["profile"] == "mon_projet"
    profiles_data = yaml.safe_load((root / "profiles.yml").read_text())["mon_projet"]
    assert profiles_data["target"] == "dev"
    assert set(profiles_data["outputs"]) == {"dev", "prod"}
    assert profiles_data["outputs"]["dev"]["type"] == "duckdb"


def test_le_projet_cree_entre_au_registre(tmp_path: Path):
    root = projects.create(tmp_path, "mon_projet")
    assert str(root) in [p["path"] for p in projects.catalog()]


@pytest.mark.parametrize(
    "name", ["", "Mon Projet", "mon-projet", "2projets", "projet été"]
)
def test_nom_de_projet_refuse(tmp_path: Path, name: str):
    with pytest.raises(ProjectError, match="minuscules"):
        projects.create(tmp_path, name)


def test_seul_duckdb_est_gere_a_la_creation(tmp_path: Path):
    with pytest.raises(ProjectError, match="dbt init"):
        projects.create(tmp_path, "p", adapter="snowflake")


def test_dossier_parent_inexistant(tmp_path: Path):
    with pytest.raises(ProjectError, match="n'existe pas"):
        projects.create(tmp_path / "absent", "p")


def test_dossier_deja_occupe(tmp_path: Path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "quelque_chose.txt").write_text("x")
    with pytest.raises(ProjectError, match="pas vide"):
        projects.create(tmp_path, "p")


def test_un_dossier_vide_est_reutilisable(tmp_path: Path):
    (tmp_path / "p").mkdir()
    assert projects.create(tmp_path, "p").name == "p"


# ------------------------------------------------------------------ registre


def test_retenir_oublier_et_dernier_ouvert(tmp_path: Path):
    root = projects.create(tmp_path, "p")
    projects.remember(root, active=True)
    projects.remember(root)  # sans doublon
    assert projects.last_opened() == root
    assert [p["path"] for p in projects.catalog()].count(str(root)) == 1

    projects.forget(root)
    assert projects.last_opened() is None
    assert str(root) not in [p["path"] for p in projects.catalog()]


def test_le_dernier_ouvert_disparu_du_disque_ne_ressort_pas(tmp_path: Path):
    root = projects.create(tmp_path, "p")
    projects.remember(root, active=True)
    (root / "dbt_project.yml").unlink()
    assert projects.last_opened() is None


def test_un_registre_corrompu_ne_bloque_pas_le_demarrage(tmp_path: Path):
    projects.REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    projects.REGISTRY.write_text("{ ceci n'est pas du json")
    assert projects.catalog() == []
    projects.remember(projects.create(tmp_path, "p"))
    assert len(projects.catalog()) == 1


# --------------------------------------------------------------------- scan


def test_la_fouille_trouve_les_projets_et_evite_le_bruit(tmp_path: Path):
    (tmp_path / "espace").mkdir()
    projects.create(tmp_path / "espace", "a")
    write(
        tmp_path / "espace" / "sous" / "dossier" / "b" / "dbt_project.yml",
        DBT_PROJECT_YML,
    )
    write(tmp_path / "espace" / ".cache" / "c" / "dbt_project.yml", DBT_PROJECT_YML)
    write(tmp_path / "espace" / "target" / "d" / "dbt_project.yml", DBT_PROJECT_YML)

    found = {p.name for p in projects.scan(tmp_path / "espace")}
    assert found == {"a", "b"}


def test_un_projet_n_en_contient_pas_un_autre(tmp_path: Path):
    root = projects.create(tmp_path, "a")
    write(root / "sous_projet" / "dbt_project.yml", DBT_PROJECT_YML)
    assert projects.scan(tmp_path) == [root]


def test_la_fouille_s_arrete_a_la_profondeur_demandee(tmp_path: Path):
    write(tmp_path / "un" / "deux" / "trois" / "dbt_project.yml", DBT_PROJECT_YML)
    assert projects.scan(tmp_path, depth=1) == []
    assert len(projects.scan(tmp_path, depth=3)) == 1


# -------------------------------------------------------------------- fiche


def test_la_fiche_d_un_projet(tmp_path: Path):
    root = projects.create(tmp_path, "p")
    write(root / "models" / "staging" / "stg_a.sql", "select 1")
    write(root / "seeds" / "raw_a.csv", "a\n1\n")

    node_card = projects.describe(root).as_dict()
    assert node_card["name"] == "p" and node_card["profile"] == "p"
    assert node_card["adapter"] == "duckdb" and node_card["target"] == "dev"
    assert node_card["targets"] == ["dev", "prod"]
    assert node_card["models"] == 1 and node_card["seeds"] == 1
    assert node_card["database"].endswith("dev.duckdb")
    assert node_card["database_size"] is None  # jamais construit
    assert node_card["last_run"] is None
    assert node_card["error"] is None


def test_la_fiche_d_un_dossier_qui_n_est_pas_un_projet(tmp_path: Path):
    node_card = projects.describe(tmp_path).as_dict()
    assert node_card["error"] == "dbt_project.yml introuvable"


def test_la_fiche_signale_un_profil_manquant(tmp_path: Path):
    root = tmp_path / "sans_profil"
    write(root / "dbt_project.yml", DBT_PROJECT_YML)
    assert "aucun profil" in projects.describe(root).error


def test_la_fiche_signale_un_dbt_project_illisible(tmp_path: Path):
    root = tmp_path / "casse"
    write(root / "dbt_project.yml", "name: [ {")
    assert "illisible" in projects.describe(root).error


def test_l_accueil_montre_les_projets_les_plus_recents_d_abord(tmp_path: Path):
    stale = projects.create(tmp_path, "vieux")
    recent = projects.create(tmp_path, "recent")
    write(stale / "target" / "run_results.json", "{}")
    write(recent / "target" / "run_results.json", "{}")
    import os
    import time

    os.utime(stale / "target" / "run_results.json", (time.time() - 3600,) * 2)

    names = [p["name"] for p in projects.catalog()]
    assert names.index("recent") < names.index("vieux")


# --------------------------------------------------------------- suppression


def test_supprimer_exige_le_nom_du_dossier(tmp_path: Path):
    root = projects.create(tmp_path, "p")
    with pytest.raises(ProjectError, match="Confirmation attendue"):
        projects.delete(root, "")
    with pytest.raises(ProjectError, match="Confirmation attendue"):
        projects.delete(root, "P")
    assert root.exists()

    projects.delete(root, "p")
    assert not root.exists()
    assert str(root) not in [p["path"] for p in projects.catalog()]


def test_un_effacement_qui_echoue_laisse_le_projet_dans_le_registre(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Le registre ne doit pas devancer le disque.

    `forget()` passait avant `shutil.rmtree()` : un effacement refusé — dossier
    verrouillé, montage réseau, droits — rendait bien « Suppression impossible »,
    mais le projet avait déjà quitté le registre. Il restait sur le disque, et
    l'accueil ne le montrait plus qu'à condition d'être voisin immédiat du
    projet ouvert. Aucun recours simple pour le retrouver.
    """
    root = projects.create(tmp_path, "recalcitrant")

    def refuse(*args, **kwargs):
        raise PermissionError("dossier verrouillé")

    monkeypatch.setattr(projects.shutil, "rmtree", refuse)
    with pytest.raises(PermissionError):
        projects.delete(root, "recalcitrant")

    assert root.exists(), "le dossier est toujours là"
    assert str(root) in [
        p["path"] for p in projects.catalog()
    ], "et le registre le sait encore"


def test_supprimer_refuse_ce_qui_n_est_pas_un_projet(tmp_path: Path):
    with pytest.raises(ProjectError, match="n'est pas un projet"):
        projects.delete(tmp_path, tmp_path.name)
    assert tmp_path.exists()


def test_humanize():
    import time

    assert projects.humanize(None) == ""
    assert projects.humanize(time.time()) == "à l'instant"
    assert projects.humanize(time.time() - 600) == "il y a 10 min"
    assert projects.humanize(time.time() - 7200) == "il y a 2 h"
    assert projects.humanize(time.time() - 3 * 86400) == "il y a 3 j"
