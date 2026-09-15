"""Découverte du projet dbt, lecture de profiles.yml, et adresse d'écoute."""

from __future__ import annotations

from pathlib import Path

import pytest

from pliq.__main__ import _check_listening, trusted_host
from pliq.config import Settings, discover_project, load_settings

from .conftest import DBT_PROJECT_YML, PROFILES_YML, write


def test_discover_dans_le_dossier_courant(project_dir: Path):
    assert discover_project(project_dir) == project_dir.resolve()


def test_discover_dans_un_sous_dossier(project_dir: Path):
    assert discover_project(project_dir.parent) == project_dir.resolve()


def test_discover_ignore_les_dossiers_caches(tmp_path: Path):
    write(tmp_path / ".cache" / "dbt_project.yml", DBT_PROJECT_YML)
    assert discover_project(tmp_path) is None


def test_load_settings_refuse_un_dossier_sans_projet(tmp_path: Path):
    with pytest.raises(SystemExit) as exc:
        load_settings(project_dir=str(tmp_path))
    assert "dbt_project.yml" in str(exc.value)


def test_load_settings_suit_DBT_PROJECT_DIR(project_dir: Path, monkeypatch):
    monkeypatch.setenv("DBT_PROJECT_DIR", str(project_dir))
    assert load_settings().project_dir == project_dir.resolve()


def test_profiles_dans_le_projet_priment_sur_le_home(settings: Settings):
    assert settings.profiles_dir == settings.project_dir
    assert settings.profile_name == "demo"
    assert settings.targets() == ["dev", "prod"]


def test_profiles_yml_absent_du_projet_renvoie_au_home(tmp_path: Path):
    root = tmp_path / "sans_profils"
    write(root / "dbt_project.yml", DBT_PROJECT_YML)
    s = load_settings(project_dir=str(root))
    assert s.profiles_dir == Path.home() / ".dbt"


def test_cible_active_par_defaut_et_forcee(settings: Settings, project_dir: Path):
    assert settings.active_target() == "dev"
    forced = load_settings(project_dir=str(project_dir), target="prod")
    assert forced.active_target() == "prod"
    assert forced.target_config()["path"] == "prod.duckdb"


def test_un_profiles_dir_explicite_est_retenu_comme_tel(
    project_dir: Path, tmp_path: Path
):
    """La bascule de projet doit savoir ce que le *lancement* a demandé.

    Sans cette distinction, rouvrir un projet reprenait le `profiles.yml` que
    le nouveau dossier porte à sa racine, et perdait le `--profiles-dir` donné
    au démarrage — le projet devenait alors impossible à rouvrir.
    """
    elsewhere = tmp_path / "profils"
    write(elsewhere / "profiles.yml", PROFILES_YML)

    explicit = load_settings(project_dir=str(project_dir), profiles_dir=str(elsewhere))
    assert explicit.profiles_dir == elsewhere.resolve()
    assert explicit.explicit_profiles_dir == str(elsewhere)

    # Déduit du projet : rien à reconduire ailleurs.
    inferred = load_settings(project_dir=str(project_dir))
    assert inferred.profiles_dir == project_dir.resolve()
    assert inferred.explicit_profiles_dir is None


def test_database_path_est_resolu_depuis_le_projet(settings: Settings):
    assert settings.database_path() == settings.project_dir / "dev.duckdb"


def test_database_path_absolu_reste_absolu(project_dir: Path, tmp_path: Path):
    absolute = tmp_path / "ailleurs.duckdb"
    write(
        project_dir / "profiles.yml",
        PROFILES_YML.replace("path: dev.duckdb", f"path: {absolute}"),
    )
    assert load_settings(project_dir=str(project_dir)).database_path() == absolute


def test_database_path_vide_pour_memory_et_autres_adapters(project_dir: Path):
    write(
        project_dir / "profiles.yml",
        PROFILES_YML.replace("path: dev.duckdb", "path: ':memory:'"),
    )
    assert load_settings(project_dir=str(project_dir)).database_path() is None

    write(
        project_dir / "profiles.yml",
        """\
demo:
  target: dev
  outputs:
    dev:
      type: postgres
      host: localhost
""",
    )
    s = load_settings(project_dir=str(project_dir))
    assert s.database_path() is None
    assert s.target_config()["type"] == "postgres"


def test_chemins_derives_du_projet(settings: Settings):
    assert settings.model_paths() == [settings.project_dir / "models"]
    assert settings.target_dir == settings.project_dir / "target"
    assert settings.manifest_path.name == "manifest.json"


def test_projet_illisible_ne_fait_pas_planter_les_proprietes(tmp_path: Path):
    """Un profil manquant doit donner des valeurs vides, pas une exception."""
    s = Settings(project_dir=tmp_path, profiles_dir=tmp_path)
    assert s.project_yml == {}
    assert s.profile() == {}
    assert s.targets() == []
    assert s.active_target() == "dev"


# -------------------------------------------- une cible écrite en Jinja


JINJA_PROFILES_YML = """\
demo:
  target: "{{ env_var('AUDIT_DBT_TARGET', 'dev') }}"
  outputs:
    dev:
      type: duckdb
      path: dev.duckdb
      threads: 1
    prod:
      type: duckdb
      path: prod.duckdb
      threads: 1
"""


def test_une_cible_ecrite_en_jinja_est_resolue_comme_dbt_la_resout(project_dir: Path):
    """dbt rend le `profiles.yml` avant de le lire ; l'atelier lisait le brut.

    La cible valait alors « {{ env_var(...) }} », ne correspondait à aucune
    sortie déclarée, et `target_config()` sortait vide : l'atelier ignorait
    l'adaptateur et jugeait les formules à l'aune d'un SQL générique, pour un
    projet que `dbt parse` acceptait pourtant.
    """
    write(project_dir / "profiles.yml", JINJA_PROFILES_YML)
    s = load_settings(project_dir=str(project_dir))
    assert s.active_target() == "dev"
    assert s.target_config()["type"] == "duckdb"
    assert s.database_path() == project_dir.resolve() / "dev.duckdb"


def test_la_cible_en_jinja_suit_l_environnement(project_dir: Path, monkeypatch):
    monkeypatch.setenv("AUDIT_DBT_TARGET", "prod")
    write(project_dir / "profiles.yml", JINJA_PROFILES_YML)
    s = load_settings(project_dir=str(project_dir))
    assert s.active_target() == "prod"
    assert s.target_config()["path"] == "prod.duckdb"


def test_une_cible_forcee_prime_sur_l_expression(project_dir: Path, monkeypatch):
    """`--target` reste le dernier mot : c'est déjà ce que fait dbt."""
    monkeypatch.setenv("AUDIT_DBT_TARGET", "prod")
    write(project_dir / "profiles.yml", JINJA_PROFILES_YML)
    s = load_settings(project_dir=str(project_dir), target="dev")
    assert s.active_target() == "dev"
    assert s.target_config()["path"] == "dev.duckdb"


def test_un_jinja_irreparable_n_empeche_pas_de_lire_le_reste(project_dir: Path):
    """Le rendu peut échouer : mieux vaut le YAML brut que plus rien du tout."""
    write(
        project_dir / "profiles.yml",
        'demo:\n  target: "{{ pas_une_fonction() }}"\n'
        "  outputs:\n    dev:\n      type: duckdb\n      path: dev.duckdb\n",
    )
    s = load_settings(project_dir=str(project_dir))
    assert s.targets() == ["dev"]


# ----------------------------------------------------------- model-paths


def test_model_dirs_garde_un_chemin_imbrique(tmp_path: Path):
    """`transform/models` doit rester entier : dbt ne lit que ses model-paths.

    Ne garder que le dernier dossier fait écrire les recipes dans `models/`,
    où dbt ne va jamais regarder — l'atelier annonce alors une sauvegarde
    réussie sur un fichier mort.
    """
    root = tmp_path / "demo"
    write(
        root / "dbt_project.yml",
        DBT_PROJECT_YML.replace(
            'model-paths: ["models"]', 'model-paths: ["transform/models"]'
        ),
    )
    write(root / "profiles.yml", PROFILES_YML)
    s = load_settings(project_dir=str(root))
    assert s.model_dirs() == ["transform/models"]
    assert s.model_paths() == [root.resolve() / "transform" / "models"]


def test_model_dirs_par_defaut_et_nettoyage(tmp_path: Path):
    root = tmp_path / "demo"
    write(
        root / "dbt_project.yml",
        DBT_PROJECT_YML.replace(
            'model-paths: ["models"]', 'model-paths: ["/transform/models/", ".", ""]'
        ),
    )
    write(root / "profiles.yml", PROFILES_YML)
    assert load_settings(project_dir=str(root)).model_dirs() == ["transform/models"]

    write(
        root / "dbt_project.yml",
        DBT_PROJECT_YML.replace('model-paths: ["models"]\n', ""),
    )
    assert load_settings(project_dir=str(root)).model_dirs() == ["models"]


# ------------------------------------------------------------ dépendances


def test_un_projet_sans_paquets_n_a_rien_a_installer(settings):
    assert settings.packages_needed() is False


def test_des_paquets_declares_mais_absents_sont_signales(settings, project_dir):
    """Le mur d'entrée d'un projet existant : sans `dbt deps`, rien ne parse."""
    (project_dir / "packages.yml").write_text(
        "packages:\n  - package: dbt-labs/dbt_utils\n    version: 1.1.1\n"
    )
    assert settings.packages_needed() is True

    (project_dir / "dbt_packages" / "dbt_utils").mkdir(parents=True)
    assert settings.packages_needed() is False


def test_le_dossier_d_installation_du_projet_est_respecte(settings, project_dir):
    (project_dir / "dependencies.yml").write_text("packages: []\n")
    (project_dir / "dbt_project.yml").write_text(
        (project_dir / "dbt_project.yml").read_text()
        + '\npackages-install-path: "vendor"\n'
    )
    assert settings.packages_needed() is True
    (project_dir / "vendor" / "x").mkdir(parents=True)
    assert settings.packages_needed() is False


# ------------------------------- la cible que dbt utilisera vraiment (C1)


def test_la_cible_de_l_environnement_est_celle_qu_on_affiche(
    project_dir: Path, monkeypatch
):
    """`DBT_TARGET` pilotait dbt, et l'atelier n'en savait rien.

    dbt lie son option `--target` à cette variable : avec un profil réglé sur
    `dev`, l'atelier annonçait `dev`, choisissait le dialecte de `dev`, et
    `dbt build` écrivait sur `prod`. Reproduit en son temps sur un projet
    temporaire : seul `prod.duckdb` était créé.
    """
    monkeypatch.setenv("DBT_TARGET", "prod")
    s = load_settings(project_dir=str(project_dir))
    assert s.active_target() == "prod"
    assert s.target_config()["path"] == "prod.duckdb"
    assert s.command_target() == "prod", "et la commande doit dire la même chose"


def test_l_autre_nom_de_la_variable_compte_autant(project_dir: Path, monkeypatch):
    """dbt lit `DBT_ENGINE_TARGET` avant `DBT_TARGET` : n'en couvrir qu'une
    laissait l'écart se rouvrir par l'autre porte."""
    monkeypatch.setenv("DBT_ENGINE_TARGET", "prod")
    assert load_settings(project_dir=str(project_dir)).active_target() == "prod"

    monkeypatch.setenv("DBT_TARGET", "dev")
    assert load_settings(project_dir=str(project_dir)).active_target() == "prod"


def test_la_cible_forcee_prime_sur_l_environnement(project_dir: Path, monkeypatch):
    """L'ordre est celui de dbt : `--target`, puis l'environnement, puis le profil."""
    monkeypatch.setenv("DBT_TARGET", "prod")
    s = load_settings(project_dir=str(project_dir), target="dev")
    assert s.active_target() == "dev"


def test_la_cible_n_est_epinglee_que_si_le_profil_la_declare(
    project_dir: Path, monkeypatch
):
    """Un profil que l'atelier ne sait pas lire reste l'affaire de dbt.

    Imposer un repli d'affichage sur la ligne de commande casserait une
    configuration que dbt, lui, résout très bien.
    """
    monkeypatch.setenv("DBT_TARGET", "recette")
    s = load_settings(project_dir=str(project_dir))
    assert s.active_target() == "recette"
    assert s.command_target() is None


def test_un_profil_sans_cible_prend_le_mot_de_dbt(project_dir: Path):
    """dbt replie sur « default », pas sur « dev » : une cible que le profil ne
    déclare pas doit se voir, et pas se confondre avec une cible qui existe."""
    write(
        project_dir / "profiles.yml",
        "demo:\n  outputs:\n    prod:\n      type: duckdb\n      path: prod.duckdb\n",
    )
    s = load_settings(project_dir=str(project_dir))
    assert s.active_target() == "default"
    assert s.command_target() is None


# ------------------------------------------------------------ écoute réseau


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "localhost", "LOCALHOST", "::1", "[::1]", "127.0.0.53"],
)
def test_la_boucle_locale_est_de_confiance(host: str):
    assert trusted_host(host)


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "::", "192.168.1.10", "10.0.0.5", "mon-mac.local", "example.com"],
)
def test_tout_le_reste_expose_l_atelier(host: str):
    assert not trusted_host(host)


def test_une_ecoute_hors_de_la_machine_est_refusee_sans_le_dire():
    """Toutes les parades de l'atelier visent une page web, pas le réseau.

    Le contrôle du nom d'hôte, celui de l'origine sur la socket, le refus d'un
    corps JSON sans `Content-Type` : chacune protège d'une page qui parle à un
    atelier *local* depuis le navigateur de qui l'a ouvert. Aucune n'oppose
    quoi que ce soit à quelqu'un qui atteint le port directement — et il n'y a
    rien derrière. `--host 0.0.0.0` passait sans un mot.
    """
    with pytest.raises(SystemExit) as refusal:
        _check_listening("0.0.0.0", expose=False)
    message = str(refusal.value)
    assert "0.0.0.0" in message
    assert "--exposer" in message
    # Le refus doit dire *ce qui* est exposé, sinon on ajoute le drapeau sans
    # savoir ce qu'on accepte.
    assert "SQL" in message and "authentification" in message


def test_l_exposition_assumee_passe():
    """Une machine de développement derrière un réseau maîtrisé : c'est un
    choix légitime, il doit juste être explicite."""
    _check_listening("0.0.0.0", expose=True)
    _check_listening("127.0.0.1", expose=False)
