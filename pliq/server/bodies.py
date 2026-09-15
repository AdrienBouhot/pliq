"""Les corps de requête, et ce que chaque champ engage.

Plusieurs portent une empreinte : l'écran rend ce qu'il a lu à l'ouverture,
et le serveur refuse d'écraser une version qu'il n'a pas vue. C'est le seul
endroit où la forme de l'API est écrite en un bloc.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RunBody(BaseModel):
    command: str = "build"
    select: str = ""
    exclude: str = ""
    full_refresh: bool = False


class SqlBody(BaseModel):
    sql: str
    # L'empreinte du fichier tel que l'écran l'a lu, rendue par
    # `GET /api/dataset/{uid}`. Sans elle, la sauvegarde écrase à l'aveugle ce
    # qu'un autre onglet ou un éditeur de texte a écrit entre-temps.
    base: str | None = None


class SampleBody(BaseModel):
    limit: int = Field(default=200, ge=1, le=5000)


class DocBody(BaseModel):
    description: str = ""
    columns: list[dict] = Field(default_factory=list)
    # L'empreinte de la seule entrée du YAML, rendue par la lecture.
    base: str | None = None


class TestsBody(BaseModel):
    description: str = ""
    columns: list[dict] = Field(default_factory=list)
    tags: list[str] | None = None
    freshness: dict | None = None
    base: str | None = None


class RecipeBody(BaseModel):
    spec: dict
    upto: int | None = None
    limit: int = Field(default=200, ge=1, le=5000)


class SaveRecipeBody(BaseModel):
    spec: dict
    run: bool = False
    # Une création et une modification n'ont pas les mêmes droits : la première
    # ne doit écraser aucun fichier, la seconde doit retrouver celui qu'elle a
    # lu. `base` porte les empreintes rendues par `GET /api/recipe/{name}`.
    is_new: bool = False
    base: dict | None = None
    # Nul pour une recipe neuve : le modèle n'existe pas encore.
    model_uid: str | None = None
    # Régénérer un `.sql` qui ne dit plus ce que la recipe produit. L'écran ne
    # l'envoie qu'après avoir montré ce qui sera remplacé, et sur un clic.
    overwrite_sql: bool = False


class DeleteRecipeBody(BaseModel):
    name: str
    delete_model: bool = True
    # L'identité du modèle que l'écran avait sous les yeux, rendue par le Flow.
    # Un nom ne distingue pas le `orders` du projet de celui d'un paquet ;
    # `unique_id`, si.
    model_uid: str | None = None


class RenameRecipeBody(BaseModel):
    name: str
    new_name: str
    # Un aperçu : la route dit ce qu'elle toucherait, et n'écrit rien.
    dry_run: bool = False
    model_uid: str | None = None


class OpenProjectBody(BaseModel):
    path: str


class CreateProjectBody(BaseModel):
    parent: str
    name: str
    adapter: str = "duckdb"
    open_it: bool = Field(default=True, alias="open")

    model_config = {"populate_by_name": True}


class ScanBody(BaseModel):
    root: str


class ForgetProjectBody(BaseModel):
    path: str
    delete_files: bool = False
    confirm: str = ""


class SourceBody(BaseModel):
    source_name: str
    schema_name: str
    database: str | None = None
    description: str = ""
    tables: list[dict] = Field(default_factory=list)


class SuggestBody(BaseModel):
    inputs: list[dict] = Field(default_factory=list)


class SpecBody(BaseModel):
    spec: dict


class FullProfileBody(BaseModel):
    uid: str | None = None
    spec: dict | None = None
