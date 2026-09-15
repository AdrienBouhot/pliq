"""La documentation servie par l'atelier, et ce qui la tient droite.

Une documentation ne casse jamais bruyamment : un lien mort, une ancre
renommée, un processeur ajouté et jamais décrit se lisent comme du texte
normal. Ces contrôles sont donc l'équivalent, pour `/doc`, de ce que
`test_frontend.py` fait pour l'interface — l'accord entre ce qui est écrit et
ce qui existe.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

import pytest

from pliq.recipes.processors import PROCESSORS

STATIC = Path(__file__).resolve().parent.parent / "pliq" / "static"
DOC = STATIC / "doc"
PAGES = DOC / "pages"

SUMMARY = json.loads((DOC / "sommaire.json").read_text())
INPUTS = [p for section in SUMMARY for p in section["pages"]]
SLUGS = [p["slug"] for p in INPUTS]

# Les liens Markdown : `[texte](cible)`. Le lecteur n'en connaît pas d'autre
# forme, et ces contrôles non plus.
LINK = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)")
TITLE = re.compile(r"^(#{1,5})\s+(.*)$", re.M)


def anchor(text: str) -> str:
    """L'identifiant d'un titre — le jumeau Python d'`anchor` dans `markdown.js`.

    Les deux doivent rendre la même chaîne : c'est ce qui permet de vérifier
    ici qu'un lien interne désigne un titre qui existe vraiment.
    """
    without_accents = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    without_markers = re.sub(r"[`*]", "", without_accents.lower())
    return re.sub(r"[^a-z0-9]+", "-", without_markers).strip("-")


def text_of(slug: str) -> str:
    return (PAGES / f"{slug}.md").read_text()


def anchors_of(slug: str) -> set[str]:
    return {anchor(t) for _, t in TITLE.findall(text_of(slug))}


# --------------------------------------------------------------- la coquille


def test_la_documentation_a_ses_fichiers():
    for f in ("index.html", "doc.css", "doc.js", "markdown.js", "sommaire.json"):
        assert (DOC / f).is_file(), f


def test_la_coquille_ne_reference_que_des_fichiers_existants():
    html = (DOC / "index.html").read_text()
    for src in re.findall(r'(?:src|href)="/static/([^"]+)"', html):
        assert (STATIC / src).is_file(), src


def test_la_documentation_ne_depend_d_aucun_serveur_exterieur():
    """Elle est lue devant un entrepôt, souvent hors ligne, comme l'atelier."""
    external = []
    for file in (DOC / "index.html", DOC / "doc.css", DOC / "doc.js"):
        for url in re.findall(r'https?://[^\s"\')]+', file.read_text()):
            external.append(f"{file.name} : {url}")
    assert not external, f"ressources chargées depuis l'extérieur : {external}"


def test_la_palette_est_partagee_et_non_recopiee():
    """Une couleur n'existe qu'à un endroit.

    La documentation emploie la charte de l'atelier. Tant qu'elle charge
    `palette.css`, une teinte ajustée l'est partout ; une copie aurait dérivé
    au premier ajustement.
    """
    palette = (STATIC / "palette.css").read_text()
    assert "--brand-navy" in palette and "--brand-orange" in palette
    for page in ((STATIC / "index.html"), (DOC / "index.html")):
        assert "/static/palette.css" in page.read_text(), page
    # La feuille de l'atelier ne redéclare pas ce que la palette porte déjà.
    assert "--brand-navy:" not in (STATIC / "app.css").read_text()


def test_le_point_d_interrogation_ouvre_la_documentation():
    """Le `?` du bandeau est la seule porte d'entrée : il doit exister."""
    html = (STATIC / "index.html").read_text()
    button = re.search(r'<a[^>]*id="btn-doc"[^>]*>', html)
    assert button, "le bouton « ? » a disparu du bandeau"
    assert 'href="/doc"' in button.group(0)
    assert 'target="_blank"' in button.group(0), "la doc s'ouvre dans un onglet à part"
    server = (STATIC.parent / "server" / "__init__.py").read_text()
    assert '@app.get("/doc")' in server, "rien ne sert la documentation"


# ----------------------------------------------------------------- sommaire


def test_le_sommaire_et_les_pages_se_correspondent():
    """Ni page orpheline, ni entrée qui ne mène nulle part."""
    on_disk = {f.stem for f in PAGES.glob("*.md")}
    announced = set(SLUGS)
    assert announced - on_disk == set(), "annoncées au sommaire, absentes"
    assert on_disk - announced == set(), "sur le disque, hors du sommaire"


def test_le_sommaire_n_a_pas_de_doublon():
    assert len(SLUGS) == len(set(SLUGS))


def test_l_accueil_est_la_premiere_page():
    """`/doc` sans paramètre affiche `index` : il doit ouvrir le sommaire."""
    assert SLUGS[0] == "index"


@pytest.mark.parametrize("slug", SLUGS)
def test_chaque_page_commence_par_son_titre(slug: str):
    first = TITLE.search(text_of(slug))
    assert first and first.group(1) == "#", f"{slug} n'a pas de titre de niveau 1"
    assert text_of(slug).startswith("# "), f"{slug} : le titre doit ouvrir la page"


# -------------------------------------------------------------------- liens


@pytest.mark.parametrize("slug", SLUGS)
def test_les_liens_internes_menent_quelque_part(slug: str):
    dead = []
    for target in LINK.findall(text_of(slug)):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if target.startswith("#"):
            if target[1:] not in anchors_of(slug):
                dead.append(target)
            continue
        page, _, fragment = target.partition("#")
        assert page.endswith(".md"), f"{slug} → {target} : un lien de page finit en .md"
        targets_it = page[:-3]
        if targets_it not in SLUGS:
            dead.append(target)
        elif fragment and fragment not in anchors_of(targets_it):
            dead.append(target)
    assert not dead, f"{slug} : liens morts {dead}"


def test_toutes_les_pages_sont_atteignables_depuis_une_autre():
    """Une page qu'aucune autre ne cite n'existe que dans la barre latérale.

    Le sommaire suffit à l'atteindre, mais une page que rien n'amène est
    presque toujours une page qu'on a oublié de relier à son voisinage.
    """
    cited = set()
    for slug in SLUGS:
        for target in LINK.findall(text_of(slug)):
            page = target.partition("#")[0]
            if page.endswith(".md") and page[:-3] != slug:
                cited.add(page[:-3])
    orphans = set(SLUGS) - cited - {"index"}
    assert not orphans, f"aucune page ne mène à : {sorted(orphans)}"


# ------------------------------------------------- la syntaxe que le lecteur lit

FENCED = {"note", "astuce", "attention"}


@pytest.mark.parametrize("slug", SLUGS)
def test_les_pages_n_emploient_que_la_syntaxe_du_lecteur(slug: str):
    """Le lecteur ne comprend qu'un sous-ensemble, et l'ignore en silence.

    Une balise HTML serait échappée et s'afficherait telle quelle ; une
    citation ordinaire passerait pour un paragraphe. Mieux vaut le dire ici
    que le découvrir sur la page.
    """
    text = text_of(slug)
    # Ni les blocs de code, ni les codes en ligne : `<nom>.sql` est un nom de
    # fichier, et le lecteur l'échappe comme tout ce qui est entre accents
    # graves.
    outside_code = re.sub(r"```.*?```", "", text, flags=re.S)
    outside_code = re.sub(r"`[^`]*`", "", outside_code)

    tags = re.findall(r"</?[a-z][a-z0-9]*(?:\s[^>]*)?>", outside_code)
    assert not tags, f"{slug} : HTML dans une page Markdown {tags}"

    for line in outside_code.splitlines():
        if not line.startswith(">"):
            continue
        marker = re.match(r">\s*\[!(\w+)\]", line)
        assert marker, f"{slug} : citation qui n'est pas un encadré — {line[:60]}"
        kind = marker.group(1)
        assert kind in FENCED, f"{slug} : encadré inconnu {kind}"


@pytest.mark.parametrize("slug", SLUGS)
def test_les_tableaux_ont_leur_ligne_de_tirets(slug: str):
    """Sans elle, le lecteur ne voit pas un tableau mais des paragraphes."""
    lines = text_of(slug).splitlines()
    for n, line in enumerate(lines):
        if not re.match(r"^\s*\|.+\|\s*$", line):
            continue
        before = lines[n - 1] if n else ""
        after = lines[n + 1] if n + 1 < len(lines) else ""
        in_a_table = re.match(r"^\s*\|", before) or re.match(r"^\s*\|", after)
        assert in_a_table, f"{slug} : ligne de tableau isolée — {line[:50]}"


# ------------------------------------------------ l'accord avec l'atelier

# Chaque famille de processeurs a sa page ; le nom du fichier se déduit de la
# catégorie, comme la bibliothèque la nomme.
PAGE_BY_CATEGORY = {
    "Colonnes": "processeurs-colonnes",
    "Formules": "processeurs-formules",
    "Filtrage": "processeurs-filtrage",
    "Nettoyage": "processeurs-nettoyage",
    "Nombres": "processeurs-nombres",
    "Dates": "processeurs-dates",
    "Lignes": "processeurs-lignes",
}


@pytest.mark.parametrize("key", sorted(PROCESSORS))
def test_chaque_processeur_a_sa_section(key: str):
    """Un processeur ajouté sans un paragraphe est un processeur que personne
    ne trouvera. Le libellé de la bibliothèque est le titre de la section : les
    deux ne peuvent donc pas diverger sans que ce test le dise."""
    proc = PROCESSORS[key]
    page = PAGE_BY_CATEGORY.get(proc["category"])
    assert page, f"famille sans page de documentation : {proc['category']}"
    titles = [t for _, t in TITLE.findall(text_of(page))]
    assert proc["label"] in titles, f"{key} : « {proc['label']}» absent de {page}.md"


def test_la_page_des_processeurs_annonce_les_sept_familles():
    families_summary = text_of("processeurs")
    for category, page in PAGE_BY_CATEGORY.items():
        assert f"({page}.md)" in families_summary, category


# -------------------------------------------------------- le lecteur Markdown


def _render_md(markdown: str) -> dict:
    """Le lecteur, exécuté sous node : c'est lui qui tourne dans le navigateur."""
    source = json.dumps(markdown)
    reader = json.dumps((DOC / "markdown.js").as_uri())
    driver = f"""
    const {{ render }} = await import({reader});
    const out = render({source});
    console.log(JSON.stringify({{ html: out.html, headings: out.headings }}));
    """
    node = subprocess.run(
        ["node", "--input-type=module", "-e", driver],
        capture_output=True,
        text=True,
        check=False,
    )
    assert node.returncode == 0, node.stderr
    return json.loads(node.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node absent")


@needs_node
@pytest.mark.parametrize("module", ["doc.js", "markdown.js"], ids=lambda n: n)
def test_chaque_module_de_la_documentation_est_syntaxiquement_valide(module: str):
    """Les modules de `/doc` ne sont pas ceux de l'atelier, et
    `test_frontend.py` ne les voit donc pas. Une erreur de syntaxe s'y
    traduirait par une page blanche, sans rien dans les tests."""
    node = subprocess.run(
        ["node", "--input-type=module", "--check"],
        input=(DOC / module).read_text(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert node.returncode == 0, f"{module} : {node.stderr}"


@needs_node
def test_le_lecteur_rend_les_blocs_de_la_documentation():
    output = _render_md(
        "# Titre\n\n"
        "## Une **section**\n\n"
        "Du texte avec `du code` et un [lien](flow.md#ancre).\n\n"
        "- premier\n- second\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "> [!attention] Ceci est un avertissement.\n\n"
        "```sql\nselect 1\n```\n"
    )
    html = output["html"]
    assert '<h2 id="une-section">' in html
    assert "<b>section</b>" in html
    assert '<a href="flow.md#ancre">lien</a>' in html
    assert "<ul><li>premier</li><li>second</li></ul>" in html
    assert "<th>a</th>" in html and "<td>2</td>" in html
    assert 'class="aside attention"' in html and "<b>Attention</b>" in html
    assert '<pre class="code" data-langue="sql">' in html
    assert [t["level"] for t in output["headings"]] == [1, 2]


@needs_node
def test_le_lecteur_echappe_ce_qu_il_rend():
    """Aucune page n'est censée contenir de HTML — le lecteur ne le suppose pas."""
    page = "# T\n\nUn <script>alert(1)</script> et une & esperluette.\n"
    html = _render_md(page)["html"]
    assert "<script>" not in html
    assert "&lt;script&gt;" in html and "&amp;" in html


@needs_node
def test_le_code_en_ligne_protege_ce_qu_il_contient():
    """`**` dans un `code` n'est pas du gras, et `|` ne coupe pas de cellule."""
    html = _render_md("# T\n\nOn écrit `a ** b` et `a | b`.\n")["html"]
    assert "<code>a ** b</code>" in html and "<code>a | b</code>" in html
    assert "<b>" not in html


@needs_node
def test_les_ancres_des_titres_sont_celles_que_les_liens_visent():
    """Le calcul d'ancre existe deux fois — en JS et dans ce fichier. S'ils
    divergent, tous les liens vers un paragraphe tombent à côté en silence."""
    examples = [
        "Dédoublonner par clé",
        "L'aperçu a un coût, et il l'annonce",
        "Ce que l'enregistrement vérifie avant d'écrire",
        "`dbt deps`",
    ]
    output = _render_md("# T\n\n" + "\n\n".join(f"## {t}" for t in examples))
    ids = [t["id"] for t in output["headings"] if t["level"] == 2]
    assert ids == [anchor(t) for t in examples]
