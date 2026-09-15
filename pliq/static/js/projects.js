/* L'accueil, la création d'un projet, et la déclaration d'une source. */
import { $, OPS, PIVOT_AGGS, TYPES, WINDOW_FNS, el, esc } from "./core.js";
import { state } from "./state.js";
import { api, post, projectDir, refreshRefs, renderCrumb, show, toast } from "./api.js";
import { closeRename, loadFlow, renderPanel, resetRecipeDelete, view } from "./flow.js";
import { newRecipeCtx, leaveRecipe, resetSqlTargets } from "./recipe.js";
import { applyLayerMaterialization, renderLayerHint } from "./boot.js";
import { answerConfirm } from "./confirm.js";


function humanBytes(n) {
  if (!n && n !== 0) return "";
  if (n < 1024) return n + " o";
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " Ko";
  if (n < 1024 * 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + " Mo";
  return (n / 1024 / 1024 / 1024).toFixed(1) + " Go";
}


function humanAgo(ts) {
  if (!ts) return "jamais construit";
  const d = Date.now() / 1000 - ts;
  if (d < 60) return "construit à l'instant";
  if (d < 3600) return `construit il y a ${Math.floor(d / 60)} min`;
  if (d < 86400) return `construit il y a ${Math.floor(d / 3600)} h`;
  if (d < 86400 * 30) return `construit il y a ${Math.floor(d / 86400)} j`;
  return "construit " + new Date(ts * 1000).toLocaleDateString("fr-FR");
}


async function loadHome() {
  const box = $("home-grid");
  box.innerHTML = `<p class="home-empty">Lecture…</p>`;
  try {
    state.projects = await api("/api/projects");
  } catch (e) {
    box.innerHTML = "";
    return toast("Projets illisibles", e.message, "error");
  }
  $("scan-path").value = $("scan-path").value || state.projects.default_parent || "";
  renderHome();
}


function renderHome() {
  const data = state.projects || { projects: [], active: "" };
  const box = $("home-grid");
  box.innerHTML = "";
  $("home-empty").hidden = data.projects.length > 0;

  data.projects.forEach((p) => {
    const active = p.path === data.active;
    const card = el("div", "pcard" + (active ? " active" : "") + (p.error ? " broken" : ""));

    const facts = [];
    if (p.adapter) facts.push(`<span class="fact">${esc(p.adapter)}${p.target ? " · " + esc(p.target) : ""}</span>`);
    facts.push(`<span class="fact"><b>${p.models}</b> modèles</span>`);
    if (p.seeds) facts.push(`<span class="fact"><b>${p.seeds}</b> seeds</span>`);
    if (p.snapshots) facts.push(`<span class="fact"><b>${p.snapshots}</b> snapshots</span>`);
    if (p.database_size) facts.push(`<span class="fact">${esc(humanBytes(p.database_size))}</span>`);
    if (p.error) facts.push(`<span class="fact warn">${esc(p.error)}</span>`);

    card.innerHTML =
      `<h3>${esc(p.name)}${active ? '<span class="open">ouvert</span>' : ""}</h3>
       <div class="path">${esc(p.path)}</div>
       <div class="facts">${facts.join("")}</div>
       <div class="foot">
         <span class="when">${esc(humanAgo(p.last_run))}</span>
         <button class="btn sm" data-act="forget">Retirer</button>
         <button class="btn sm danger" data-act="delete">Supprimer</button>
         <button class="btn sm primary" data-act="open">${active ? "Revenir" : "Ouvrir"}</button>
       </div>`;

    card.querySelector('[data-act="open"]').addEventListener("click", () => openProject(p.path));
    card.querySelector('[data-act="forget"]').addEventListener("click", async () => {
      try { await post("/api/projects/forget", { path: p.path }); }
      catch (e) { return toast("Impossible", e.message, "error"); }
      toast("Retiré de la liste", "Les fichiers sont intacts.");
      loadHome();
    });
    card.querySelector('[data-act="delete"]').addEventListener("click", () => openDelete(p));
    box.appendChild(card);
  });
}


async function openProject(path) {
  // Ouvrir un autre projet ferme l'éditeur et met `rec` à null : c'est une
  // sortie d'écran comme une autre, et elle ne demandait rien.
  if (!(await leaveRecipe("La recipe ouverte n'est pas enregistrée."))) return;
  try {
    const r = await post("/api/projects/open", { path });
    if (r.parse_error) toast("dbt parse", r.parse_error, "error");
    // Changer de cible sans le dire, c'est risquer de construire ailleurs que
    // là où l'on croit : ce projet-ci ne déclare pas celle de la session.
    if (r.target_dropped) {
      toast("Cible changée",
            `« ${r.target_dropped} » n'existe pas dans ce projet : cible « ${r.target} ».`,
            "error");
    }
  } catch (e) {
    return toast("Ouverture impossible", e.message, "error");
  }
  await reloadProject();
  show("flow");
}

/* La bibliothèque d'étapes suit l'entrepôt : « Changer le type » ne propose
   pas les mêmes types sur BigQuery et sur DuckDB. Elle se relit donc à chaque
   projet, et pas seulement au démarrage. */
async function loadLibrary() {
  try {
    const lib = await api("/api/processors");
    state.processors = lib.processors;
    state.categories = lib.categories;
    (lib.recipe_types || []).forEach((t) => { state.recipeTypes[t.key] = t; });
    ["materializations", "incremental_strategies", "strategies_needing_key",
     "on_schema_change", "types", "batch_sizes", "lookback_units",
     "window_needs_column", "window_needs_order",
     // L'arité des opérateurs de filtre, et la stratégie incrémentale par
     // défaut de cet entrepôt : deux listes que l'écran ne doit pas deviner.
     "single_value_operators", "no_value_operators",
     "default_incremental_strategy"]
      .forEach((k) => { if (lib[k]) state.caps[k] = lib[k]; });
    // Les listes déroulantes viennent du serveur, et de lui seul. `remplir`
    // vide puis recharge le tableau au lieu de le remplacer : `FIELDS` en
    // garde la référence, et une réaffectation laisserait les formulaires
    // pointer sur l'ancien.
    const fill = (target, entriesOf) => {
      target.length = 0;
      (entriesOf || []).forEach((e) => target.push([e.key, e.label]));
    };
    fill(OPS, lib.operators);
    fill(WINDOW_FNS, lib.window_functions);
    fill(PIVOT_AGGS, lib.pivot_aggregations);
    // Les types nomment ceux de l'entrepôt ouvert : une simple liste de mots.
    TYPES.length = 0;
    (lib.types || []).forEach((t) => TYPES.push([t, t]));
  } catch (_) { /* la bibliothèque précédente restera */ }
}

/* Les écrans qui gardent des données lues dans un projet. Après une bascule,
   ils affichent l'ancien et enregistreraient dans le nouveau : la garde
   `X-Pliq-Project` refuse bien l'écriture, mais laisser la modale ouverte
   revient à promettre un enregistrement qui ne peut plus aboutir.

   Deux absences volontaires : « nouveau projet » ne lit rien du projet ouvert
   (un dossier parent et un nom), et « oublier un projet » porte sur une entrée
   de la liste d'accueil, pas sur le projet actif. */
const FORMS = [
  ["tests-scrim", "Tests & documentation"],
  ["sql-scrim", "Éditeur SQL"],
  ["ds-scrim", "Nouvelle source"],
  ["data-scrim", "Contrôle complet"],
  ["imp-scrim", "Import"],
  ["sqldiv-scrim", "Remplacement du SQL"],
  ["rdel-scrim", "Suppression"],
  ["rren-scrim", "Renommage"],
  ["scrim", "Étape"],
  ["lib", "Bibliothèque d'étapes"],
  // En dernier : c'est la modale qu'une autre peut recouvrir, et Échap doit
  // trancher celle du dessus.
  ["quit-scrim", "Confirmation"],
];

/* Ce qu'il faut remettre à zéro quand un formulaire se ferme, formulaire par
   formulaire. Fermer une modale n'est pas seulement retirer une classe : elle
   porte un état — la cible d'une suppression, l'inventaire d'un entrepôt, les
   requêtes encore en vol — et le laisser en place fait qu'une réouverture
   repart de la fiche précédente. */
const CLEANUP = {
  "ds-scrim": () => resetDatasetModal(),
  "sql-scrim": () => resetSqlTargets(),
  "sqldiv-scrim": () => resetSqlTargets(),
  "rdel-scrim": () => resetRecipeDelete(),
  "rren-scrim": () => closeRename(),
  "tests-scrim": () => { state.tests = null; },
  // Fermer la question sans y répondre — Échap, ou un changement de projet —
  // vaut « non » : sans ça, la promesse de `quitterRecipe` n'arrive jamais et
  // la sortie reste en suspens pour toujours.
  "quit-scrim": () => answerConfirm(false),
};

/** Ferme un formulaire par son identifiant, et remet à zéro ce qu'il portait. */
function closeForm(id) {
  const box = $(id);
  if (box) box.classList.remove("on");
  const cleanup = CLEANUP[id];
  if (cleanup) cleanup();
}

/** Ferme les écrans qui portaient des données de l'ancien projet, et les nomme. */
function closeOpenForms() {
  const closed = [];
  FORMS.forEach(([id, label]) => {
    const box = $(id);
    if (box && box.classList.contains("on")) {
      box.classList.remove("on");
      closed.push(label);
    }
  });
  // Et le contenu avec : rouvrir l'écran doit relire le projet ouvert, pas
  // retrouver la fiche d'un autre.
  state.tests = null;
  resetSqlTargets();
  resetRecipeDelete();
  closeRename();
  resetDatasetModal();
  answerConfirm(false);
  return closed;
}


async function reloadProject() {
  const closed = closeOpenForms();
  state.selected = null;
  view.fitted = false;
  view.touched = false;
  state.rec = null;
  state.explore = null;
  state.refs = [];
  try { state.project = await api("/api/project"); } catch (_) { /* rien */ }
  await loadLibrary();
  await refreshRefs();
  await loadFlow(false);
  renderPanel();
  renderCrumb();
  if (state.project && !state.project.database_exists)
    toast("Base absente", "Lancez « dbt build » pour créer la base et activer les aperçus.");
  return closed;
}


function npPreview() {
  const name = $("np-name").value.trim();
  const parent = $("np-parent").value.trim().replace(/\/+$/, "");
  $("np-preview").innerHTML = name && parent
    ? `Le projet sera créé dans <code>${esc(parent + "/" + name)}</code>.`
    : "";
}

/* ---------------------------------------------------- suppression projet */

let delTarget = null;


function openDelete(p) {
  delTarget = p;
  const folder = p.path.split("/").filter(Boolean).pop();
  $("del-title").textContent = `Supprimer « ${p.name} »`;
  $("del-warn").innerHTML =
    `Le dossier <code>${esc(p.path)}</code> et tout son contenu seront effacés du disque :
     modèles, seeds, tests, base de données. <b>C'est irréversible.</b>
     Pour seulement retirer le projet de cette liste, fermez et cliquez « Retirer ».`;
  $("del-confirm").value = "";
  $("del-confirm").placeholder = folder;
  $("del-err").textContent = "";
  $("del-scrim").classList.add("on");
  $("del-confirm").focus();
}


/* `gen` est le numéro de l'ouverture en cours de la modale. Chaque `await`
   d'`openNewDataset` le relit avant d'écrire quoi que ce soit : ouvrir
   « Nouvelle source » dans A, basculer vers B et rouvrir la modale laissait
   la réponse tardive de A réécrire `dsState.tables`, alors que
   `dsState.project` valait déjà B. Le garde-fou serveur validait — l'en-tête
   nommait bien B — et le formulaire déclarait dans B des tables lues dans
   l'inventaire de A. Fermer ou rouvrir la modale invalide donc ses requêtes
   en vol, et le contexte capturé au départ est confronté à l'actuel. */
const dsState = { tables: [], sources: [], database: "", sel: new Set(),
                  project: null, gen: 0 };

/* Oublie l'inventaire lu : il décrivait l'entrepôt d'un autre projet.

   Déclarée en `function` pour être appelable depuis `closeOpenForms`, qui est
   écrit plus haut : `dsState` n'est alors déréférencé qu'à l'appel, et pas à
   l'évaluation du module. */
function resetDatasetModal() {
  dsState.tables = [];
  dsState.sources = [];
  dsState.database = "";
  dsState.project = null;
  dsState.sel.clear();
  // Toute réponse encore en vol appartient désormais au passé.
  dsState.gen += 1;
}

// Les trois niveaux : deux bases attachées peuvent avoir chacune leur
// `main.orders`, et une clé à deux niveaux en cochait une pour l'autre.

// Les trois niveaux : deux bases attachées peuvent avoir chacune leur
// `main.orders`, et une clé à deux niveaux en cochait une pour l'autre.
function dsKey(t) { return (t.database || "") + "." + t.schema + "." + t.name; }


async function openNewDataset() {
  dsState.sel.clear();
  // Le contexte de ce formulaire, figé : le projet d'où part la déclaration,
  // et le numéro de cette ouverture.
  const project = projectDir();
  const gen = ++dsState.gen;
  dsState.project = project;
  $("ds-err").textContent = "";
  $("ds-source-desc").value = "";
  $("ds-search").value = "";
  // Le nom et le drapeau « touché » repartent de zéro : un choix précédent
  // continuait sinon de piloter une nouvelle déclaration.
  $("ds-source-name").value = "";
  delete $("ds-source-name").dataset.touched;
  $("ds-scrim").classList.add("on");

  $("ds-tables").innerHTML = `<div class="dempty">Lecture de l'entrepôt…</div>`;
  // Le contexte se revérifie après *chaque* await : la modale a pu être
  // fermée, rouverte, ou le projet changé pendant la lecture de l'entrepôt.
  const stale = () => gen !== dsState.gen || project !== projectDir();
  try {
    const inv = await api("/api/warehouse/tables");
    if (stale()) return;
    dsState.tables = inv.tables || [];
    dsState.sources = inv.sources || [];
    dsState.database = inv.database || "";
    dsState.truncated = !!inv.truncated;
    dsState.limit = inv.limit || 0;
    $("ds-source-list").innerHTML =
      dsState.sources.map((s) => `<option value="${esc(s.name)}">`).join("");
    renderDsTables();
  } catch (e) {
    if (stale()) return;
    dsState.tables = [];
    $("ds-tables").innerHTML = `<div class="dempty">${esc(e.message)}</div>`;
  }
  renderSourcePreview();
}


function renderDsTables() {
  const box = $("ds-tables");
  const q = $("ds-search").value.trim().toLowerCase();
  const hide = $("ds-hide-known").checked;
  const rows = dsState.tables.filter((t) => {
    if (hide && t.declared) return false;
    if (!q) return true;
    return (t.schema + "." + t.name).toLowerCase().includes(q);
  });

  $("ds-count").textContent = `${rows.length} / ${dsState.tables.length} tables`
    + (dsState.truncated ? ` (inventaire tronqué à ${dsState.limit})` : "");
  box.innerHTML = "";
  if (!rows.length) {
    box.innerHTML = `<div class="dempty">${dsState.tables.length
      ? "Toutes les tables de l'entrepôt sont déjà déclarées."
      : "L'entrepôt ne renvoie aucune table."}</div>`;
    return;
  }

  rows.forEach((t) => {
    const key = dsKey(t);
    const row = el("label", "trow-t" + (t.declared ? " known" : ""));
    const meta = [];
    if (t.columns != null) meta.push(`${t.columns} col.`);
    if (t.rows != null) meta.push(`${t.rows.toLocaleString("fr-FR")} lignes`);
    if (t.type === "view") meta.push("vue");
    row.innerHTML =
      `<input type="checkbox" ${dsState.sel.has(key) ? "checked" : ""} ${t.declared ? "disabled" : ""}>
       <span class="sch">${esc(t.schema)}.</span><span class="nm">${esc(t.name)}</span>
       <span class="meta">${meta.map((m) => `<span>${esc(m)}</span>`).join("")}
         ${t.declared ? `<span class="tag">déjà ${esc(t.declared.kind)} : ${esc(t.declared.label)}</span>` : ""}
       </span>`;
    if (!t.declared) {
      row.querySelector("input").addEventListener("change", (e) => {
        if (e.target.checked) dsState.sel.add(key); else dsState.sel.delete(key);
        onSelectionChanged();
      });
    }
    box.appendChild(row);
  });
}


function selectedTables() {
  return dsState.tables.filter((t) => dsState.sel.has(dsKey(t)));
}

/* La base des tables cochées. L'inventaire la connaît par table ; sans elle,
   dbt retombe sur celle de la cible, et `raw.orders` choisi dans `analytics`
   désigne alors le `raw.orders` de `dev` — introuvable, ou pire : homonyme. */
function selectedDatabases(sel) {
  return [...new Set(sel.map((t) => t.database || dsState.database || ""))];
}


function onSelectionChanged() {
  const sel = selectedTables();
  const field = $("ds-source-name");
  if (sel.length && !field.dataset.touched) {
    const schema = sel[0].schema;
    field.value = /^[A-Za-z_][A-Za-z0-9_]*$/.test(schema) && schema !== "main" ? schema : "raw";
  }
  renderSourcePreview();
}

function renderSourcePreview() {
  const box = $("ds-source-prev");
  const sel = selectedTables();
  if (!sel.length) {
    box.innerHTML = `<p>Choisissez une ou plusieurs tables à gauche. Rien n'est copié :
      dbt se contentera de savoir qu'elles existent.</p>`;
    return;
  }
  const schemas = [...new Set(sel.map((t) => t.schema))];
  const databases = selectedDatabases(sel);
  const name = $("ds-source-name").value.trim() || "raw";
  const desc = $("ds-source-desc").value.trim();
  const yaml = [
    "sources:",
    `  - name: ${name}`,
    ...(databases[0] ? [`    database: ${databases[0]}`] : []),
    `    schema: ${schemas[0]}`,
    ...(desc ? [`    description: "${desc}"`] : []),
    "    tables:",
    ...sel.map((t) => `      - name: ${t.name}`),
  ].join("\n");

  box.innerHTML =
    `<p>Sera ajouté au <code>sources.yml</code> du projet :</p>
     <pre class="code" style="max-height:180px">${esc(yaml)}</pre>
     <p>Puis utilisable dans une recipe via
        <code>{{ source('${esc(name)}', '${esc(sel[0].name)}') }}</code>.</p>
     ${schemas.length > 1
       ? `<p style="color:var(--danger)">Un groupe de sources porte un seul schéma :
          ${esc(schemas.join(", "))} sont mélangés. Faites-en deux groupes.</p>`
       : ""}
     ${databases.length > 1
       ? `<p style="color:var(--danger)">Un groupe de sources porte une seule base :
          ${esc(databases.map((d) => d || "(défaut)").join(", "))} sont mélangées.
          Faites-en deux groupes.</p>`
       : ""}`;
}

/* --------------------------------------------------------------- création */


async function createSource() {
  const sel = selectedTables();
  if (!sel.length) throw new Error("Choisissez au moins une table.");
  const schemas = [...new Set(sel.map((t) => t.schema))];
  if (schemas.length > 1)
    throw new Error("Un groupe de sources ne porte qu'un schéma. Sélectionnez les tables de "
                    + schemas[0] + " d'abord.");
  const databases = selectedDatabases(sel);
  if (databases.length > 1)
    throw new Error("Un groupe de sources ne porte qu'une base. Sélectionnez les tables de "
                    + (databases[0] || "(défaut)") + " d'abord.");
  const name = $("ds-source-name").value.trim();
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name))
    throw new Error("Nom de source invalide : lettres, chiffres et tirets bas.");

  // Le projet de l'ouverture, et non celui de maintenant : déclarer dans le
  // projet courant des tables lues dans un autre est précisément le geste
  // qu'on refuse.
  if (dsState.project && dsState.project !== projectDir())
    throw new Error("Le projet a changé depuis l'ouverture de ce formulaire. "
                    + "Fermez-le et rouvrez-le pour repartir de l'inventaire "
                    + "du projet courant.");

  const r = await post("/api/datasets/source", {
    source_name: name,
    schema_name: schemas[0],
    // Transmise telle que l'inventaire l'a lue : le groupe la déclare, et ne
    // dépend plus de la base par défaut de la cible.
    database: databases[0] || null,
    description: $("ds-source-desc").value.trim(),
    tables: sel.map((t) => ({ name: t.name })),
  }, { project: dsState.project });
  $("ds-scrim").classList.remove("on");
  if (r.parse_error) toast("dbt parse", r.parse_error, "error");
  toast("Source déclarée",
        `${r.added.length} table(s) dans ${r.path}` +
        (r.skipped.length ? ` — ${r.skipped.length} déjà présentes` : ""));
  state.refs = [];
  await refreshRefs();
  loadFlow(true);
}

export {
  FORMS,
  closeForm,
  loadHome,
  loadLibrary,
  reloadProject,
};

/* Les branchements de ce module. Appelé par `main.js` une fois
   tous les modules évalués : aucun effet de bord ne doit avoir lieu
   pendant le chargement, sinon les imports croisés se mordent la
   queue. */
export function wire() {

  $("nr-layer").addEventListener("change", () => {
    if (newRecipeCtx) newRecipeCtx.layerTouched = true;
    if (newRecipeCtx && !newRecipeCtx.matTouched) applyLayerMaterialization();
    renderLayerHint();
  });
  $("nr-mat").addEventListener("change", () => {
    if (newRecipeCtx) newRecipeCtx.matTouched = true;
  });

  /* ==========================================================================
     ACCUEIL — les projets dbt connus
     ========================================================================== */


  $("btn-home").addEventListener("click", () => { show("home"); loadHome(); });
  $("home-add").addEventListener("click", () => {
    const box = $("home-scan");
    box.hidden = !box.hidden;
    if (!box.hidden) $("scan-path").focus();
  });
  $("scan-go").addEventListener("click", async () => {
    const root = $("scan-path").value.trim();
    if (!root) return;
    $("scan-note").textContent = "Recherche…";
    try {
      const r = await post("/api/projects/scan", { root });
      $("scan-note").textContent = r.found.length
        ? `${r.found.length} projet(s) trouvé(s).`
        : "Aucun dbt_project.yml sous ce dossier.";
      loadHome();
    } catch (e) {
      $("scan-note").textContent = e.message;
    }
  });

  /* ------------------------------------------------------- nouveau projet */


  /* ------------------------------------------------------- nouveau projet */

  $("home-new").addEventListener("click", () => {
    $("np-name").value = "";
    $("np-err").textContent = "";
    $("np-parent").value = (state.projects && state.projects.default_parent) || "";
    $("np-preview").textContent = "";
    $("np-scrim").classList.add("on");
    $("np-name").focus();
  });

  $("np-name").addEventListener("input", npPreview);
  $("np-parent").addEventListener("input", npPreview);
  $("np-cancel").addEventListener("click", () => $("np-scrim").classList.remove("on"));


  $("np-create").addEventListener("click", async () => {
    const name = $("np-name").value.trim();
    const parent = $("np-parent").value.trim();
    $("np-err").textContent = "";
    if (!/^[a-z_][a-z0-9_]*$/.test(name)) {
      $("np-err").textContent = "Nom invalide : minuscules, chiffres et « _ », commençant par une lettre.";
      return;
    }
    if (!parent) { $("np-err").textContent = "Indiquez le dossier parent."; return; }
    $("np-create").disabled = true;
    try {
      const r = await post("/api/projects/create", { parent, name, open: true });
      $("np-scrim").classList.remove("on");
      toast("Projet créé", r.created);
      // Le serveur distingue désormais « créé » de « ouvert » : un build en
      // cours empêche la bascule, et le projet existe quand même.
      if (r.opened === false) {
        toast("Projet non ouvert",
              r.open_error || "Ouvrez-le depuis l'accueil.", "warn");
        await loadHome();
        show("home");
      } else {
        await reloadProject();
        show("flow");
      }
    } catch (e) {
      $("np-err").textContent = e.message;
    } finally {
      $("np-create").disabled = false;
    }
  });

  /* ---------------------------------------------------- suppression projet */


  $("del-cancel").addEventListener("click", () => $("del-scrim").classList.remove("on"));
  $("del-go").addEventListener("click", async () => {
    if (!delTarget) return;
    $("del-err").textContent = "";
    try {
      await post("/api/projects/forget", {
        path: delTarget.path, delete_files: true, confirm: $("del-confirm").value.trim(),
      });
    } catch (e) {
      $("del-err").textContent = e.message;
      return;
    }
    $("del-scrim").classList.remove("on");
    toast("Projet supprimé", delTarget.path);
    delTarget = null;
    loadHome();
  });

  /* ==========================================================================
     NOUVEAU DATASET — déclarer une table de l'entrepôt

     Une table n'entre dans le Flow qu'en étant déclarée : dbt sait qu'elle
     existe, rien n'est copié. Charger des données est le travail d'un outil
     d'ingestion, pas d'un atelier de transformation.
     ========================================================================== */


  $("ds-search").addEventListener("input", renderDsTables);
  $("ds-hide-known").addEventListener("change", renderDsTables);

  $("ds-source-name").addEventListener("input", (e) => {
    e.target.dataset.touched = "1";
    renderSourcePreview();
  });
  $("ds-source-desc").addEventListener("input", renderSourcePreview);


  /* --------------------------------------------------------------- création */

  $("ds-cancel").addEventListener("click", () => $("ds-scrim").classList.remove("on"));
  $("ds-scrim").addEventListener("click", (e) => {
    if (e.target === $("ds-scrim")) $("ds-scrim").classList.remove("on");
  });


  $("ds-create").addEventListener("click", async () => {
    $("ds-err").textContent = "";
    $("ds-create").disabled = true;
    try {
      await createSource();
    } catch (e) {
      $("ds-err").textContent = e.message;
    } finally {
      $("ds-create").disabled = false;
    }
  });


  $("btn-new-dataset").addEventListener("click", openNewDataset);

  /* ------------------------------------------ déplacer et cadrer le canevas */
}
