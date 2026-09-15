"""Les projets connus : les lister, en ouvrir un, en créer, l'oublier."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException

from .. import projects
from .workshop import Workshop
from .bodies import (
    CreateProjectBody,
    ForgetProjectBody,
    OpenProjectBody,
    ScanBody,
)


def mount(app: FastAPI, a: Workshop) -> None:
    @app.get("/api/projects")
    def list_projects() -> dict:
        return {
            # La session porte `--profiles-dir` et `--target` : l'accueil doit
            # décrire les projets comme l'ouverture les lira, et non selon une
            # résolution qui lui serait propre.
            "projects": projects.catalog(
                extra_roots=[a.settings.project_dir.parent],
                explicit_profiles_dir=a.settings.explicit_profiles_dir,
                explicit_target=a.settings.explicit_target,
            ),
            "active": str(a.settings.project_dir),
            "home": str(Path.home()),
            "default_parent": str(a.settings.project_dir.parent),
        }

    @app.post("/api/projects/open")
    @a.serialized
    def open_project(body: OpenProjectBody) -> dict:
        return a.switch(Path(body.path).expanduser().resolve())

    @app.post("/api/projects/scan")
    @a.serialized
    def scan_projects(body: ScanBody) -> dict:
        """Cherche des projets sous un dossier, et les inscrit au registre.

        Sérialisée comme les autres routes qui touchent au registre : elle
        était la seule à faire des entrées/sorties non bornées *et* des
        écritures de registre sans aucun verrou. Le verrou de fichier de
        `projects` protège des autres processus ; celui-ci protège des autres
        onglets du même.
        """
        root = Path(body.root).expanduser()
        if not root.is_dir():
            raise HTTPException(400, f"Dossier introuvable : {root}")
        found = projects.scan(root)
        for f in found:
            projects.remember(f)
        return {
            "found": [
                projects.describe(
                    f,
                    a.settings.explicit_profiles_dir,
                    a.settings.explicit_target,
                ).as_dict()
                for f in found
            ]
        }

    @app.post("/api/projects/create")
    @a.serialized
    def create_project(body: CreateProjectBody) -> dict:
        # L'ouverture se prévalide *avant* de créer quoi que ce soit : la
        # route créait le dossier, l'inscrivait au registre, puis appelait
        # `switch`, qui refuse pendant un build. Tout l'appel répondait alors
        # en erreur alors que le projet existait déjà — et réessayer échouait
        # à son tour, le dossier n'étant plus vide.
        if body.open_it and a.svc.run.running:
            raise HTTPException(
                409,
                {
                    "code": "run_in_progress",
                    "error": (
                        "Une exécution dbt est en cours : l'atelier ne peut pas "
                        "changer de projet maintenant. Rien n'a été créé — "
                        "attendez la fin du build, ou créez le projet sans "
                        "l'ouvrir."
                    ),
                },
            )
        try:
            root = projects.create(Path(body.parent), body.name, adapter=body.adapter)
        except projects.ProjectError as exc:
            raise HTTPException(400, str(exc)) from None
        except OSError as exc:
            raise HTTPException(400, f"Création impossible : {exc}") from None
        out = {"created": str(root), "name": body.name, "opened": False}
        if body.open_it:
            try:
                out.update(a.switch(root))
                out["opened"] = True
            except HTTPException as exc:
                # Le projet, lui, est bien là : présenter l'opération entière
                # comme annulée ferait recommencer une création qui échouerait
                # alors sur un dossier non vide. On rend « créé, pas ouvert »,
                # et l'écran propose de l'ouvrir.
                out["open_error"] = (
                    exc.detail.get("error")
                    if isinstance(exc.detail, dict)
                    else str(exc.detail)
                )
        return out

    @app.post("/api/projects/forget")
    @a.serialized
    def forget_project(body: ForgetProjectBody) -> dict:
        """Retire un projet du registre, et sur demande l'efface du disque.

        Sérialisée avec les bascules de projet : sans ce verrou, `_switch`
        pouvait ouvrir le dossier entre la vérification ci-dessous et le
        `rmtree`, et l'atelier se retrouvait dans un projet effacé.
        """
        path = Path(body.path).expanduser().resolve()
        if body.delete_files:
            active = a.settings.project_dir.resolve()
            # L'égalité ne suffit pas : `rmtree` emporte tout l'arbre. Un projet
            # dbt imbriqué dans un autre — le cas d'un dépôt qui en contient
            # plusieurs — faisait disparaître le projet ouvert en répondant 200.
            if path == active:
                raise HTTPException(
                    400,
                    "C'est le projet ouvert : ouvrez-en un autre avant de le "
                    "supprimer.",
                )
            if path in active.parents:
                raise HTTPException(
                    400,
                    f"Le projet ouvert « {a.settings.project_name} » est dans ce "
                    f"dossier ({active}) : le supprimer l'emporterait avec lui. "
                    f"Ouvrez un autre projet avant.",
                )
            if active in path.parents:
                raise HTTPException(
                    400,
                    f"Ce dossier est dans le projet ouvert "
                    f"« {a.settings.project_name} » : le supprimer effacerait des "
                    f"fichiers de ce projet. Ouvrez un autre projet d'abord.",
                )
            try:
                projects.delete(path, body.confirm)
            except projects.ProjectError as exc:
                raise HTTPException(400, str(exc)) from None
            except OSError as exc:
                raise HTTPException(400, f"Suppression impossible : {exc}") from None
            return {"deleted": str(path)}
        projects.forget(path)
        return {"forgotten": str(path)}
