"""L'interface et le serveur doivent rester d'accord.

Aucune dépendance JS dans ce projet, donc pas de suite de tests navigateur :
on se contente de ce qui se vérifie statiquement, et c'est déjà ce qui casse en
pratique — une route renommée, un processeur retiré, un opérateur ajouté d'un
seul côté.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pliq import recipes as rcp
from pliq.recipes import ColumnState

STATIC = Path(__file__).resolve().parent.parent / "pliq" / "static"
JS = STATIC / "js"
# L'interface est un paquet de modules ES. Ces tests cherchent une phrase
# quelque part dedans, sans se soucier du module qui la porte : le contrôle
# reste vrai après un déplacement, et c'est bien l'accord entre l'écran et le
# serveur qu'on vérifie, pas la carte des fichiers.
MODULES = sorted(JS.glob("*.js"))
APP_JS = "\n".join(f.read_text() for f in [*MODULES, STATIC / "app.js"])
# Le serveur est un paquet : ces tests cherchent une phrase quelque part dans
# le code des routes, sans se soucier du fichier qui la porte. Les concaténer
# garde le contrôle vrai après un déplacement — c'est l'accord entre
# l'interface et le serveur qui est vérifié, pas la carte des fichiers.
SERVER_PY = "\n".join(
    sorted(f.read_text() for f in (STATIC.parent / "server").glob("*.py"))
)


# --------------------------------------------------------------- l'interface


def test_les_fichiers_de_l_interface_sont_la():
    for f in ("index.html", "app.js", "app.css"):
        assert (STATIC / f).is_file(), f


def test_index_ne_reference_que_des_fichiers_existants():
    html = (STATIC / "index.html").read_text()
    for src in re.findall(r'(?:src|href)="/static/([^"]+)"', html):
        assert (STATIC / src).is_file(), src


def test_l_interface_ne_depend_d_aucun_serveur_exterieur():
    """L'atelier tourne sur un poste, souvent hors ligne.

    Les polices venaient d'un CDN : la page ne s'affichait bien qu'avec un
    accès réseau, et chaque ouverture annonçait à Google qu'on travaillait.
    Rien d'autre dans l'outil ne sort de la machine — ce `link` était la seule
    exception, et elle n'avait aucune raison d'être.
    """
    external = []
    for file in ("index.html", "app.css", "fonts.css", "app.js"):
        for url in re.findall(r'https?://[^\s"\')]+', (STATIC / file).read_text()):
            # Un lien en commentaire documente, il ne charge rien.
            if url.startswith(("https://docs.getdbt.com", "https://github.com")):
                continue
            external.append(f"{file} : {url}")
    assert not external, f"ressources chargées depuis l'extérieur : {external}"


def test_les_polices_annoncees_sont_toutes_embarquees():
    css = (STATIC / "fonts.css").read_text()
    files = re.findall(r"url\(/static/([^)]+)\)", css)
    assert files, "aucune police déclarée"
    for f in files:
        assert (STATIC / f).is_file(), f
    # Les deux familles que `app.css` nomme en premier dans ses piles.
    assert "IBM Plex Mono" in css and "Source Sans 3" in css


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
@pytest.mark.parametrize("file", [STATIC / "app.js", *MODULES], ids=lambda p: p.name)
def test_chaque_module_est_syntaxiquement_valide(file: Path):
    """`--input-type=module` : un `.js` est du CommonJS pour node sans ça, et
    le premier `import` passerait pour une erreur de syntaxe."""
    node = subprocess.run(
        ["node", "--input-type=module", "--check"],
        input=file.read_text(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert node.returncode == 0, f"{file.name} : {node.stderr}"


# Le DOM que le harnais ci-dessous prête aux modules : juste assez pour que
# `wire()` retrouve ses nœuds et enregistre ses écouteurs. Tout nœud répond à
# tout, et `getElementById` rend deux fois le même objet — un écouteur posé
# sur « nr-mat » doit se retrouver quand le test le déclenche.
_DOM_STUB = """
const handlers = new Map();
const mk = (id) => ({
  id, value: "table", innerHTML: "", textContent: "", dataset: {}, style: {},
  classList: { add(){}, remove(){}, toggle(){}, contains: () => false },
  addEventListener(ev, fn) { handlers.set(id + ":" + ev, fn); },
  removeEventListener(){}, appendChild(){}, append(){}, remove(){},
  querySelector: () => mk("x"), querySelectorAll: () => [], closest: () => null,
  focus(){}, click(){}, setAttribute(){}, getAttribute: () => null,
  insertAdjacentHTML(){}, replaceChildren(){}, children: [], childNodes: [],
  parentNode: null, checked: false, options: [], scrollIntoView(){},
});
const seen = new Map();
globalThis.document = {
  getElementById: (id) => seen.get(id) ?? (seen.set(id, mk(id)), seen.get(id)),
  createElement: (t) => mk(t), querySelector: () => mk("x"),
  querySelectorAll: () => [], addEventListener(){},
  body: mk("body"), documentElement: mk("html"),
};
globalThis.window = { addEventListener(){}, location: { href: "" },
  matchMedia: () => ({ matches: false, addEventListener(){} }) };
globalThis.localStorage = { getItem: () => null, setItem(){}, removeItem(){} };
globalThis.fetch = async () => (
  { ok: true, json: async () => ({}), text: async () => "" });
globalThis.EventSource = class { addEventListener(){} close(){} };
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_l_indice_de_materialisation_s_affiche_sans_nom_libre():
    """Un nom libre ne casse qu'à l'exécution, et aucun contrôle statique ne le voyait.

    Le découpage qui a sorti `output.js` de `recipe.js` a réduit la ligne
    d'import à `{ state }` en laissant un `MAT_HELP` derrière lui.
    `renderMatHint()` est appelé par `openNewRecipe()` *avant* l'ouverture de
    la modale : créer une recipe était impossible, et la suite entière passait
    au vert — le contrôle de syntaxe accepte un nom libre, et celui des
    imports ne juge que les noms déjà écrits dans un `import`.

    On charge donc les vrais modules sous un DOM de fortune et on déclenche le
    « change » que `wire()` pose sur « nr-mat » : c'est le chemin exact.
    """
    harness = _DOM_STUB + f"""
const mod = await import("{(JS / "recipe.js").as_uri()}");
mod.wire();
const fn = handlers.get("nr-mat:change");
if (!fn) throw new Error("wire() ne branche plus le change de nr-mat");
fn();
"""
    node = subprocess.run(
        ["node", "--input-type=module"],
        input=harness,
        capture_output=True,
        text=True,
        check=False,
    )
    assert node.returncode == 0, node.stderr


def test_chaque_import_de_module_designe_un_fichier_reel():
    for file in [STATIC / "app.js", *MODULES]:
        for target in re.findall(r'from "\./(?:js/)?([\w.-]+)"', file.read_text()):
            path = (file.parent / target) if file.parent == JS else (JS / target)
            assert path.is_file(), f"{file.name} importe {target}, absent"


def test_aucun_module_n_agit_pendant_son_chargement():
    """C'est ce qui rend les imports croisés inoffensifs.

    Les modules de l'interface se référencent en rond — le Flow ouvre une
    recipe, la recipe recharge le Flow — et ES les charge quand même, à une
    condition : que personne n'appelle rien pendant que les autres se chargent.
    Une `const` fléchée appelée trop tôt lèverait une ReferenceError, sur un
    chemin qu'aucun test Python ne parcourt.

    Les 88 `addEventListener` de l'ancien fichier vivent donc dans un
    `wire()` que `app.js` appelle une fois tout évalué.
    """
    offenders = []
    for file in MODULES:
        for n, line in enumerate(file.read_text().splitlines(), 1):
            if not line.strip() or line[0] in " \t/*})];":
                continue
            starts = (
                "export ",
                "import ",
                "async function ",
                "function ",
                "const ",
                "let ",
                "var ",
            )
            if line.startswith(starts):
                continue
            offenders.append(f"{file.name}:{n} {line.strip()[:60]}")
    assert not offenders, (
        "instructions exécutées au chargement d'un module — elles doivent "
        f"passer dans `wire()` : {offenders}"
    )


def _exports_by_module() -> dict[str, set[str]]:
    """Ce que chaque module déclare exporter."""
    out: dict[str, set[str]] = {}
    for file in MODULES:
        source = file.read_text()
        names: set[str] = set()
        # Les deux formes en usage : le bloc d'une ligne et celui qui s'étale.
        for block in re.findall(r"^export\s*\{(.*?)\}\s*;", source, re.S | re.M):
            names |= {
                n.strip().rstrip(",") for n in re.split(r"[,\n]", block) if n.strip()
            }
        for m in re.finditer(
            r"^export\s+(?:async\s+)?(?:function|const|let|var)\s+([\w$]+)",
            source,
            re.M,
        ):
            names.add(m.group(1))
        out[file.name] = names
    return out


def _imports_by_module() -> dict[str, set[str]]:
    """Ce que chaque module se voit réellement demander, `app.js` compris."""
    requested: dict[str, set[str]] = {f.name: set() for f in MODULES}
    for file in [STATIC / "app.js", *MODULES]:
        for block, target in re.findall(
            r'import\s*\{([^}]*)\}\s*from\s*"\./(?:js/)?([\w.-]+\.js)"',
            file.read_text(),
        ):
            for n in block.split(","):
                name = n.strip().split(" as ")[0].strip()
                if name:
                    requested.setdefault(target, set()).add(name)
    return requested


def test_aucun_module_n_exporte_ce_que_personne_n_importe():
    """Un `export` que rien n'importe n'est pas une interface.

    Les modules exportaient tout ce qu'ils déclaraient — `recipe.js` sortait
    soixante-seize noms dont dix-sept servaient, `flow.js` cinquante-cinq pour
    dix-sept. Personne ne pouvait plus lire où passait la frontière d'un
    module, donc personne ne pouvait dire ce qu'un découpage casserait : tout
    avait l'air public. C'est la première chose à tenir pour que ces fichiers
    redeviennent séparables.

    Le remède n'est jamais d'allonger ce test : c'est de retirer le nom du
    bloc `export`, ou de l'importer là où il devait servir.
    """
    exports, imports = _exports_by_module(), _imports_by_module()
    dead = {
        module: sorted(names - imports.get(module, set()))
        for module, names in exports.items()
        if names - imports.get(module, set())
    }
    assert not dead, f"noms exportés que personne n'importe : {dead}"


def test_chaque_nom_importe_est_bien_exporte():
    """L'envers : un import qui ne désigne rien est `undefined` à l'exécution.

    Un module ES n'échoue pas au chargement sur un nom absent de la cible —
    la ligne casse plus tard, à l'appel, et seulement si on y passe. Le
    contrôle du découpage se fait donc ici.
    """
    exports, missing = _exports_by_module(), []
    for file in [STATIC / "app.js", *MODULES]:
        for block, target in re.findall(
            r'import\s*\{([^}]*)\}\s*from\s*"\./(?:js/)?([\w.-]+\.js)"',
            file.read_text(),
        ):
            for n in block.split(","):
                name = n.strip().split(" as ")[0].strip()
                if name and name not in exports.get(target, set()):
                    missing.append(f"{file.name} importe {name} de {target}")
    assert not missing, missing


def test_aucun_module_n_affecte_un_nom_importe():
    """Un binding importé est immuable en module ES.

    Dans l'ancien `app.js` d'un seul tenant, `sqlTarget = null` visait un `let`
    du même fichier. Le découpage en a fait un import sans toucher à
    l'affectation : elle lève désormais une TypeError à l'exécution, et
    `closeOpenForms()` s'interrompait au milieu — les écrans de l'ancien projet
    restaient ouverts pendant que l'atelier écrivait déjà dans le nouveau.

    Aucun test statique ne pouvait l'attraper avant celui-ci : celui de la
    bascule vérifiait la *présence* de la chaîne « sqlTarget = null », donc
    justement la ligne fautive. La remise à zéro passe maintenant par une
    fonction exportée par le module qui possède la variable.
    """
    offenders = []
    for file in MODULES:
        source = file.read_text()
        imported: set[str] = set()
        for block in re.findall(r"^import \{([^}]*)\} from", source, re.M):
            imported |= {n.strip() for n in block.split(",") if n.strip()}
        if not imported:
            continue
        for n, line in enumerate(source.splitlines(), 1):
            bare = line.strip()
            if bare.startswith(("import ", "//", "*", "/*")):
                continue
            for name in imported:
                # `nom = …` mais pas `==`, `=>` ; plus `++`, `--`, `+=`…
                pattern = (
                    rf"(?<![\w$.]){re.escape(name)}"
                    r"\s*(?:=(?![=>])|\+\+|--|[-+*/|&]=)"
                )
                if re.search(pattern, line):
                    offenders.append(f"{file.name}:{n} {bare[:70]}")
    assert not offenders, (
        "affectation à un nom importé — immuable en module ES, donc TypeError "
        f"à l'exécution : {offenders}"
    )


def test_le_demarrage_n_a_lieu_qu_une_fois():
    """Deux `boot()`, c'est deux WebSocket et chaque événement traité en double.

    Le découpage a mis le `boot();` de premier niveau de l'ancien fichier dans
    le `wire()` de son module, alors que `app.js` l'appelle déjà à la fin.
    `connectWs()` n'a aucune garde d'idempotence : la page ouvrait deux sockets,
    et chaque `run_done` donnait deux toasts, deux `loadFlow` et deux lignes
    « Terminé en … s » dans le journal.

    `wire()` ne câble que des écouteurs ; le démarrage appartient au point
    d'entrée.
    """
    calls = []
    for file in MODULES:
        for n, line in enumerate(file.read_text().splitlines(), 1):
            if re.search(r"(?<![\w$.])boot\(\)\s*;", line):
                calls.append(f"{file.name}:{n}")
    assert not calls, f"`boot()` appelé depuis un module : {calls}"
    input_entry = (STATIC / "app.js").read_text()
    assert len(re.findall(r"^boot\(\);$", input_entry, re.M)) == 1


def test_le_point_d_entree_branche_puis_demarre():
    input_entry = (STATIC / "app.js").read_text()
    wirings = re.findall(r"^wire_(\w+)\(\);$", input_entry, re.M)
    assert wirings, "aucun module branché"
    for module in wirings:
        assert (JS / f"{module}.js").is_file(), module
        assert "export function wire()" in (JS / f"{module}.js").read_text(), module
    # Sur les lignes entières : `wire_boot();` contient « boot(); ».
    lines = [line.strip() for line in input_entry.splitlines()]
    assert lines.index("boot();") > max(
        i for i, line in enumerate(lines) if line.startswith("wire_")
    ), (
        "`boot()` doit partir après tous les branchements : il lit des "
        "éléments que ceux-ci viennent d'équiper"
    )


def test_chaque_id_utilise_par_le_js_existe_dans_le_html():
    """`$("truc")` sur un id absent rend `null`, et la ligne casse en silence.

    C'est exactement ce qui rendait la coupure du suivi en direct invisible :
    une alerte qu'on croit afficher, mais dont l'élément n'existe pas.
    """
    html = (STATIC / "index.html").read_text()
    in_html = set(re.findall(r'id="([^"]+)"', html))
    used = set(re.findall(r'\$\("([^"]+)"\)', APP_JS))
    missing = sorted(used - in_html)
    assert (
        not missing
    ), f"ids utilisés par app.js mais absents de index.html : {missing}"


def test_les_modales_a_une_colonne_gardent_leur_marge():
    """`.modal-body` est une grille à deux moitiés, et sa marge vient de
    `.mhalf`. Une modale à une seule colonne n'en avait donc aucune : le champ
    de saisie touchait le cadre, alors que l'en-tête au-dessus était en retrait.

    On vérifie que la classe existe et qu'aucune modale ne repasse par le
    `display:block` en ligne qui contournait la feuille de style."""
    html = (STATIC / "index.html").read_text()
    css = (STATIC / "app.css").read_text()

    rule = re.search(r"\.modal-body\.solo\{([^}]*)\}", css)
    assert rule, "la classe `.modal-body solo` a disparu de la feuille de style"
    assert "padding" in rule.group(
        1
    ), f"`.modal-body.solo` sans marge : {rule.group(1)}"

    inline_text = re.findall(
        r'class="modal-body"[^>]*style="[^"]*display:\s*block', html
    )
    assert not inline_text, (
        'une modale à une colonne doit porter `class="modal-body solo"` : '
        "le `display:block` en ligne écrase la grille sans rendre la marge"
    )


def test_la_coupure_du_suivi_en_direct_est_signalee():
    """Un WebSocket qui ne s'établit jamais ne doit pas passer inaperçu."""
    assert 'id="ws-warn"' in (STATIC / "index.html").read_text()
    assert "ws.onopen" in APP_JS, "sans onopen, impossible de savoir qu'on est revenu"
    assert "onWsDown" in APP_JS, "les échecs de connexion doivent être comptés"


# ------------------------------------------------------ contrat avec l'API


def _server_routes() -> set[str]:
    routes = re.findall(r'@app\.(?:get|post|put|delete)\("([^"]+)"\)', SERVER_PY)
    return {re.sub(r"\{[^}]+\}", "{}", r) for r in routes}


def _ui_calls() -> set[str]:
    literals = re.findall(r'"(/api/[^"]*)"', APP_JS)
    templates = re.findall(r"`(/api/[^`]*)`", APP_JS)
    out = set()
    for path in literals + templates:
        path = re.sub(r"\$\{[^}]+\}", "{}", path)
        path = path.split("?")[0].rstrip("/")
        out.add(path)
    return out


def test_toutes_les_routes_appelees_par_l_interface_existent():
    routes = _server_routes()
    missing = {
        path
        for path in _ui_calls()
        # `/api/flow${refresh ? "?refresh=true" : ""}` : la partie variable est
        # une chaîne de requête, pas un segment de chemin.
        if path not in routes and path.removesuffix("{}") not in routes
    }
    assert not missing, f"routes appelées mais absentes du serveur : {missing}"


# ------------------------------------------- contrat avec les processeurs


def _form_keys() -> set[str]:
    """Les processeurs pour lesquels l'interface sait afficher un formulaire."""
    start = APP_JS.index("const FIELDS = {")
    block = APP_JS[start : APP_JS.index("\n};", start)]
    return set(re.findall(r"^  ([a-z_]+):", block, re.M))


def test_chaque_formulaire_correspond_a_un_processeur():
    unknown = _form_keys() - set(rcp.PROCESSORS)
    assert not unknown, f"formulaires sans processeur : {unknown}"


def test_chaque_processeur_a_son_formulaire():
    without_form = set(rcp.PROCESSORS) - _form_keys()
    assert (
        not without_form
    ), f"processeurs non éditables dans l'interface : {without_form}"


def _options(name: str) -> list[str]:
    block = re.search(rf"const {name} = \[(.*?)\];", APP_JS, re.S).group(1)
    return re.findall(r'\["([^"]+)"', block)


# ------------------------------------ les listes déroulantes viennent du Python
#
# Elles étaient écrites des deux côtés : mêmes clés, mêmes libellés français,
# et un test par liste pour vérifier que les deux copies restaient d'accord. Un
# test qui surveille une duplication ne la remplace pas — il en fait une dette
# qu'on entretient, et il ne dit rien du jour où le serveur gagne un opérateur
# que l'écran n'offre pas.
#
# Ce qu'on vérifie maintenant est l'inverse : qu'il n'y a **qu'une** source, et
# que l'écran s'y branche.


@pytest.mark.parametrize("name", ["OPS", "TYPES", "WINDOW_FNS", "PIVOT_AGGS"])
def test_les_listes_deroulantes_ne_sont_pas_ecrites_dans_le_js(name: str):
    """Une liste écrite ici finirait par ne plus dire ce que le serveur dit."""
    block = re.search(rf"const {name} = \[(.*?)\];", APP_JS, re.S)
    assert block, f"{name} a disparu : `FIELDS` en garde la référence"
    assert not block.group(1).strip(), (
        f"{name} porte des valeurs en dur : elles doivent venir de "
        f"`/api/processors`, sans quoi les deux copies divergeront"
    )


@pytest.mark.parametrize(
    "field", ["operators", "window_functions", "pivot_aggregations", "types"]
)
def test_l_interface_remplit_ses_listes_depuis_le_serveur(field: str):
    assert f"lib.{field}" in APP_JS, f"`{field}` n'est jamais lu de la réponse"
    assert f'"{field}"' in SERVER_PY, f"`{field}` n'est jamais servi"


@pytest.mark.parametrize(
    "key",
    [
        "materializations",
        "incremental_strategies",
        "batch_sizes",
        "lookback_units",
        "on_schema_change",
        "strategies_needing_key",
        "window_needs_column",
        "window_needs_order",
    ],
)
def test_les_listes_fermees_de_dbt_partent_vides_dans_le_js(key: str):
    """Même règle pour `state.caps` : rien en dur, tout du serveur."""
    block = re.search(rf"^    {key}: \[(.*?)\],$", APP_JS, re.M)
    assert block, f"`state.caps.{key}` a disparu"
    assert not block.group(1).strip(), f"`state.caps.{key}` porte un repli en dur"
    assert f'"{key}"' in SERVER_PY, f"`{key}` n'est jamais servi"


def test_les_operateurs_servis_sont_tous_compiles():
    """Ce que le menu offre, le compilateur doit savoir l'écrire.

    C'est le contrôle qui compte maintenant qu'il n'y a plus qu'une liste :
    elle est la même des deux côtés par construction, mais rien ne garantit
    qu'un opérateur ajouté au vocabulaire ait un sens pour `_predicate`.
    """
    state = ColumnState(["status"], {"status": "VARCHAR"})
    for op in rcp.FILTER_OPERATORS:
        rcp._predicate(state, {"column": "status", "operator": op, "values": ["x"]})


def test_les_agregations_de_pivot_servies_sont_toutes_compilees():
    for fn in rcp.PIVOT_AGGREGATIONS:
        rcp.pivot_alias(fn)  # un nom d'alias valide pour chacune
    assert set(rcp.PIVOT_AGGREGATIONS) <= {"max", "min", "sum", "avg", "count"}


def test_l_ecran_distingue_les_etapes_que_l_apercu_a_executees():
    """L'œil borne le SQL, pas ce qu'on sait des colonnes — et l'écran le sait.

    Le serveur rend les deltas de tout le script, en marquant d'un `applied`
    ceux qui sont dans le SQL exécuté. Ce qui décrit la grille — les fantômes,
    la teinte — ne lit que ceux-là ; ce qui décrit le script — les colonnes qui
    entrent dans une étape, celles que la sortie produira — les lit tous. Sans
    le drapeau, les deux se remettent silencieusement à dire la même chose, et
    le formulaire d'une étape placée après l'œil propose les colonnes de l'œil.
    """
    spec = {
        "name": "t",
        "type": "prepare",
        "inputs": [{"ref": "src", "alias": "src"}],
        "output": {"materialized": "view"},
        "steps": [
            {"id": "s0", "type": "formula", "params": {"into": "b", "expression": "a"}},
            {"id": "s1", "type": "formula", "params": {"into": "c", "expression": "b"}},
        ],
    }
    cols = {"src": [{"name": "a", "type": "INTEGER"}]}
    _, _, deltas = rcp.compile_prepare(spec, cols, upto=1)
    assert [d["applied"] for d in deltas] == [True, False]
    assert deltas[1]["created"] == ["c"]

    assert "applied !== false" in APP_JS, "l'écran ne filtre plus la grille"
    assert "applied === false" in APP_JS, "l'écran ne repère plus un aperçu borné"


def test_chaque_operateur_a_son_libelle():
    """Un opérateur sans libellé s'afficherait sous sa clé technique."""
    for key, label in rcp.FILTER_OPERATORS.items():
        assert label and label != key, key


def test_le_libelle_du_menu_est_celui_de_la_carte_d_etape():
    """Une seule liste, donc une seule façon de dire « commence par ».

    Les deux vivaient séparément : le menu déroulant dans le JS, la phrase de
    la carte dans `describe_step`. Rien n'obligeait les deux à se ressembler.
    """
    for key, label in rcp.FILTER_OPERATORS.items():
        sentence = rcp.describe_step(
            {
                "type": "filter_value",
                "params": {"column": "c", "operator": key, "values": ["x"]},
            }
        )
        assert label in sentence, (key, sentence)


def test_l_interface_annonce_le_projet_qu_elle_croit_modifier():
    """Deux onglets, un seul projet actif : le serveur compare avant d'écrire.

    Si l'en-tête est renommé d'un seul côté, la protection redevient muette —
    et une recipe commencée dans un projet s'enregistre dans l'autre.
    """
    assert '"X-Pliq-Project"' in APP_JS
    assert 'PROJECT_HEADER = "x-pliq-project"' in SERVER_PY
    assert "check_project()" in SERVER_PY


def test_les_transformations_de_texte_proposees_existent():
    block = re.search(r'k: "mode".*?opts: \[(.*?)\]', APP_JS, re.S).group(1)
    for mode in re.findall(r'\["([^"]+)"', block):
        rcp.PROCESSORS["text_transform"]["fn"](
            ColumnState(["c"], {"c": "VARCHAR"}), {"column": "c", "mode": mode}
        )


def test_les_mesures_proposees_sont_toutes_compilees():
    """Y compris sur la colonne que l'interface autorise pour chacune.

    Elle proposait « * » pour « nombre de valeurs distinctes », ce qui compile
    en `count(distinct *)` — pas du SQL.
    """
    for fn in _options("AGG_FNS"):
        assert fn in rcp.AGGREGATIONS, f"mesure inconnue du compilateur : {fn}"
        rcp._aggregate(fn, "montant", "statut = 'ok'")
    star = re.search(r'a\.fn === "([a-z_]+)" \? \["\*"\]', APP_JS).group(1)
    rcp._aggregate(star, "*")


def test_les_materialisations_et_strategies_de_l_interface_existent():
    for mat in re.findall(r'"(view|table|incremental|ephemeral)"', APP_JS):
        assert mat in rcp.MATERIALIZATIONS
    for strategy in re.findall(r'"(append|delete\+insert|merge|microbatch)"', APP_JS):
        assert strategy in rcp.INCREMENTAL_STRATEGIES


def test_le_pliage_est_demande_au_serveur_sous_le_nom_qu_il_attend():
    """Replier change la disposition : c'est le serveur qui la refait.

    L'interface envoie `fold=` sur `/api/flow`. Si le paramètre est renommé d'un
    seul côté, le Flow revient simplement déplié, sans erreur — le genre de
    panne muette que ce fichier existe pour attraper.
    """
    assert '"fold=" + encodeURIComponent' in APP_JS
    assert 'def get_flow(refresh: bool = False, fold: str = "")' in SERVER_PY


def test_l_interface_demande_les_trois_reglages_de_microbatch():
    """Proposer la stratégie sans les demander produisait un modèle qui ne parse pas.

    dbt exige `event_time`, `begin` et `batch_size`. Le panneau « Sortie » doit
    donc les poser, et le serveur doit servir la liste des tailles de tranche.
    """
    assert "microbatchFields" in APP_JS
    for key in ("event_time", "begin", "batch_size"):
        assert key in APP_JS, key
    assert '"batch_sizes": list(rcp.BATCH_SIZES)' in SERVER_PY


def test_l_apercu_numerote_ses_requetes():
    """Deux aperçus ne reviennent pas forcément dans l'ordre où ils sont partis.

    Sans numéro de série, la réponse lente écrase la récente et la grille
    montre un script qu'on n'écrit plus — sans que rien ne le dise.
    """
    assert "previewSeq" in APP_JS
    assert "seq !== previewSeq" in APP_JS, "la réponse périmée doit être jetée"


def test_l_apercu_manuel_existe_et_annonce_son_cout():
    """Chaque aperçu exécute la requête entière : sur un entrepôt facturé,
    ça se paie."""
    html = (STATIC / "index.html").read_text()
    assert 'id="rec-auto"' in html and 'id="rec-refresh"' in html
    assert 'id="rec-cost"' in html
    assert "previewAuto" in APP_JS


# ----------------------------------------- fenêtres, dédoublonnage, pivot


def test_le_dedoublonnage_par_cle_part_du_menu_de_colonne():
    """« Une ligne par client » se décide en regardant la colonne du client."""
    from pliq import profiling

    proposals = profiling.suggestions(
        {"name": "customer_id", "meaning": "integer", "storage": "int"}
    )
    assert any(p["processor"] == "dedup_key" for p in proposals)


# ------------------------------------------- historique et réordonnancement


def test_l_editeur_a_un_historique_d_edition():
    """Annuler / rétablir, et les boutons qui vont avec."""
    html = (STATIC / "index.html").read_text()
    assert 'id="btn-undo"' in html and 'id="btn-redo"' in html
    assert "pushHistory" in APP_JS and "travelHistory" in APP_JS
    assert "HIST_MAX" in APP_JS, "une pile sans borne finit par peser"


def test_l_historique_ne_sort_pas_de_l_onglet():
    """Le .sql sur le disque reste la seule vérité.

    Un brouillon en `localStorage` installerait une seconde vérité invisible,
    qui vieillit et entre en conflit avec les empreintes de fichiers.
    """
    block = re.search(r"function pushHistory\(\)[\s\S]*?\n}", APP_JS).group(0)
    assert "localStorage" not in block and "sessionStorage" not in block


def test_les_etapes_se_deplacent_et_se_dupliquent():
    for commits in ("up", "down", "dup"):
        assert f'data-act="{commits}"' in APP_JS, commits


def test_l_apercu_d_etape_suit_l_etape_qu_on_deplace():
    """L'œil suit un rang, pas une étape : sans ça il montre la voisine."""
    block = re.search(r"const move = \(delta\)[\s\S]*?\n  };", APP_JS).group(0)
    assert "rec.previewStep = target" in block


# --------------------------------------------- dépendances et fraîcheur


def test_l_atelier_sait_lancer_deps_et_source_freshness():
    html = (STATIC / "index.html").read_text()
    for command in ("deps", "freshness"):
        assert f'value="{command}"' in html, command
    block = re.search(r"allowed = \{(.*?)\}", SERVER_PY, re.S).group(1)
    allowed = set(re.findall(r'"([a-z ]+)"', block))
    assert {"deps", "freshness"} <= allowed, allowed


def test_les_paquets_manquants_sont_signales_et_installables():
    """Sans `dbt deps`, un projet existant ne parse pas : l'atelier le disait
    par un message brut de dbt, sans dire quoi faire."""
    html = (STATIC / "index.html").read_text()
    assert 'id="deps-warn"' in html
    assert "deps_needed" in APP_JS and "deps_needed" in SERVER_PY
    assert 'runDbt("deps"' in APP_JS, "la pastille doit lancer l'installation"


def test_deps_ne_part_jamais_avec_un_selecteur():
    """`dbt deps` installe des paquets : il ne sélectionne pas des nœuds."""
    assert 'select = "" if body.command == "deps" else body.select' in SERVER_PY


def test_la_fraicheur_a_sa_place_dans_le_flow():
    """Une transformation qui réussit peut reposer sur des données figées."""
    assert "paintFreshness" in APP_JS
    assert "renderFreshnessPanel" in APP_JS
    assert 'g["freshness"] = dict(a.svc.freshness)' in SERVER_PY
    assert ".fnode .fresh" in (STATIC / "app.css").read_text()


def test_les_unites_de_fraicheur_de_l_interface_sont_celles_de_dbt():
    from pliq import files

    block = re.search(r"const PERIOD_LABEL = \{(.*?)\};", APP_JS).group(1)
    assert set(re.findall(r"(\w+):", block)) == set(files.FRESHNESS_PERIODS)


# ------------------------------ contrôle complet et lignes en échec


def test_le_controle_complet_est_accessible_des_deux_grilles():
    """Explorer et l'éditeur de recipe ouvrent le même écran."""
    html = (STATIC / "index.html").read_text()
    assert 'id="rec-check"' in html and 'id="exp-check"' in html
    assert "openFullCheck" in APP_JS


def test_le_controle_complet_ne_se_fait_pas_passer_pour_un_echantillon():
    """Confondre les deux, c'est se tromper d'un facteur mille sans le savoir."""
    assert "la table entière, pas l'échantillon" in APP_JS
    assert '"scope": "complet"' in SERVER_PY


def test_un_test_en_echec_s_ouvre_sur_ses_lignes():
    assert "openTestFailures" in APP_JS
    assert "/api/test/{}/failures" in _server_routes()
    assert "compiled_test_sql" in (STATIC.parent / "dbt_service.py").read_text()


def test_le_controle_complet_perime_avec_l_apercu():
    """Des chiffres calculés sur la version d'avant ne doivent pas rester à l'écran."""
    assert "rec.fullCheck = null" in APP_JS


# ------------------------------------------- colonnes supprimées dans la grille


def _js_function(name: str) -> str:
    """Le corps d'une fonction de `app.js`, pour la faire tourner sous node."""
    block = re.search(rf"^function {name}\(.*?^\}}", APP_JS, re.S | re.M)
    assert block, f"fonction absente de app.js : {name}"
    return block.group(0)


def _js_functions(*names: str) -> str:
    """Plusieurs fonctions de `app.js`, avec ce dont elles dépendent."""
    return "\n".join(_js_function(name) for name in names)


def _grid(deltas: list[dict], columns: list[str], steps: list[dict]) -> dict:
    """Ce que la grille afficherait : l'ordre des colonnes, et les supprimées."""
    import json

    constant = re.search(r"^const RESHAPERS = .*$", APP_JS, re.M)
    assert constant, "la liste des étapes qui refont la table a disparu"
    source = constant.group(0) + "\n" + _js_functions("ghostLayout", "placeGhosts")
    driver = f"""
    {source}
    const rec = {{ spec: {{ steps: {json.dumps(steps)} }} }};
    const pv = {{
      deltas: {json.dumps(deltas)},
      columns: {json.dumps([{"name": c} for c in columns])},
    }};
    const out = ghostLayout(rec, pv);
    console.log(JSON.stringify({{
      order: out.order, ghosts: [...out.ghosts.keys()],
    }}));
    """
    node = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, check=False
    )
    assert node.returncode == 0, node.stderr
    return json.loads(node.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_une_colonne_supprimee_garde_sa_place_dans_la_grille():
    """Elle disparaissait de l'aperçu, et rien ne disait ce qu'on avait retiré.

    Elle reste donc affichée, à sa place, marquée supprimée — et l'ordre des
    colonnes vivantes reste celui que l'étape a produit, renommage compris.
    """
    view = _grid(
        deltas=[
            {
                "step": "s0",
                "before": ["a", "b", "c", "d"],
                "after": ["a", "c", "d"],
                "created": [],
                "deleted": ["b"],
            },
            {
                "step": "s1",
                "before": ["a", "c", "d"],
                "after": ["a", "cc", "d"],
                "created": ["cc"],
                "deleted": ["c"],
            },
        ],
        columns=["a", "cc", "d"],
        steps=[
            {"id": "s0", "type": "keep_delete"},
            {"id": "s1", "type": "rename"},
        ],
    )
    assert view["order"] == ["a", "b", "cc", "d"]
    # Renommer retire l'ancien nom sans rien supprimer : pas de fantôme.
    assert view["ghosts"] == ["b"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_un_pivot_efface_les_fantomes_de_la_grille():
    """Pivoter refait la table : une colonne supprimée avant n'y a plus de place."""
    view = _grid(
        deltas=[
            {
                "step": "s0",
                "before": ["key", "b", "name", "valeur"],
                "after": ["key", "name", "valeur"],
                "created": [],
                "deleted": ["b"],
            },
            {
                "step": "s1",
                "before": ["key", "name", "valeur"],
                "after": ["key", "x", "y"],
                "created": ["x", "y"],
                "deleted": ["name", "valeur"],
            },
        ],
        columns=["key", "x", "y"],
        steps=[
            {"id": "s0", "type": "keep_delete"},
            {"id": "s1", "type": "pivot"},
        ],
    )
    assert view["order"] == ["key", "x", "y"]
    assert view["ghosts"] == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_une_colonne_recreee_cesse_d_etre_un_fantome():
    """Sans ça, deux en-têtes du même nom se contrediraient dans la grille."""
    view = _grid(
        deltas=[
            {
                "step": "s0",
                "before": ["a", "b"],
                "after": ["a"],
                "created": [],
                "deleted": ["b"],
            },
            {
                "step": "s1",
                "before": ["a"],
                "after": ["a", "b"],
                "created": ["b"],
                "deleted": [],
            },
        ],
        columns=["a", "b"],
        steps=[
            {"id": "s0", "type": "keep_delete"},
            {"id": "s1", "type": "formula"},
        ],
    )
    assert view["order"] == ["a", "b"]
    assert view["ghosts"] == []


def test_la_grille_sait_dessiner_une_colonne_supprimee():
    """L'en-tête la barre, les cellules se hachurent, et le menu la rétablit."""
    css = (STATIC / "app.css").read_text()
    assert (
        ".dgrid th.gone" in css
    ), "l'en-tête d'une colonne supprimée n'a plus de style"
    assert (
        ".dgrid td.gone" in css
    ), "ses cellules doivent se distinguer d'une valeur vide"
    assert "tag-gone" in APP_JS and "tag-gone" in css
    assert "restoreColumn" in APP_JS, "une suppression doit pouvoir s'annuler du menu"


def test_le_serveur_dit_a_chaque_etape_ce_qu_elle_supprime():
    """La grille reconstitue les colonnes supprimées à partir des deltas."""
    compilation = (STATIC.parent / "recipes" / "compilers.py").read_text()
    assert '"deleted": deleted' in compilation
    assert '"before": before' in compilation


# ------------------------------------------- ce qui part, et ce qui est écrit


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_une_source_se_reference_avec_source_et_non_avec_ref():
    """Le SQL de départ d'une recipe « SQL » écrivait `ref('raw.orders')`.

    Un nom pointé n'est pas un modèle : dbt ne le résout pas, et la recipe ne
    compilait pas avant qu'on la corrige à la main. L'interface et le serveur
    doivent écrire la même chose — d'où la comparaison avec `ref_sql`.
    """
    import json

    driver = _js_functions("jinjaRef", "jinjaStr") + """
    console.log(JSON.stringify([
      jinjaRef({ ref: "orders", alias: "orders" }),
      jinjaRef({ source_name: "raw", table: "orders", alias: "orders" }),
    ]));
    """
    node = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, check=False
    )
    assert node.returncode == 0, node.stderr
    model, source = json.loads(node.stdout)

    assert model == rcp.ref_sql({"ref": "orders"})
    assert source == rcp.ref_sql({"source_name": "raw", "table": "orders"})
    assert source == "{{ source('raw', 'orders') }}"


def test_le_sql_de_depart_passe_par_le_meme_chemin():
    """Sans quoi la correction ne tiendrait que jusqu'au prochain gabarit."""
    block = re.search(r'if \(spec\.type === "sql"\).*', APP_JS).group(0)
    assert "jinjaRef(spec.inputs[0])" in block
    assert "ref('" not in block, "plus aucun ref() écrit à la main dans le gabarit"


def test_l_enregistrement_ne_declare_pas_enregistre_ce_qu_on_a_ecrit_depuis():
    """La réponse remettait `dirty` à faux sans regarder la révision envoyée.

    Continuer à éditer pendant la requête donnait donc un bandeau « ENREGISTRÉ »
    pour un état que personne n'avait écrit — et quitter l'écran ne prévenait
    plus.
    """
    block = re.search(r"async function doSave\([\s\S]*?\n\}", APP_JS).group(0)
    assert "const sent = JSON.stringify(rec.spec);" in block
    assert block.index("const sent") < block.index(
        "await post"
    ), "la révision doit être retenue *avant* l'aller-retour"
    assert "rec.dirty = JSON.stringify(rec.spec) !== sent;" in block
    assert "rec.dirty = false" not in block


def test_l_editeur_sql_ne_se_ferme_pas_sur_des_frappes_non_enregistrees():
    """Le bouton se grise pendant la requête, la zone de saisie non.

    La modale se fermait au retour : ce qui avait été tapé entre-temps partait
    avec elle, sans un mot.
    """
    block = re.search(
        # Le corps est indenté d'un cran : il vit dans le `wire()` de son
        # module, et non plus au premier niveau d'un script unique.
        r'\$\("sql-save"\)\.addEventListener\([\s\S]*?\n\s*\}\);',
        APP_JS,
    ).group(0)
    assert "const sent = target.editor.textarea.value;" in block
    assert block.index("const sent") < block.index("await put")
    assert "target.editor.textarea.value !== sent" in block
    # Et la modale qui reste ouverte repart de l'empreinte qu'on vient d'écrire.
    assert "target.base = r.digest" in block
    assert '"digest"' in SERVER_PY, "le serveur doit la rendre"


def test_un_modele_de_paquet_s_ouvre_en_lecture():
    """Ses fichiers ne sont pas dans ce projet : l'écrire écrirait à côté."""
    assert "d.editable !== false" in APP_JS
    assert "d.external === true" in APP_JS
    # Le serveur ne s'en remet pas à l'écran pour autant.
    assert "def writable_node" in SERVER_PY
    for route in ("save_sql", "save_doc"):
        block = re.search(rf"def {route}\(.*?\n\n", SERVER_PY, re.S).group(0)
        assert "writable_node" in block, route


# ------------------------------------- deux onglets, deux projets : côté écran
#
# Le serveur refuse déjà une écriture destinée à un autre projet : il compare
# l'en-tête `X-Pliq-Project` au projet ouvert (voir test_api.py). Encore
# faut-il que l'interface y mette la bonne valeur. Elle envoyait le projet
# *actif* — donc toujours celui du serveur, y compris juste après une bascule :
# la garde ne pouvait alors rien refuser. C'est le projet dont le formulaire a
# lu ses données qui doit partir.


def _calls(anchor: str) -> list[str]:
    """Les appels d'écriture qui passent par cette route, avec leurs options."""
    out = []
    i = APP_JS.find(anchor)
    while i != -1:
        out.append(APP_JS[i : i + 800])
        i = APP_JS.find(anchor, i + 1)
    return out


WRITES = [
    'post("/api/recipe/save"',
    'post("/api/recipe/delete"',
    'post("/api/recipe/rename"',
    'post("/api/datasets/source"',
    "post(`/api/dataset/${encodeURIComponent(node)}/tests`",
    "put(`/api/dataset/${encodeURIComponent(target.uid)}/sql`",
]


@pytest.mark.parametrize("anchor", WRITES)
def test_chaque_ecriture_dit_de_quel_projet_elle_vient(anchor: str):
    calls = _calls(anchor)
    assert calls, f"aucun appel trouvé pour {anchor} — l'ancre a-t-elle bougé ?"
    for block in calls:
        assert "{ project" in block, (
            f"{anchor} n'envoie pas son projet d'origine : après une bascule "
            f"depuis un autre onglet, cette écriture partirait dans le "
            f"mauvais projet sans que le serveur puisse s'en apercevoir"
        )


def test_l_entete_de_projet_peut_etre_impose_par_le_formulaire():
    """Sans cette surcharge, `api()` retombe sur le projet actif — et être
    d'accord avec le serveur par construction ne prouve rien."""
    assert '"project" in opts' in APP_JS
    assert 'headers["X-Pliq-Project"] = project' in APP_JS


# Les écrans qui se remplissent d'une lecture, et le nom de la fonction qui les
# ouvre. Chacun doit retenir son projet *avant* sa requête : mémoriser
# `projectDir()` après le `await` nomme le projet où l'on vient de basculer, et
# le formulaire — rempli avec les données du projet de départ — rend alors une
# valeur que la garde du serveur accepte au lieu de refuser.
READS = [
    "async function openTestsModal(",
    "async function openSqlModal(",
    "async function openRecipeNode(",
]


@pytest.mark.parametrize("anchor", READS)
def test_un_ecran_retient_son_projet_avant_sa_lecture(anchor: str):
    start = APP_JS.index(anchor)
    body = APP_JS[start : start + 1600]
    capture = body.find("currentProject()")
    timeout_s = body.find("await")
    assert capture != -1, f"{anchor} ne retient pas son projet d'origine"
    assert capture < timeout_s, (
        f"{anchor} retient son projet après sa requête : une bascule pendant "
        f"la lecture rattacherait ce formulaire au projet d'arrivée"
    )
    assert "stale()" in body, (
        f"{anchor} affiche sa réponse sans vérifier qu'on est toujours dans le "
        f"projet qu'elle décrit"
    )


def test_une_reponse_tardive_s_abandonne_au_lieu_de_s_afficher():
    """`perime()` doit se lire au projet ouvert *à cet instant*, pas à la copie."""
    start = APP_JS.index("function currentProject()")
    body = APP_JS[start : start + 300]
    assert "projectDir() !== project" in body


def test_une_bascule_de_projet_ferme_les_ecrans_de_l_ancien():
    """Une modale laissée ouverte montre l'ancien projet et promet un
    enregistrement qui ne peut plus aboutir."""
    assert "function closeOpenForms()" in APP_JS

    start = APP_JS.index("async function reloadProject()")
    assert "closeOpenForms()" in APP_JS[start : start + 400], (
        "reloadProject est le seul endroit où le projet actif change : "
        "c'est là que les écrans de l'ancien doivent être invalidés"
    )

    body = APP_JS[APP_JS.index("function closeOpenForms()") :][:1200]
    omissions = (
        "state.tests = null",
        "resetSqlTargets()",
        "resetRecipeDelete()",
        "resetDatasetModal()",
    )
    for forgets in omissions:
        assert forgets in body, f"{forgets} : le contenu lu doit partir avec l'écran"

    inventory = APP_JS[APP_JS.index("function resetDatasetModal()") :][:400]
    assert "dsState.sel.clear()" in inventory


def test_la_declaration_d_une_source_transmet_sa_base():
    """L'inventaire connaît la base de chaque table ; ne pas la transmettre
    laissait dbt prendre celle de la cible — donc, au pire, une table
    homonyme dans un autre catalogue."""
    (block,) = _calls('post("/api/datasets/source"')
    assert "database:" in block, "la base choisie doit partir avec la déclaration"
    assert (
        "selectedDatabases" in APP_JS
    ), "un groupe de sources porte une seule base : un mélange doit être refusé"


# ------------------------------------ l'identité du modèle, pas son nom


IDENTITY = [
    'post("/api/recipe/save"',
    'post("/api/recipe/delete"',
    'post("/api/recipe/rename"',
]


@pytest.mark.parametrize("anchor", IDENTITY)
def test_chaque_ecriture_de_recipe_dit_sur_quel_modele_elle_porte(anchor: str):
    """Un nom ne désigne pas un modèle : `orders` peut être celui du projet
    comme celui d'un paquet installé. Sans `model_uid`, la suppression partie
    de la recipe d'un paquet effaçait le `.sql` du modèle local homonyme, et
    l'API répondait 200."""
    calls = _calls(anchor)
    assert calls, f"aucun appel trouvé pour {anchor} — l'ancre a-t-elle bougé ?"
    for block in calls:
        assert "model_uid" in block, (
            f"{anchor} n'envoie que le nom : le serveur ne peut pas distinguer "
            f"deux modèles homonymes, et prend celui du projet ouvert"
        )


def test_l_ouverture_d_une_recipe_dit_de_quel_modele_elle_part():
    """La route est adressée par nom : `GET /api/recipe/orders` rendait le
    script visuel du projet, même pour le `orders` d'un paquet."""
    body = APP_JS[APP_JS.index("async function openRecipeNode(") :][:700]
    assert "model_uid=" in body


def test_aucune_action_d_ecriture_sur_un_modele_de_paquet():
    """L'interface doit fermer ce que le serveur refuse, et le dire avant le
    clic plutôt qu'après."""
    menu = APP_JS[APP_JS.index("function openRecipeMenu(") :][:2200]
    assert "off: r.external" in menu, "supprimer doit être grisé"

    panel = APP_JS[APP_JS.index("function renderRecipePanel(") :][:1600]
    assert "if (!r.external)" in panel, "pas de bouton Supprimer sur un paquet"

    modal = APP_JS[APP_JS.index("function openRecipeDelete(") :][:400]
    assert "r.external" in modal, "la modale doit refuser de s'ouvrir"

    flow = APP_JS[APP_JS.index("function openFlowMenu(") :][:1400]
    assert "off: !d.editable" in flow, "renommer un modèle de paquet"


def test_une_fiche_en_lecture_seule_ne_propose_pas_d_enregistrer():
    """Le serveur refuse d'écrire la fiche d'un modèle versionné : l'écran ne
    doit pas faire remplir un formulaire qui partira en 409."""
    body = APP_JS[APP_JS.index("async function openTestsModal(") :][:1400]
    assert "data.editable === false" in body
    assert '$("tests-save").hidden' in body
    assert "readonly_reason" in body


def test_le_bandeau_du_flow_affiche_la_cible():
    """La cible ne figurait que sur les cartes de l'accueil et dans la bannière
    du terminal. L'écran où l'on lance les builds n'en disait rien : un build
    parti sur une autre cible ne laissait aucune trace visible."""
    assert 'id="top-target"' in (STATIC / "index.html").read_text()
    body = APP_JS[APP_JS.index("function renderTarget()") :][:700]
    assert (
        "state.project.target" in body
        or "state.project && state.project.target" in body
    )
    # Rendu à chaque changement d'écran et après une bascule de projet.
    assert "renderTarget();" in APP_JS[APP_JS.index("function renderCrumb()") :][:200]


# ---------------------------- les jumeaux de l'alias et de l'échappement (C4/C5)


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_l_interface_donne_des_alias_distincts_et_valides():
    """L'alias venait du nom du dataset, sans contrôle d'unicité.

    Deux fois `orders` faisaient deux CTE `orders`, et l'entrepôt répondait
    `Duplicate CTE name`. Le serveur refuse le doublon ; l'interface doit faire
    en sorte qu'il n'y en ait jamais — et rabattre sur un identifiant les noms
    dbt qui n'en sont pas.
    """
    import json

    driver = _js_functions("slugAlias", "freeAlias") + """
    const taken = new Set();
    console.log(JSON.stringify([
      freeAlias("orders", 0, taken),
      freeAlias("orders", 1, taken),
      freeAlias("orders", 2, taken),
      freeAlias("order-items", 3, taken),
      freeAlias("2024_sales", 4, taken),
      freeAlias("", 5, taken),
    ]));
    """
    node = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, check=False
    )
    assert node.returncode == 0, node.stderr
    assert json.loads(node.stdout) == [
        "orders",
        "orders_2",
        "orders_3",
        "order_items",
        "_2024_sales",
        "input_5",
    ]

    # Et le serveur doit lire ces alias-là sans broncher.
    assert rcp.input_aliases(
        [{"ref": "orders", "alias": "orders"}, {"ref": "orders", "alias": "orders_2"}]
    ) == ["orders", "orders_2"]
    assert rcp.input_alias({"table": "order-items"}, 3) == "order_items"
    assert rcp.input_alias({"table": "2024_sales"}, 4) == "_2024_sales"


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_l_echappement_jinja_est_le_meme_des_deux_cotes():
    """`jinjaRef` écrivait les noms sans le moindre échappement."""
    import json

    driver = _js_functions("jinjaStr") + """
    console.log(JSON.stringify(["l'été", "a\\\\b", "d'hier"].map(jinjaStr)));
    """
    node = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, check=False
    )
    assert node.returncode == 0, node.stderr
    assert json.loads(node.stdout) == [
        rcp.jinja_str(v) for v in ("l'été", "a\\b", "d'hier")
    ]


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_une_source_au_nom_pointu_se_reference_pareil_des_deux_cotes():
    """`order-items` est une table de source valide : les deux côtés l'écrivent."""
    import json

    driver = _js_functions("jinjaRef", "jinjaStr") + """
    console.log(JSON.stringify(
      jinjaRef({ source_name: "brut", table: "order-items" })));
    """
    node = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, check=False
    )
    assert node.returncode == 0, node.stderr
    assert json.loads(node.stdout) == rcp.ref_sql(
        {"source_name": "brut", "table": "order-items"}
    )


# ------------------------------------------- constats d'audit reproduits sous node


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_b04_annuler_editer_annuler_retablir_ne_dit_pas_enregistre():
    """Le bandeau affichait ENREGISTRÉ sur un script jamais écrit sur le disque.

    `doSave` retenait l'*index* de la révision écrite, et `pushHistory` tronque
    l'historique après un « annuler » : l'index conservé désignait alors une
    révision qui n'existait plus, et la nouvelle prenait sa place. Quitter
    l'écran ne demandait aucune confirmation, et le travail partait sans un mot.

    On repère donc la révision enregistrée par sa *valeur*, et la troncature
    n'a plus rien à casser.
    """
    source = _js_functions("pushHistory", "travelHistory")
    driver = f"""
    const state = {{ rec: null }};
    const HIST_MAX = 80;
    const $ = () => ({{ classList: {{ contains: () => false }}, disabled: false }});
    const renderScript = () => {{}}, renderOutputPane = () => {{}};
    const renderRecipeTests = () => {{}}, refreshPreview = () => {{}};
    const markSaved = () => {{}};
    {source}
    const A = JSON.stringify({{ v: "A" }});
    const rec = {{ spec: {{ v: "A" }}, hist: [A], histIdx: 0,
                  savedSnap: A, dirty: false }};
    state.rec = rec;
    const trace = [];
    const note = (what) => trace.push([what, rec.dirty]);
    note("ouvrir A");
    rec.spec = {{ v: "B" }}; pushHistory(); rec.dirty = true;
    rec.savedSnap = JSON.stringify(rec.spec); rec.dirty = false;
    note("enregistrer B");
    travelHistory(-1); note("annuler vers A");
    rec.spec = {{ v: "C" }}; pushHistory(); rec.dirty = true; note("editer C");
    travelHistory(-1); note("annuler vers A");
    travelHistory(1); note("retablir C");
    console.log(JSON.stringify(trace));
    """
    import json

    node = subprocess.run(
        ["node", "-e", driver], capture_output=True, text=True, check=False
    )
    assert node.returncode == 0, node.stderr
    steps = dict(json.loads(node.stdout))
    assert steps["enregistrer B"] is False
    assert steps["annuler vers A"] is True
    # C n'a jamais été écrit : il ne peut pas être « enregistré ».
    assert steps["retablir C"] is True


def test_b21_les_noms_de_colonnes_ecartees_sont_balises_un_par_un():
    """Le séparateur était inséré *avant* `esc()`, qui échappait donc
    consciencieusement les balises destinées à être du HTML : l'écran affichait
    « a</code>, <code>b » en clair."""
    tests_js = (JS / "tests.js").read_text()
    assert 'esc(names.join("</code>, <code>"))' not in tests_js
    assert 'names.map((n) => `<code>${esc(n)}</code>`).join(", ")' in tests_js


def test_b20_une_seule_liste_de_modales_pour_echap():
    """Les raccourcis étaient écrits à deux endroits, et chacun en oubliait
    trois : Échap ne fermait ni « Renommer », ni « Documenter & tester », ni
    « Remplacer le SQL »."""
    modals = (JS / "modals.js").read_text()
    assert "FORMS" in modals
    assert 'e.key === "Escape"' in modals
    # Et plus nulle part ailleurs une liste d'identifiants de modales.
    for file in ("canvas.js", "recipe.js"):
        source = (JS / file).read_text()
        assert '["ds-scrim", "np-scrim"' not in source
        assert 'if ($("sql-scrim").classList.contains("on"))' not in source


def test_b20_chaque_modale_recoit_sa_semantique_et_son_focus():
    modals = (JS / "modals.js").read_text()
    for expected in (
        'role", "dialog"',
        "aria-modal",
        "aria-labelledby",
        "back.focus",
        'e.key !== "Tab"',
    ):
        assert expected in modals, expected


def test_b22_les_references_se_rafraichissent_a_chaque_changement_du_graphe():
    """`state.refs` n'était rempli qu'au démarrage, à la bascule de projet et
    après une déclaration de source : un modèle créé manquait aux menus, et un
    modèle renommé y restait sous son ancien nom."""
    assert "async function refreshRefs()" in (JS / "api.js").read_text()
    recipe = (JS / "recipe.js").read_text()
    flow = (JS / "flow.js").read_text()
    # L'enregistrement, le renommage et la suppression le rafraîchissent.
    assert recipe.count("refreshRefs(") >= 2
    assert flow.count("refreshRefs(") >= 2


def test_a33_le_flow_n_applique_que_la_derniere_reponse_demandee():
    """Deux rafraîchissements revenant dans le désordre réinstallaient l'ancien
    graphe, et ses replis étaient mémorisés sous le projet courant."""
    flow = (JS / "flow.js").read_text()
    assert "let flowGen = 0;" in flow
    assert "const gen = ++flowGen;" in flow
    assert "if (gen !== flowGen || stale()) return;" in flow


def test_a34_explorer_et_les_modales_de_resultats_verifient_leur_demande():
    """Ouvrir A puis B et recevoir A en dernier laissait le titre de B au-dessus
    des lignes de A."""
    assert "let exploreGen = 0;" in (JS / "explore.js").read_text()
    tests_js = (JS / "tests.js").read_text()
    assert "let dataGen = 0;" in tests_js
    assert "closeDataModal" in tests_js
    assert tests_js.count("if (stale()) return;") >= 4


def test_a35_le_diagnostic_compare_le_script_envoye_et_non_l_objet():
    """Modifier la jointure pendant la requête puis recevoir l'ancien
    diagnostic remettait `diagStale` à false : l'identité de `rec` ne protège
    pas de ses mutations internes."""
    editors = (JS / "editors.js").read_text()
    assert "rec.diagStale = JSON.stringify(rec.spec) !== sent;" in editors


def test_a36_l_apercu_ne_se_declare_a_jour_que_pour_le_script_envoye():
    """`previewSeq` n'avance qu'au lancement d'une requête : en mode manuel,
    modifier le script pendant un aperçu n'en lance aucune, et l'ancienne
    réponse remettait `stale` à false."""
    recipe = (JS / "recipe.js").read_text()
    assert "rec.stale = JSON.stringify({ spec: rec.spec, upto }) !== sent;" in recipe


def test_a38_toutes_les_sorties_de_recipe_passent_par_la_meme_garde():
    """Le bouton « retour » vérifiait `dirty` ; le fil d'Ariane, l'accueil,
    l'ouverture d'un autre projet et la fermeture de l'onglet ne le faisaient
    pas."""
    assert "async function leaveRecipe(" in (JS / "recipe.js").read_text()
    # La garde rend maintenant une promesse : une sortie qui ne l'attend pas
    # verrait un objet, toujours vrai, et partirait sans jamais poser la question.
    assert "await leaveRecipe()" in (JS / "api.js").read_text()
    assert "await leaveRecipe(" in (JS / "projects.js").read_text()
    assert "await leaveRecipe()" in (JS / "recipe.js").read_text()
    assert 'window.addEventListener("beforeunload"' in (JS / "recipe.js").read_text()


def test_aucune_question_n_est_posee_par_une_boite_du_navigateur():
    """`confirm()` / `alert()` / `prompt()` dessinent la boîte du système : sa
    police, son fond, et des boutons « OK / Annuler » qui ne nomment pas le
    geste. L'atelier a ses propres modales — `.scrim` / `.modal`, Échap, focus
    piégé et rendu — et toute question doit passer par elles."""
    natives = re.compile(r"(?<![\w.$])(confirm|alert|prompt)\s*\(")
    for f in [*MODULES, STATIC / "app.js"]:
        code = re.sub(r"/\*.*?\*/", "", f.read_text(), flags=re.S)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.M)
        assert not natives.search(code), f"boîte native du navigateur dans {f.name}"


def test_la_question_de_sortie_est_toujours_tranchee():
    """Une modale qui se ferme sans répondre laisse la promesse en l'air, et la
    sortie attend pour toujours. Échap et le changement de projet passent par
    le nettoyage : il répond « non »."""
    projects = (JS / "projects.js").read_text()
    assert '["quit-scrim", "Confirmation"]' in projects
    assert '"quit-scrim": () => answerConfirm(false)' in projects
    assert 'id="quit-scrim"' in (STATIC / "index.html").read_text()


def test_a08_le_formulaire_de_source_verifie_son_contexte_apres_chaque_await():
    """La réponse tardive d'un projet A réécrivait `dsState.tables` alors que
    `dsState.project` valait déjà B : le formulaire pouvait déclarer dans B les
    tables choisies dans l'inventaire de A."""
    projects_js = (JS / "projects.js").read_text()
    assert "const gen = ++dsState.gen;" in projects_js
    assert "const stale = () =>" in projects_js
    assert "dsState.gen += 1;" in projects_js
    assert "Le projet a changé depuis l'ouverture de ce formulaire" in projects_js


def test_a51_null_et_chaine_vide_ne_s_affichent_pas_pareil():
    """Les deux valeurs se filtrent et s'agrègent différemment en SQL ; leur
    confusion est particulièrement gênante dans un atelier de nettoyage."""
    explore = (JS / "explore.js").read_text()
    assert 'td.textContent = "(vide)"' in explore
    assert "chaîne d'espaces" in explore


def test_a52_une_liste_de_valeurs_a_un_champ_par_valeur():
    """`split(",")` puis `trim` rendait impossible de saisir une valeur qui
    contient une virgule, une chaîne vide ou des espaces significatifs — et
    rééditer une configuration existante l'abîmait."""
    # Dans l'interface entière, et non dans `recipe.js` : l'éditeur de listes
    # est devenu un widget partagé, comme le dit l'en-tête de ce fichier.
    assert "function valuesEditor(" in APP_JS
    assert 'inp.value.split(",")' not in APP_JS


def test_b02_l_ecran_n_offre_pas_de_borne_sur_une_recipe_sql():
    # Dans l'interface entière : la configuration de sortie a son module.
    assert 'else if (rec.spec.type === "sql")' in APP_JS
    assert "is_incremental()" in APP_JS


def test_a11_les_strategies_offertes_sont_celles_de_l_entrepot():
    assert "default_incremental_strategy" in (JS / "projects.js").read_text()
    assert "indisponible sur cet entrepôt" in APP_JS


def test_a40_la_reconnexion_compare_projet_et_run():
    """`hello` ne contenait ni identité de projet, ni fraîcheur : un onglet qui
    manquait `project_changed` ou `run_done` restait sur une vue périmée."""
    runs = (JS / "runs.js").read_text()
    assert "async function resync(" in runs
    assert "m.generation" in runs
    routes = (
        Path(__file__).resolve().parent.parent / "pliq" / "server" / "routes_runs.py"
    ).read_text()
    assert '"generation": a.generation' in routes


def test_a58_la_barre_du_haut_transmet_exclude_et_full_refresh():
    """La table de couverture du README les annonçait ; `runDbt` ne
    transmettait que la commande et le sélecteur."""
    runs = (JS / "runs.js").read_text()
    assert "exclude, full_refresh: full" in runs
    assert 'id="exclude-input"' in (STATIC / "index.html").read_text()
    assert 'id="full-refresh"' in (STATIC / "index.html").read_text()
