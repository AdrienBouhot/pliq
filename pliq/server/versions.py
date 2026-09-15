"""Refuser d'écraser une version qu'on n'a pas lue.

`project_lock` sérialise les écritures de l'atelier ; il ne voit rien de
ce qui s'écrit en dehors — un autre onglet, un autre navigateur, un
éditeur de texte. L'écran rend donc l'empreinte reçue à la lecture, et
une empreinte qui ne correspond plus désigne une version écrite
entre-temps.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from .. import files
from .. import recipes as rcp
from .workshop import Workshop
from .nodes import _name_taken
from .bodies import SaveRecipeBody


def reject_if_stale(
    a: Workshop, what: str, body: BaseModel, current: str | None
) -> None:
    """Refuse d'écraser une version que l'écran n'a pas lue.

    Le principe que la sauvegarde des recipes applique déjà, étendu aux
    formulaires qui réécrivent un fichier ou une fiche entière : l'écran
    rend l'empreinte reçue à la lecture, et une empreinte qui ne correspond
    plus désigne une version écrite entre-temps — autre onglet, autre
    navigateur, éditeur de texte. `a.project_lock` sérialise les écritures de
    l'atelier ; il ne voit rien de ce qui s'écrit en dehors.

    C'est le champ *fourni ou non* qui décide, et pas sa valeur : `null` est
    une empreinte à part entière, celle de ce qui n'existait pas encore
    quand l'écran a lu. La confondre avec un champ absent laissait deux
    écrans créer la même fiche, le second effaçant le premier — le cas
    exact d'une documentation qu'on remplit à deux.

    Un client qui n'envoie rien garde l'ancien comportement : la garantie
    est en plus, elle n'est pas un passage obligé.
    """
    if "base" not in body.model_fields_set:
        return
    expected = getattr(body, "base", None)
    if expected == current:
        return
    if expected is None:
        raise HTTPException(
            409,
            f"« {what} » a été créé depuis que vous avez ouvert cet écran. "
            f"Rechargez la page pour repartir de ce qui est sur le disque : "
            f"enregistrer maintenant remplacerait ce travail.",
        )
    if current is None:
        raise HTTPException(
            409,
            f"« {what} » a disparu depuis que vous l'avez ouvert. Rechargez "
            f"la page : enregistrer maintenant le recréerait à l'aveugle.",
        )
    raise HTTPException(
        409,
        f"« {what} » a changé depuis que vous l'avez ouvert. Rouvrez-le pour "
        f"repartir de la version sur le disque : enregistrer maintenant "
        f"effacerait cette modification.",
    )


def file_fingerprint(a: Workshop, rel_path: str) -> str | None:
    """Empreinte d'un fichier du projet, ou None s'il est illisible."""
    try:
        return files.digest(files.safe_path(a.settings, rel_path))
    except files.FileError:
        return None


def _sql_diverged(a: Workshop, name: str, file: Path, cols: dict) -> str:
    """Le fichier sur disque ne dit plus ce que le script visuel produit.

    Les empreintes de `base` protègent d'une écriture *concurrente* : elles
    comparent le disque à ce que l'éditeur a lu à l'ouverture. Elles ne
    disent rien d'une modification faite *avant* cette ouverture — l'éditeur
    charge alors l'ancien script visuel mais prend l'empreinte du SQL déjà
    modifié, et l'enregistrement juge donc ce SQL remplaçable. Un `select 2`
    écrit à la main redevenait `select 1`, avec un 200 pour tout
    avertissement.

    La question à poser n'est donc pas « a-t-il bougé depuis que je l'ai
    lu ? » mais « dit-il encore ce que cette recipe écrit ? ». On compile la
    recipe *enregistrée* — celle dont ce fichier est censé sortir — et on
    compare. Les colonnes sont déjà lues pour la compilation qui suit : le
    contrôle ne coûte pas un aller-retour de plus à l'entrepôt.

    Trois réponses, et non deux : « identique », « diverge », et
    « indéterminé » — la recipe enregistrée ne compile plus, ou le fichier ne
    se lit pas. Confondre le troisième cas avec le premier ouvrait la
    protection au moment précis où elle sert : un schéma qui évolue casse la
    compilation de l'ancien script, l'utilisateur corrige le visuel, et il
    enregistre par-dessus un SQL modifié à la main sans que la confirmation
    prévue pour cette divergence lui soit jamais posée.
    """
    previous = rcp.load_recipe(a.settings.project_dir, name)
    if previous is None:
        return ""  # aucun script visuel enregistré : rien à confronter
    try:
        expected = rcp.compile_recipe(previous, cols)
    except rcp.RecipeError as exc:
        return f"indetermine:{exc}"
    except OSError as exc:
        return f"indetermine:{exc}"
    try:
        current = file.read_text()
    except OSError as exc:
        return f"indetermine:{exc}"
    return "diverge" if current.strip() != expected.strip() else ""


def _check_save_allowed(
    a: Workshop,
    body: SaveRecipeBody,
    name: str,
    path: str,
    model_file: Path,
    schema_path: str,
    cols: dict,
    old_file: Path | None = None,
) -> None:
    """Refuse d'écraser un fichier que l'éditeur n'a pas lu.

    Deux cas distincts, que la route confondait. Une *création* ne doit
    écraser aucun modèle existant : nommer une recipe comme un modèle déjà
    écrit à la main remplaçait sa logique métier, et répondait 200. Une
    *modification* ne doit écraser que la version qu'elle a lue : sinon la
    sauvegarde efface en silence ce qu'un autre écran, un autre navigateur
    ou un éditeur de texte a écrit entre-temps.

    La comparaison porte sur l'empreinte du contenu, et pas sur la simple
    existence : rouvrir une recipe sans la modifier doit pouvoir être
    réenregistré.

    Quand la sauvegarde *déplace* le modèle, c'est le fichier qui va
    disparaître qu'il faut comparer : le nouvel emplacement est vide, donc
    toujours d'accord avec n'importe quoi. Encore faut-il qu'il le soit —
    sinon le déplacement écraserait ce qui est déjà là.
    """
    current = files.digest(old_file if old_file is not None else model_file)

    if old_file is not None and model_file.exists():
        raise HTTPException(
            409,
            f"« {path} » existe déjà : déplacer « {name} » vers cette "
            f"couche remplacerait ce modèle. Ouvrez-le pour voir ce qu'il "
            f"contient, ou choisissez une autre couche.",
        )

    if body.is_new:
        if current is not None:
            raise HTTPException(
                409,
                f"« {path} » existe déjà. Choisissez un autre nom pour cette "
                f"recipe, ou ouvrez le modèle existant pour le modifier.",
            )
        # La place est libre à *cet* endroit, mais un nom de modèle est
        # unique dans tout le projet, pas dans son dossier : un
        # `models/staging/ventes.sql` écrit à la main et une nouvelle
        # recipe `ventes` en couche `marts` donnaient deux fichiers, un 200,
        # et un projet qui ne parsait plus (DuplicateResourceNameError).
        # C'est le contrôle que le renommage applique déjà.
        held = _name_taken(a, name)
        if held:
            raise HTTPException(409, held)
        return

    base = body.base or {}
    if "sql" in base and base.get("sql") != current:
        what = str(old_file.name) if old_file is not None else path
        # Disparu, et non modifié : un éditeur resté ouvert annulerait
        # silencieusement une suppression. C'est ce que `reject_if_stale`
        # dit déjà pour les fiches et le SQL écrit à la main.
        if current is None:
            raise HTTPException(
                409,
                f"« {what} » a été supprimé depuis que vous avez ouvert "
                f"cette recipe. Rouvrez l'atelier pour repartir de ce qui "
                f"est sur le disque : enregistrer maintenant recréerait un "
                f"modèle que quelqu'un vient d'effacer.",
            )
        raise HTTPException(
            409,
            f"« {what} » a changé depuis que vous l'avez ouvert. "
            f"Rouvrez la recipe pour repartir de la version sur le disque : "
            f"enregistrer maintenant effacerait cette modification.",
        )

    # La doc ne se compare que si elle est toujours au même endroit : un
    # `patch_path` qui a bougé désigne un autre fichier, pas un conflit.
    if "doc" in base and base.get("schema_path") == schema_path:
        if base.get("doc") != files.doc_digest(a.settings, schema_path, name):
            raise HTTPException(
                409,
                f"La documentation de « {name} » a changé dans "
                f"{schema_path} depuis que vous l'avez ouverte. Rouvrez la "
                f"recipe pour repartir de la version sur le disque.",
            )

    # Et le script visuel lui-même. Deux versions d'une recipe peuvent
    # rendre exactement le même SQL — désactiver une étape en est le cas le
    # plus simple. Le `.sql` et le `schema.yml` étaient alors d'accord tous
    # les deux, et un onglet resté ouvert réécrivait la recipe par-dessus
    # sans que rien ne le signale : la modification disparaissait.
    if "recipe" in base:
        current_recipe = _recipe_digest(a, name)
        if base.get("recipe") != current_recipe:
            if current_recipe is None:
                raise HTTPException(
                    409,
                    f"Le script visuel de « {name} » a été supprimé depuis "
                    f"que vous l'avez ouvert. Rouvrez l'atelier pour "
                    f"repartir de ce qui est sur le disque.",
                )
            raise HTTPException(
                409,
                f"Le script visuel de « {name} » a changé depuis que vous "
                f"l'avez ouvert — même quand le SQL produit, lui, n'a pas "
                f"bougé. Rouvrez la recipe pour repartir de la version sur "
                f"le disque : enregistrer maintenant effacerait cette "
                f"modification.",
            )

    # Et le fichier lui-même, qu'aucune des trois empreintes ne couvre : il
    # peut avoir été réécrit à la main *avant* qu'on ouvre la recipe.
    # Rouvrir n'y changerait rien — c'est un choix à faire, pas un conflit à
    # résoudre — donc l'écran pose la question et renvoie la réponse ici.
    verdict = (
        ""
        if body.overwrite_sql
        else _sql_diverged(
            a, name, old_file if old_file is not None else model_file, cols
        )
    )
    if verdict.startswith("indetermine:"):
        raise HTTPException(
            409,
            {
                "code": "sql_diverge",
                "error": (
                    f"L'atelier ne peut pas dire si "
                    f"« {(old_file or model_file).name} » contient encore le "
                    f"SQL de ce script visuel : le script enregistré ne compile "
                    f"plus ({verdict.split(':', 1)[1].strip()}). Le comparer est "
                    f"impossible, et enregistrer régénérerait le fichier — une "
                    f"modification faite à la main serait perdue. Ouvrez le SQL "
                    f"pour voir ce qu'il contient avant de trancher."
                ),
            },
        )
    if verdict:
        raise HTTPException(
            409,
            {
                "code": "sql_diverge",
                "error": (
                    f"« {(old_file or model_file).name} » ne contient "
                    f"plus le SQL que ce script visuel produit : il a été "
                    f"modifié en dehors de l'atelier. Enregistrer le "
                    f"régénérerait, et cette modification serait perdue."
                ),
            },
        )


def _recipe_digest(a: Workshop, name: str) -> str | None:
    """Empreinte du script visuel, ou None s'il n'y en a pas encore."""
    try:
        return files.digest(rcp.recipe_file(a.settings.project_dir, name))
    except rcp.RecipeError:
        return None


def _recipe_base(
    a: Workshop, name: str, model_rel: str | None, schema_path: str
) -> dict:
    """Ce que l'éditeur devra rendre pour prouver qu'il a lu la bonne version."""
    fingerprint = None
    if model_rel:
        try:
            fingerprint = files.digest(files.safe_path(a.settings, model_rel))
        except files.FileError:
            fingerprint = None
    return {
        "model_path": model_rel,
        "sql": fingerprint,
        "schema_path": schema_path,
        "doc": files.doc_digest(a.settings, schema_path, name),
        # Le script visuel porte plus que le SQL qu'il produit : une étape
        # désactivée, un paramètre sans effet sur le rendu, l'ordre de deux
        # étapes commutatives. Sans son empreinte, ces modifications-là
        # s'écrasaient sans qu'aucune des deux autres ne bouge.
        "recipe": _recipe_digest(a, name),
    }
