"""Le Flow : manifest dbt → graphe biparti dataset / recipe / dataset."""

from __future__ import annotations

from pliq import graph

from .conftest import FakeManifest, fake_node


def _manifest() -> FakeManifest:
    return FakeManifest(
        nodes=[
            fake_node(
                "seed.demo.raw_customers",
                "raw_customers",
                resource_type="seed",
                path="seeds/raw_customers.csv",
            ),
            fake_node(
                "model.demo.stg_orders",
                "stg_orders",
                path="models/staging/stg_orders.sql",
                depends_on=("source.demo.raw.raw_orders",),
                raw_code="select * from {{ source('raw', 'raw_orders') }}",
            ),
            fake_node(
                "model.demo.stg_customers",
                "stg_customers",
                path="models/staging/stg_customers.sql",
                depends_on=("seed.demo.raw_customers",),
            ),
            fake_node(
                "model.demo.customer_orders",
                "customer_orders",
                path="models/marts/customer_orders.sql",
                materialized="table",
                depends_on=("model.demo.stg_orders", "model.demo.stg_customers"),
                raw_code="select * from a left join b on a.id = b.id",
                tags=("finance",),
                columns=("customer_id",),
            ),
            fake_node(
                "test.demo.unique_customer_orders_customer_id",
                "unique_customer_orders_customer_id",
                resource_type="test",
                depends_on=("model.demo.customer_orders",),
                column_name="customer_id",
                test_name="unique",
            ),
            fake_node("analysis.demo.x", "x", resource_type="analysis"),
        ],
        sources=[
            fake_node(
                "source.demo.raw.raw_orders",
                "raw_orders",
                resource_type="source",
                source_name="raw",
                path="models/staging/sources.yml",
            )
        ],
    )


def test_chaque_modele_a_sa_recipe_entre_ses_entrees_et_sa_sortie():
    g = graph.collect(_manifest())
    recipes = {r["model_name"]: r for r in g["recipes"]}
    assert set(recipes) == {"stg_orders", "stg_customers", "customer_orders"}

    rid = recipes["customer_orders"]["id"]
    incoming = {e["from"] for e in g["edges"] if e["to"] == rid}
    outgoing = {e["to"] for e in g["edges"] if e["from"] == rid}
    assert incoming == {"model.demo.stg_orders", "model.demo.stg_customers"}
    assert outgoing == {"model.demo.customer_orders"}


def test_seeds_sources_tests_et_analyses_ne_sont_pas_des_recipes():
    g = graph.collect(_manifest())
    ids = {d["id"] for d in g["datasets"]}
    assert "seed.demo.raw_customers" in ids
    assert "source.demo.raw.raw_orders" in ids
    assert "test.demo.unique_customer_orders_customer_id" not in ids
    assert "analysis.demo.x" not in ids
    assert all(
        r["model_name"] not in ("raw_customers", "raw_orders") for r in g["recipes"]
    )


def test_les_tests_sont_rattaches_a_leur_dataset():
    g = graph.collect(_manifest())
    target = next(d for d in g["datasets"] if d["name"] == "customer_orders")
    assert target["tests"] == [
        {
            "id": "test.demo.unique_customer_orders_customer_id",
            "name": "unique",
            "column": "customer_id",
            "severity": "error",
        }
    ]


def test_le_type_d_une_recipe_ecrite_a_la_main_est_devine():
    g = graph.collect(_manifest())
    recipes = {r["model_name"]: r for r in g["recipes"]}
    assert recipes["customer_orders"]["recipe_type"] == "join"
    assert recipes["customer_orders"]["managed"] is False
    assert recipes["customer_orders"]["steps"] == 0
    assert recipes["customer_orders"]["label"] == "Joindre"


def test_une_recipe_gerée_par_l_atelier_annonce_ses_etapes():
    specs = {
        "customer_orders": {
            "type": "prepare",
            "steps": [
                {"type": "round", "enabled": True},
                {"type": "sort", "enabled": False},
                {"type": "sort", "enabled": True},
            ],
        }
    }
    g = graph.collect(_manifest(), specs)
    recipe = next(r for r in g["recipes"] if r["model_name"] == "customer_orders")
    assert recipe["managed"] is True
    assert recipe["recipe_type"] == "prepare"
    assert recipe["steps"] == 2, "les étapes désactivées ne comptent pas"


def test_les_couches_et_le_type_de_chaque_dataset():
    by_name = {d["name"]: d for d in graph.collect(_manifest())["datasets"]}
    assert by_name["raw.raw_orders"]["layer"] == "source"
    assert by_name["raw_customers"]["layer"] == "source"
    assert by_name["stg_orders"]["layer"] == "staging"
    assert by_name["customer_orders"]["layer"] == "marts"
    assert by_name["customer_orders"]["materialized"] == "table"
    assert by_name["raw_customers"]["materialized"] == "seed"
    assert by_name["customer_orders"]["editable"] is True
    assert by_name["raw_customers"]["editable"] is False
    assert by_name["customer_orders"]["tags"] == ["finance"]


def test_un_modele_de_paquet_se_voit_mais_ne_s_edite_pas():
    """Son `original_file_path` est relatif à *son* paquet.

    Résolu depuis le projet ouvert, `models/orders.sql` d'un paquet désigne le
    modèle local du même nom : l'ouvrir montrait le mauvais fichier, et
    l'enregistrer l'écrasait. Le Flow le montre donc, et le dit non éditable.
    """
    nodes = list(_manifest().nodes.values())
    foreign = fake_node("model.vendor.orders", "orders", path="models/orders.sql")
    foreign.package_name = "vendor"
    g = graph.collect(FakeManifest([*nodes, foreign]), project="demo")
    by_name = {d["name"]: d for d in g["datasets"]}

    assert by_name["orders"]["external"] is True
    assert by_name["orders"]["package"] == "vendor"
    assert by_name["orders"]["editable"] is False
    # Le projet ouvert, lui, garde la main sur les siens.
    assert by_name["customer_orders"]["external"] is False
    assert by_name["customer_orders"]["editable"] is True


def test_sans_nom_de_projet_tout_appartient_au_projet():
    """L'appelant qui n'a pas la question à poser retrouve l'état d'avant."""
    nodes = list(_manifest().nodes.values())
    foreign = fake_node("model.vendor.orders", "orders", path="models/orders.sql")
    foreign.package_name = "vendor"
    g = graph.collect(FakeManifest([*nodes, foreign]))
    by_name = {d["name"]: d for d in g["datasets"]}
    assert by_name["orders"]["editable"] is True


# ------------------------------------- l'entrée de recipe et son vrai nœud


def _package(name: str, uid: str, package: str, **kw):
    node = fake_node(uid, name, path=f"models/{name}.sql", **kw)
    node.package_name = package
    return node


def test_une_entree_sans_paquet_designe_le_modele_du_projet():
    """C'est ce que `ref('orders')` résout : le projet ouvert d'abord."""
    m = FakeManifest(
        [
            _package("orders", "model.demo.orders", "demo"),
            _package("orders", "model.vendor.orders", "vendor"),
        ]
    )
    node = graph.input_node_id(m, {"ref": "orders"}, "demo")
    assert node.unique_id == "model.demo.orders"


def test_une_entree_qui_nomme_son_paquet_designe_celui_du_paquet():
    """La sélection faite à l'écran doit survivre jusqu'au nœud."""
    m = FakeManifest(
        [
            _package("orders", "model.demo.orders", "demo"),
            _package("orders", "model.vendor.orders", "vendor"),
        ]
    )
    node = graph.input_node_id(m, {"ref": "orders", "package": "vendor"}, "demo")
    assert node.unique_id == "model.vendor.orders"


def test_une_entree_sans_paquet_trouve_quand_meme_un_modele_de_paquet():
    """dbt résout le projet d'abord, les paquets ensuite — et nous aussi.

    Les recipes écrites avant que l'identité soit portée ne nomment aucun
    paquet. Exiger le paquet aurait fait disparaître de la résolution les
    modèles que seul un paquet déclare : colonnes, diagnostics et suggestion
    de couche les auraient ignorés en silence.
    """
    m = FakeManifest([_package("utils_dates", "model.vendor.utils_dates", "vendor")])
    node = graph.input_node_id(m, {"ref": "utils_dates"}, "demo")
    assert node.unique_id == "model.vendor.utils_dates"


def test_sans_version_demandee_c_est_la_derniere_qui_repond():
    """`ref('customers')` rend la dernière version ; la résolution aussi."""
    v1 = _package("customers", "model.demo.customers.v1", "demo")
    v2 = _package("customers", "model.demo.customers.v2", "demo")
    v1.version, v1.latest_version = 1, 2
    v2.version, v2.latest_version = 2, 2
    m = FakeManifest([v1, v2])

    assert graph.input_node_id(m, {"ref": "customers"}, "demo").unique_id == (
        "model.demo.customers.v2"
    )
    assert (
        graph.input_node_id(m, {"ref": "customers", "version": "1"}, "demo").unique_id
        == "model.demo.customers.v1"
    )


def test_une_version_sans_latest_version_declaree_prend_la_plus_haute():
    """`latest_version` est normalement renseigné ; s'il manque, on ne rend
    pas None pour autant."""
    v1 = _package("customers", "model.demo.customers.v1", "demo")
    v2 = _package("customers", "model.demo.customers.v2", "demo")
    v1.version, v2.version = 1, 2
    m = FakeManifest([v1, v2])
    assert graph.input_node_id(m, {"ref": "customers"}, "demo").unique_id == (
        "model.demo.customers.v2"
    )


def test_une_version_absente_du_projet_ne_bascule_pas_sur_un_paquet():
    """Le repli porte sur le nom, pas sur la version.

    Le projet déclare `customers` en v1 seulement, un paquet en v2. Demander la
    v2 ne doit pas rendre celle du paquet : ce serait la confusion d'identité
    que le paquet porté dans l'entrée sert justement à fermer.
    """
    local = _package("customers", "model.demo.customers.v1", "demo")
    local.version, local.latest_version = 1, 1
    foreign = _package("customers", "model.vendor.customers.v2", "vendor")
    foreign.version, foreign.latest_version = 2, 2
    m = FakeManifest([local, foreign])

    assert graph.input_node_id(m, {"ref": "customers", "version": "2"}, "demo") is None
    # Sans version, c'est bien celle du projet qui répond.
    assert graph.input_node_id(m, {"ref": "customers"}, "demo").unique_id == (
        "model.demo.customers.v1"
    )


def test_une_entree_introuvable_ne_rend_rien():
    m = FakeManifest([_package("orders", "model.demo.orders", "demo")])
    assert graph.input_node_id(m, {"ref": "jamais_vue"}, "demo") is None
    assert (
        graph.input_node_id(m, {"ref": "orders", "package": "vendor"}, "demo") is None
    )


def test_la_profondeur_ordonne_le_flux_de_gauche_a_droite():
    by_name = {d["name"]: d for d in graph.collect(_manifest())["datasets"]}
    assert by_name["raw.raw_orders"]["depth"] == 0
    assert by_name["stg_orders"]["depth"] == 1
    assert by_name["customer_orders"]["depth"] == 2
    assert (
        by_name["raw.raw_orders"]["cx"]
        < by_name["stg_orders"]["cx"]
        < by_name["customer_orders"]["cx"]
    )


def test_la_recipe_se_pose_entre_ses_entrees_et_sa_sortie():
    g = graph.collect(_manifest())
    output = next(d for d in g["datasets"] if d["name"] == "customer_orders")
    recipe = next(r for r in g["recipes"] if r["model_name"] == "customer_orders")
    assert recipe["cx"] < output["cx"] and recipe["cy"] == output["cy"]


def test_le_canevas_a_une_taille_utilisable():
    g = graph.collect(_manifest())
    assert g["width"] >= 560 and g["height"] >= 300
    assert all(d["cx"] > 0 and d["cy"] > 0 for d in g["datasets"])


def test_un_manifest_vide_ne_plante_pas():
    g = graph.collect(FakeManifest())
    assert g["datasets"] == [] and g["recipes"] == [] and g["edges"] == []
    assert g["width"] >= 560


def test_un_cycle_de_dependances_ne_boucle_pas_indefiniment():
    """Le manifest ne devrait pas en contenir, mais le Flow ne doit pas geler."""
    manifest = FakeManifest(
        nodes=[
            fake_node("model.demo.a", "a", depends_on=("model.demo.b",)),
            fake_node("model.demo.b", "b", depends_on=("model.demo.a",)),
        ]
    )
    g = graph.collect(manifest)
    assert len(g["datasets"]) == 2


def test_un_modele_qui_se_reference_lui_meme_est_ignore():
    manifest = FakeManifest(
        nodes=[
            fake_node("model.demo.a", "a", depends_on=("model.demo.a",)),
        ]
    )
    g = graph.collect(manifest)
    assert g["datasets"][0]["depth"] == 0
    assert not any(
        e["from"] == "model.demo.a" and e["to"] == "model.demo.a" for e in g["edges"]
    )


def _far_join_manifest() -> FakeManifest:
    """customer_orders_joined joint son parent direct et un dataset deux colonnes
    plus à gauche — le cas où l'arête longue se cachait derrière les nœuds."""
    return FakeManifest(
        nodes=[
            fake_node("model.demo.customer_orders", "customer_orders"),
            fake_node(
                "model.demo.customer_orders_prepared",
                "customer_orders_prepared",
                depends_on=("model.demo.customer_orders",),
            ),
            fake_node(
                "model.demo.customer_orders_joined",
                "customer_orders_joined",
                depends_on=(
                    "model.demo.customer_orders",
                    "model.demo.customer_orders_prepared",
                ),
            ),
        ]
    )


def test_une_arete_qui_enjambe_une_colonne_contourne_les_noeuds():
    g = graph.collect(_far_join_manifest())
    recipe = next(
        r for r in g["recipes"] if r["model_name"] == "customer_orders_joined"
    )

    incoming = [e for e in g["edges"] if e["to"] == recipe["id"]]
    assert len(incoming) == 2, "les deux entrées de la jointure doivent être tracées"

    far_edge = next(e for e in incoming if e["from"] == "model.demo.customer_orders")
    short = next(
        e for e in incoming if e["from"] == "model.demo.customer_orders_prepared"
    )
    assert far_edge["via"] is not None, "l'arête longue passerait derrière les nœuds"
    assert short.get("via") is None, "une arête entre colonnes voisines reste droite"

    # La voie choisie est bien dégagée de ce qu'elle enjambe.
    hovered = [
        graph._box(n)
        for n in g["datasets"] + g["recipes"]
        if n["id"] not in (far_edge["from"], far_edge["to"])
    ]
    start_point = next(d for d in g["datasets"] if d["id"] == far_edge["from"])
    between = [b for b in hovered if b[2] > start_point["cx"] and b[0] < recipe["cx"]]
    assert between, "ce test n'a de sens que s'il y a des nœuds sur le trajet"
    assert all(
        far_edge["via"] < b[1] or far_edge["via"] > b[3] for b in between
    ), "la voie traverse encore un nœud"


def test_les_aretes_courtes_restent_droites():
    g = graph.collect(_manifest())
    assert all(e.get("via") is None for e in g["edges"])


# ------------------------------------------------------------ zones repliées


def test_chaque_dataset_annonce_la_zone_dont_il_vient():
    g = graph.collect(_manifest())
    by_name = {d["name"]: d for d in g["datasets"]}
    assert by_name["stg_orders"]["zone"] == "staging"
    assert by_name["customer_orders"]["zone"] == "marts"
    assert by_name["raw_customers"]["zone"] == "seeds"
    assert by_name["raw.raw_orders"]["zone"] == "sources"

    zones = {z["name"]: z for z in g["zones"]}
    assert zones["staging"]["count"] == 2
    assert all(z["folded"] is False for z in g["zones"])


def test_replier_une_zone_la_remplace_par_un_seul_noeud():
    g = graph.collect(_manifest(), folded=["staging"])
    names = {d["name"] for d in g["datasets"]}
    assert "stg_orders" not in names and "stg_customers" not in names

    zone = next(d for d in g["datasets"] if d["node_type"] == "group")
    assert zone["name"] == "staging" and zone["count"] == 2
    assert zone["members"] == ["model.demo.stg_customers", "model.demo.stg_orders"]

    # Les recipes des modèles repliés disparaissent avec eux : la zone tient
    # lieu de transformation.
    assert {r["model_name"] for r in g["recipes"]} == {"customer_orders"}
    assert next(z for z in g["zones"] if z["name"] == "staging")["folded"] is True
    assert g["folded"] == ["zone:staging"]


def test_une_zone_repliee_herite_des_aretes_de_ce_qu_elle_cache():
    g = graph.collect(_manifest(), folded=["staging"])
    zid = graph.zone_id("staging")
    rid = next(r["id"] for r in g["recipes"] if r["model_name"] == "customer_orders")

    incoming = {e["from"] for e in g["edges"] if e["to"] == zid}
    assert incoming == {"source.demo.raw.raw_orders", "seed.demo.raw_customers"}
    # customer_orders lisait les deux modèles de staging : il ne lit plus qu'eux
    # sous la forme d'une seule entrée, et sans doublon.
    assert [e["from"] for e in g["edges"] if e["to"] == rid] == [zid]


def test_la_disposition_est_recalculee_pour_le_graphe_replie():
    """Replier n'est pas masquer : les colonnes valent pour ce qui reste."""
    g = graph.collect(_manifest(), folded=["staging"])
    by_id = {d["id"]: d for d in g["datasets"]}
    zone = by_id[graph.zone_id("staging")]
    source = by_id["source.demo.raw.raw_orders"]
    output = by_id["model.demo.customer_orders"]
    assert source["depth"] == 0 and zone["depth"] == 1 and output["depth"] == 2
    assert source["cx"] < zone["cx"] < output["cx"]


def test_replier_une_zone_inconnue_ne_change_rien():
    full = graph.collect(_manifest())
    g = graph.collect(_manifest(), folded=["zone_qui_n_existe_pas", ""])
    assert g["folded"] == []
    expected = [d["name"] for d in full["datasets"]]
    assert [d["name"] for d in g["datasets"]] == expected


def test_tout_replier_laisse_un_graphe_de_zones():
    zones = [z["name"] for z in graph.collect(_manifest())["zones"]]
    g = graph.collect(_manifest(), folded=zones)
    assert g["recipes"] == []
    assert all(d["node_type"] == "group" for d in g["datasets"])
    assert {d["name"] for d in g["datasets"]} == set(zones)
    # Les arêtes restantes relient des zones entre elles, et rien d'autre.
    ids = {d["id"] for d in g["datasets"]}
    assert g["edges"] and all(e["from"] in ids and e["to"] in ids for e in g["edges"])


# --------------------------------------------------- replier l'amont / l'aval


def test_chaque_dataset_annonce_ce_que_replierait_son_amont_et_son_aval():
    """Les comptes servent au menu du clic droit : ils portent sur tout le graphe."""
    by_name = {d["name"]: d for d in graph.collect(_manifest())["datasets"]}
    assert by_name["raw.raw_orders"]["up_count"] == 0
    assert by_name["raw.raw_orders"]["down_count"] == 2  # stg_orders, customer_orders
    assert by_name["customer_orders"]["up_count"] == 4
    assert by_name["customer_orders"]["down_count"] == 0
    assert by_name["stg_orders"]["up_count"] == 1


def test_replier_l_amont_d_un_noeud_en_fait_un_bloc():
    g = graph.collect(_manifest(), folded=["up:model.demo.customer_orders"])
    assert g["folded"] == ["up:model.demo.customer_orders"]

    block = next(d for d in g["datasets"] if d["node_type"] == "group")
    assert block["name"] == "amont de customer_orders"
    assert block["count"] == 4 and block["fold_kind"] == "up"
    # Le nœud d'ancrage reste dehors : c'est lui qu'on regarde.
    assert [d["name"] for d in g["datasets"] if d["node_type"] == "dataset"] == [
        "customer_orders"
    ]

    rid = next(r["id"] for r in g["recipes"] if r["model_name"] == "customer_orders")
    assert [e["from"] for e in g["edges"] if e["to"] == rid] == [block["id"]]


def test_replier_l_aval_d_un_noeud_en_fait_un_bloc():
    g = graph.collect(_manifest(), folded=["down:source.demo.raw.raw_orders"])
    block = next(d for d in g["datasets"] if d["node_type"] == "group")
    assert block["name"] == "aval de raw.raw_orders"
    assert block["fold_kind"] == "down"
    assert set(block["member_names"]) == {"stg_orders", "customer_orders"}
    # raw_customers n'est pas en aval de raw_orders : il reste visible.
    remaining = {d["name"] for d in g["datasets"] if d["node_type"] == "dataset"}
    assert remaining == {"raw.raw_orders", "raw_customers", "stg_customers"}


def test_un_dataset_ne_tombe_que_dans_un_seul_bloc():
    """Deux replis se recouvrent souvent : le premier servi garde le nœud."""
    g = graph.collect(
        _manifest(),
        folded=["up:model.demo.stg_orders", "up:model.demo.customer_orders"],
    )
    blocks = [d for d in g["datasets"] if d["node_type"] == "group"]
    assert [b["name"] for b in blocks] == [
        "amont de stg_orders",
        "amont de customer_orders",
    ]
    seen = [u for b in blocks for u in b["members"]]
    assert len(seen) == len(set(seen))
    assert "source.demo.raw.raw_orders" in blocks[0]["members"]
    assert "source.demo.raw.raw_orders" not in blocks[1]["members"]


def test_replier_l_amont_d_un_noeud_deja_replie_est_sans_effet():
    g = graph.collect(
        _manifest(),
        folded=["up:model.demo.customer_orders", "up:model.demo.stg_orders"],
    )
    assert g["folded"] == ["up:model.demo.customer_orders"]


def test_replier_l_amont_d_une_source_ne_cree_pas_de_bloc_vide():
    g = graph.collect(_manifest(), folded=["up:source.demo.raw.raw_orders"])
    assert g["folded"] == []
    assert not any(d["node_type"] == "group" for d in g["datasets"])


def test_un_repli_qui_ne_veut_rien_dire_est_ignore():
    for spec in ("up:model.demo.inconnu", "sideways:model.demo.stg_orders", ":", "up:"):
        assert graph.collect(_manifest(), folded=[spec])["folded"] == []


def test_chaque_zone_annonce_sa_nature():
    """`models/marts/` se dit « dossier », pas les seeds ni les sources."""
    by_name = {z["name"]: z for z in graph.collect(_manifest())["zones"]}
    assert by_name["staging"]["kind"] == "folder"
    assert by_name["seeds"]["kind"] == "seed"
    assert by_name["sources"]["kind"] == "source"


# ------------------------------------------ les dépendances des snapshots


def _manifest_snapshot() -> FakeManifest:
    """`orders → history (snapshot) → report` : une lignée qui traverse un snapshot."""
    return FakeManifest(
        nodes=[
            fake_node("model.demo.orders", "orders", path="models/orders.sql"),
            fake_node(
                "snapshot.demo.history",
                "history",
                resource_type="snapshot",
                path="snapshots/history.sql",
                depends_on=("model.demo.orders",),
            ),
            fake_node(
                "model.demo.report",
                "report",
                path="models/report.sql",
                depends_on=("snapshot.demo.history",),
            ),
        ]
    )


def test_un_snapshot_garde_ses_dependances_entrantes():
    """La boucle des arêtes sautait tout ce qui n'était pas un modèle.

    Le snapshot entrait bien dans `datasets`, dans `parents` et dans le calcul
    des profondeurs — `up_count` comptait le lien — mais aucune arête ne le
    rejoignait. La lignée affichée se coupait entre le modèle et son snapshot,
    et les parcours d'amont et d'aval de l'interface, construits à partir des
    arêtes, ne pouvaient plus suivre.
    """
    g = graph.collect(_manifest_snapshot())
    incoming = {e["from"] for e in g["edges"] if e["to"] == "snapshot.demo.history"}
    assert incoming == {"model.demo.orders"}

    by_name = {d["name"]: d for d in g["datasets"]}
    assert by_name["history"]["up_count"] == 1

    # Et le bout en bout tient : de `orders` à `report` en suivant les arêtes.
    adj = {}
    for e in g["edges"]:
        adj.setdefault(e["from"], []).append(e["to"])
    seen, queue = set(), ["model.demo.orders"]
    while queue:
        for neighbour in adj.get(queue.pop(), []):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    assert "snapshot.demo.history" in seen
    assert "model.demo.report" in seen


def test_un_snapshot_n_a_pas_de_recipe():
    """Il n'a pas de script visuel : c'est dbt qui l'écrit, pas l'atelier."""
    g = graph.collect(_manifest_snapshot())
    assert {r["model_name"] for r in g["recipes"]} == {"orders", "report"}


# ------------------------------------ le script visuel reste au projet


def test_un_modele_de_paquet_n_herite_pas_de_la_recipe_locale():
    """Deux `orders`, un seul script visuel — et il est au projet.

    `collect()` associait les scripts par nom : la recipe locale `orders` était
    annoncée sur le modèle du paquet, `managed: true`, avec ses actions. Tout
    ce qu'on en faisait — ouvrir, renommer, supprimer — portait alors sur le
    modèle local, puisque c'est lui que le nom désigne côté serveur.
    """
    local = _package("orders", "model.demo.orders", "demo")
    foreign = _package("orders", "model.vendor.orders", "vendor")
    g = graph.collect(
        FakeManifest([local, foreign]),
        recipe_specs={"orders": {"name": "orders", "type": "join", "steps": []}},
        project="demo",
    )
    by_model = {r["model"]: r for r in g["recipes"]}

    assert by_model["model.demo.orders"]["managed"] is True
    assert by_model["model.demo.orders"]["external"] is False
    assert by_model["model.demo.orders"]["editable"] is True

    assert by_model["model.vendor.orders"]["managed"] is False
    assert by_model["model.vendor.orders"]["external"] is True
    assert by_model["model.vendor.orders"]["editable"] is False
    assert by_model["model.vendor.orders"]["package"] == "vendor"


def test_l_ordre_du_manifest_ne_decide_pas_de_qui_porte_la_recipe():
    """Le paquet d'abord, le projet ensuite : la réponse ne doit pas changer."""
    local = _package("orders", "model.demo.orders", "demo")
    foreign = _package("orders", "model.vendor.orders", "vendor")
    for order in ([local, foreign], [foreign, local]):
        g = graph.collect(
            FakeManifest(order),
            recipe_specs={"orders": {"name": "orders", "type": "join", "steps": []}},
            project="demo",
        )
        by_model = {r["model"]: r for r in g["recipes"]}
        assert by_model["model.demo.orders"]["managed"] is True
        assert by_model["model.vendor.orders"]["managed"] is False


def test_un_modele_de_paquet_garde_sa_place_dans_la_lignee():
    """Lui retirer ses actions ne doit pas lui retirer ses arêtes."""
    foreign = _package("utils", "model.vendor.utils", "vendor")
    downstream = fake_node(
        "model.demo.report", "report", depends_on=("model.vendor.utils",)
    )
    g = graph.collect(FakeManifest([foreign, downstream]), project="demo")
    rid = next(r["id"] for r in g["recipes"] if r["model"] == "model.vendor.utils")
    assert {"from": rid, "to": "model.vendor.utils"} in g["edges"]


# ----------------------------------------- profondeurs sans pile Python


def _string(n: int) -> dict[str, list[str]]:
    return {f"m{i}": ([f"m{i - 1}"] if i else []) for i in range(n)}


def test_une_lignee_profonde_ne_fait_pas_deborder_la_pile():
    """600 modèles en chaîne, présentés de l'aval vers l'amont.

    `_depths()` remontait les parents par récursion, à deux cadres Python par
    niveau : au-delà d'environ cinq cents de profondeur, `collect()` levait
    `RecursionError` et le projet n'avait plus de Flow du tout. Le cache ne
    servait que si les ancêtres avaient déjà été visités, donc le résultat
    dépendait de l'ordre des nœuds dans le manifest.
    """
    parents = _string(600)
    for order in (list(parents), list(reversed(list(parents)))):
        depth = graph._depths(order, parents)
        assert depth["m599"] == 599
        assert depth["m0"] == 0
        assert len(depth) == 600


def test_la_profondeur_ne_depend_pas_de_l_ordre_des_noeuds():
    """Un diamant, dans les deux sens : mêmes profondeurs."""
    parents = {"d": [], "a": ["d"], "b": ["d"], "x": ["a", "b"]}
    expected = {"d": 0, "a": 1, "b": 1, "x": 2}
    assert graph._depths(["x", "a", "b", "d"], parents) == expected
    assert graph._depths(["d", "b", "a", "x"], parents) == expected


def test_un_cycle_ne_fait_pas_tourner_le_calcul():
    """Les replis peuvent en créer un : le Flow doit sortir quand même."""
    parents = {"a": ["b"], "b": ["a"], "c": ["b"]}
    depth = graph._depths(["c", "a", "b"], parents)
    assert set(depth) == {"a", "b", "c"}
    assert all(isinstance(v, int) for v in depth.values())


def test_un_flow_profond_se_construit_vraiment():
    """Le vrai `collect()`, pas seulement `_depths()`, sur 600 modèles."""
    nodes = [
        fake_node(
            f"model.demo.m{i}",
            f"m{i}",
            depends_on=((f"model.demo.m{i - 1}",) if i else ()),
        )
        for i in range(600)
    ]
    g = graph.collect(FakeManifest(list(reversed(nodes))))
    assert len(g["datasets"]) == 600
    assert max(d["depth"] for d in g["datasets"]) == 599


# ------------------------------- le Flow se construit sans tout comparer


def _large_project(n: int, seed: int = 7) -> FakeManifest:
    """Un graphe de la forme habituelle : staging → intermediate → marts."""
    import random

    random.seed(seed)
    nodes, stg = [], []
    for i in range(n // 2):
        uid = f"model.demo.stg_{i}"
        nodes.append(fake_node(uid, f"stg_{i}", path=f"models/staging/stg_{i}.sql"))
        stg.append(uid)
    inter = []
    for i in range(n // 4):
        uid = f"model.demo.int_{i}"
        nodes.append(
            fake_node(
                uid,
                f"int_{i}",
                path=f"models/intermediate/int_{i}.sql",
                depends_on=tuple(random.sample(stg, min(3, len(stg)))),
            )
        )
        inter.append(uid)
    for i in range(n - len(nodes)):
        nodes.append(
            fake_node(
                f"model.demo.mart_{i}",
                f"mart_{i}",
                path=f"models/marts/mart_{i}.sql",
                depends_on=tuple(random.sample(inter + stg, 3)),
            )
        )
    return FakeManifest(nodes)


def _route_by_sweep(edges: list[dict], by_id: dict[str, dict]) -> None:
    """Le routage d'avant, mot pour mot : la référence à égaler."""
    boxes = {uid: graph._box(n) for uid, n in by_id.items()}
    for edge in edges:
        src, dst = by_id.get(edge["from"]), by_id.get(edge["to"])
        if src is None or dst is None:
            continue
        x1, x2 = src["cx"], dst["cx"]
        if x2 <= x1:
            continue
        ay, by = graph._anchor_y(src), graph._anchor_y(dst)
        span = [
            box
            for uid, box in boxes.items()
            if uid not in (edge["from"], edge["to"]) and box[2] > x1 and box[0] < x2
        ]
        if not any(graph._crosses(box, x1, x2, ay, by) for box in span):
            continue
        above = min(box[1] for box in span) - graph.LANE_GAP
        below = max(box[3] for box in span) + graph.LANE_GAP
        mid = (ay + by) / 2
        use_above = above >= graph.LANE_GAP and (mid - above) <= (below - mid)
        edge["via"] = int(above if use_above else below)


def test_le_routage_indexe_donne_exactement_le_meme_dessin():
    """L'index par colonne est une accélération, pas un changement de dessin.

    `_route_edges` comparait chaque arête à tous les nœuds. Le remplacer par un
    index ne vaut que si le tracé ne bouge pas d'un pixel : c'est ce que vérifie
    la comparaison avec l'ancien balayage, y compris sur les voies de
    contournement, qui sont la partie la plus facile à dérégler.
    """
    for seed in (1, 2, 3):
        for size in (120, 400):
            g = graph.collect(_large_project(size, seed))
            indexed = {(e["from"], e["to"]): e.get("via") for e in g["edges"]}

            witness = [{"from": e["from"], "to": e["to"]} for e in g["edges"]]
            _route_by_sweep(witness, {n["id"]: n for n in g["datasets"] + g["recipes"]})
            sweeps = {(e["from"], e["to"]): e.get("via") for e in witness}

            assert indexed == sweeps, (seed, size)
            assert any(v is not None for v in sweeps.values()), "rien à contourner"


def test_le_routage_donne_le_meme_dessin_avec_un_bloc_replie():
    """Un bloc replié est plus large qu'un dataset : l'index doit le supporter."""
    g = graph.collect(_large_project(400), folded=["zone:staging"])
    witness = [{"from": e["from"], "to": e["to"]} for e in g["edges"]]
    _route_by_sweep(witness, {n["id"]: n for n in g["datasets"] + g["recipes"]})
    assert {(e["from"], e["to"]): e.get("via") for e in g["edges"]} == {
        (e["from"], e["to"]): e.get("via") for e in witness
    }


def test_construire_un_flow_large_ne_compare_pas_tout_a_tout(monkeypatch):
    """Le coût était un produit arêtes × nœuds, payé à chaque construction.

    Mesuré en nombre d'appels plutôt qu'en secondes : c'est la grandeur qui
    explosait, et elle ne dépend pas de la machine. Sur ce graphe, le balayage
    complet en demandait plus de vingt millions ; l'index en demande quelques
    dizaines de milliers. Le seuil est large — il attrape un retour au
    quadratique, pas une régression de quelques pour cent.
    """
    calls = 0
    real = graph._crosses

    def tally(*args):
        nonlocal calls
        calls += 1
        return real(*args)

    monkeypatch.setattr(graph, "_crosses", tally)
    g = graph.collect(_large_project(2000))

    assert len(g["datasets"]) == 2000
    assert calls < 200_000, f"{calls} appels à _crosses : le balayage est revenu"


def test_la_fiche_d_un_dataset_ne_construit_pas_le_flow():
    """`GET /api/dataset/{uid}` appelait `collect()` pour une seule entrée.

    Tout le graphe disposé et toutes les arêtes routées, à chaque clic sur un
    nœud. `node_card()` rend les mêmes valeurs en ne lisant que le manifeste.
    """
    m = _manifest()
    g = graph.collect(m)
    by_id = {d["id"]: d for d in g["datasets"]}

    for uid in ("model.demo.customer_orders", "seed.demo.raw_customers"):
        f = graph.node_card(m, uid)
        expected = by_id[uid]
        for key in ("name", "kind", "layer", "materialized"):
            assert f[key] == expected[key], (uid, key)
        assert f["tests"] == expected["tests"], uid

    # Une source aussi, qui vit dans `manifest.sources`.
    src = graph.node_card(m, "source.demo.raw.raw_orders")
    assert src["name"] == by_id["source.demo.raw.raw_orders"]["name"]
    assert src["kind"] == "source"

    assert graph.node_card(m, "model.demo.inconnu") == {}
