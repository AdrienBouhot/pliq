/* Le graphe : le charger, le dessiner, l'éclairer, agir sur un nœud. */
import { $, RECIPE_ICON, el, esc, svg, uid } from "./core.js";
import { state } from "./state.js";
import { api, post, projectDir, currentProject, refreshRefs, show, toast } from "./api.js";
import { openExplore } from "./explore.js";
import { generatedByRecipe, openNewRecipe, openRecipeEditor, openRecipeNode, openSqlModal } from "./recipe.js";
import { openTestFailures, openTestsModal } from "./tests.js";
import { FRESH_LABEL, PERIOD_LABEL, freshnessOf, humanDuration, runDbt } from "./runs.js";

const ZOOM_MIN = 0.12, ZOOM_MAX = 2.4, LOD_UNDER = 0.55, GRID = 24;
/* `touched` : tant que personne n'a déplacé le plan, il se recadre tout seul —
   redimensionner la fenêtre ne doit pas laisser le flux hors champ. Après un
   premier geste, en revanche, c'est le cadrage de l'utilisateur qui prime. */
const view = { x: 0, y: 0, k: 1, fitted: false, touched: false };


let fnodeEls = new Map();    // id → bouton du nœud
let edgeEls = [];            // { el, edge }
let upstream = new Map(), downstream = new Map();


function applyView() {
  const flow = $("flow");
  flow.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.k})`;
  flow.classList.toggle("lod", view.k < LOD_UNDER);
  const vp = $("flow-view");
  vp.style.backgroundSize = `${GRID * view.k}px ${GRID * view.k}px`;
  vp.style.backgroundPosition = `${view.x}px ${view.y}px`;
  $("btn-zoom-pct").textContent = Math.round(view.k * 100) + " %";
}

/** Garde toujours un bout de canevas à l'écran : on ne peut pas se perdre. */

/** Garde toujours un bout de canevas à l'écran : on ne peut pas se perdre. */
function clampView() {
  const f = state.flow;
  const r = $("flow-view").getBoundingClientRect();
  if (!f || !r.width) return;
  const margin = 120;
  const clamp = (pos, size, window) => {
    const a = margin - size * view.k, b = window - margin;
    return Math.min(Math.max(a, b), Math.max(Math.min(a, b), pos));
  };
  view.x = clamp(view.x, f.width, r.width);
  view.y = clamp(view.y, f.height, r.height);
}

/** Zoom ancré : le point sous le curseur ne bouge pas. */

/** Zoom ancré : le point sous le curseur ne bouge pas. */
function zoomAt(k, px, py) {
  view.touched = true;
  const next = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, k));
  view.x = px - (px - view.x) * (next / view.k);
  view.y = py - (py - view.y) * (next / view.k);
  view.k = next;
  clampView();
  applyView();
}


function zoomBy(factor) {
  const r = $("flow-view").getBoundingClientRect();
  zoomAt(view.k * factor, r.width / 2, r.height / 2);
}


function fitView() {
  const f = state.flow;
  const r = $("flow-view").getBoundingClientRect();
  if (!f || !r.width || !r.height) return false;
  const pad = 30;
  const k = Math.max(ZOOM_MIN, Math.min(1,
    (r.width - pad * 2) / f.width, (r.height - pad * 2) / f.height));
  view.k = k;
  view.x = (r.width - f.width * k) / 2;
  view.y = (r.height - f.height * k) / 2;
  applyView();
  return true;
}


function centerOnNode(id, kMin) {
  const n = fnodePos(id);
  const r = $("flow-view").getBoundingClientRect();
  if (!n || !r.width) return;
  view.touched = true;
  if (kMin && view.k < kMin) view.k = kMin;
  view.x = r.width / 2 - n.x * view.k;
  view.y = r.height / 2 - n.y * view.k;
  clampView();
  applyView();
}


function fnodePos(id) {
  const f = state.flow;
  if (!f) return null;
  const d = (f.datasets || []).find((n) => n.id === id);
  if (d) return { x: d.cx, y: d.cy + f.ds_size / 2 };
  const r = (f.recipes || []).find((n) => n.id === id);
  return r ? { x: r.cx, y: r.cy + f.ds_size / 2 } : null;
}

/* -------------------------------------------------------------- le pliage */

/* Un repli est une chaîne : `up:<uid>`, `down:<uid>` ou `zone:<nom>`. C'est un
   choix de lecture, pas une donnée du projet : il vit dans le navigateur, par
   projet, et se redemande au serveur, qui refait la disposition. */
function foldKey() {
  return "pliq.fold." + ((state.project && state.project.name) || "-");
}


const normFold = (spec) => {
  const s = String(spec).trim();
  return s.includes(":") ? s : "zone:" + s;   // ancienne forme : un nom nu
};


function loadFold() {
  try {
    const rawText = JSON.parse(localStorage.getItem(foldKey()) || "[]");
    state.fold = Array.isArray(rawText) ? rawText.map(normFold) : [];
  } catch (_) { state.fold = []; }
}


function saveFold() {
  try { localStorage.setItem(foldKey(), JSON.stringify(state.fold)); } catch (_) { /* mode privé */ }
}


function addFold(spec) {
  if (state.fold.includes(spec)) return;
  state.fold = state.fold.concat([spec]);
  saveFold();
  loadFlow(false);
}


function removeFold(spec) {
  state.fold = state.fold.filter((s) => s !== spec);
  saveFold();
  loadFlow(false);
}


function unfoldAll() {
  state.fold = [];
  saveFold();
  loadFlow(false);
}

/* ------------------------------------------------------- menu du clic droit */

/* Replier se désigne depuis le graphe : on montre le nœud, pas une liste de
   dossiers que personne n'a en tête. Renommer aussi : c'est du dataset qu'on
   parle, et le clic droit est le premier endroit où on le cherche. */
function openFlowMenu(d, ev) {
  ev.preventDefault();
  const menu = $("flow-menu");
  const items = [];

  // « en amont de » plutôt que « qui alimentent » : la phrase reste juste au
  // singulier comme au pluriel, sans accorder de verbe.
  const pluralize = (n, one, several) => `${n} ${n === 1 ? one : several}`;
  const plural = (n) => pluralize(n, "dataset", "datasets");

  // Un bloc replié n'est pas un dataset, un seed tient son nom de son CSV et
  // une source celui qu'elle porte en base : seul un modèle se renomme. Et
  // seul un modèle de *ce* projet : le fichier d'un paquet n'est pas ici, et
  // le renommage retombait alors sur le modèle local du même nom.
  if (d.node_type !== "group" && d.kind === "model") {
    items.push({ act: "rename", label: "Renommer…", sep: true, off: !d.editable,
                 help: d.editable
                   ? `Renomme ${d.name} partout : son fichier, sa doc, les `
                     + `ref() qui le nomment, et le script visuel qui l'écrit.`
                   : `${d.name} vient du paquet « ${d.package} » : il ne `
                     + `s'édite pas depuis l'atelier.` });
  }

  if (d.node_type === "group") {
    items.push({ act: "unfold", spec: d.fold, label: "Déplier ce bloc", n: d.count,
                 help: `Remet ${plural(d.count)} dans le Flow.` });
  } else {
    items.push({ act: "fold", spec: "up:" + d.id, label: "Replier l'amont",
                 n: d.up_count, off: !d.up_count,
                 help: d.up_count
                   ? `Remplace par un seul bloc ${plural(d.up_count)} en amont de `
                     + `${d.name}, de près ou de loin. ${d.name} reste visible.`
                   : `${d.name} n'a rien en amont.` });
    items.push({ act: "fold", spec: "down:" + d.id, label: "Replier l'aval",
                 n: d.down_count, off: !d.down_count,
                 help: d.down_count
                   ? `Remplace par un seul bloc ${plural(d.down_count)} en aval de `
                     + `${d.name}. ${d.name} reste visible.`
                   : `Rien n'est calculé à partir de ${d.name}.` });
    const zone = (state.flow.zones || []).find((z) => z.name === d.zone);
    if (zone && !zone.folded) {
      // Un seed n'est pas dans `models/`, une source n'est nulle part : on
      // nomme ce qu'on replie, pas un concept que personne n'a en tête.
      const what = zone.kind === "seed" ? "les seeds"
                 : zone.kind === "source" ? "les sources"
                 : `le dossier « ${zone.name} »`;
      const blockLabel = zone.kind === "seed"
        ? `${pluralize(zone.count, "seed", "seeds")} du projet.`
        : zone.kind === "source"
          ? `${pluralize(zone.count, "table source", "tables sources")} du projet.`
          : `${plural(zone.count)} du dossier models/${zone.name}/, où qu'ils`
            + ` soient dans le Flow.`;
      items.push({ act: "fold", spec: "zone:" + zone.name, n: zone.count,
                   label: `Replier ${what}`,
                   help: `Remplace par un seul bloc ${blockLabel}` });
    }
  }
  const nothing = (state.flow.folded || []).length === 0;

  menu.innerHTML =
    `<div class="grp">${esc(d.name)}</div>` +
    items.map((it, i) =>
      `<button type="button" data-i="${i}" title="${esc(it.help)}"` +
      `${it.off ? " disabled" : ""}>` +
      // Un « 0 » à côté d'une entrée grisée n'apprend rien : l'infobulle dit
      // déjà pourquoi elle l'est. Une action, elle, ne compte rien du tout.
      `${esc(it.label)}${it.off || it.n === undefined ? ""
        : `<span class="n">${it.n}</span>`}</button>` +
      (it.sep ? "<hr>" : "")).join("") +
    (nothing ? "" : `<hr><button type="button" data-all="1">Tout déplier</button>`) +
    `<p class="cmenu-note">Un bloc replié se rouvre d'un clic.</p>`;

  menu.querySelectorAll("[data-i]").forEach((b) =>
    b.addEventListener("click", () => {
      menu.classList.remove("on");
      const it = items[+b.dataset.i];
      if (it.act === "rename") openDatasetRename(d);
      else if (it.act === "unfold") removeFold(it.spec);
      else addFold(it.spec);
    }));
  const unfoldAllBtn = menu.querySelector("[data-all]");
  if (unfoldAllBtn) unfoldAllBtn.addEventListener("click", () => { menu.classList.remove("on"); unfoldAll(); });

  menu.style.left = Math.min(ev.clientX, window.innerWidth - 270) + "px";
  menu.style.top = Math.min(ev.clientY, window.innerHeight - 30 * (items.length + 3)) + "px";
  menu.classList.add("on");
}

/* Une recipe n'a ni amont ni aval propres — elle est le trait entre les deux.
   Son menu n'offre donc pas de pliage, mais ce qu'on fait d'elle : l'ouvrir,
   la jouer, la supprimer. */
function openRecipeMenu(r, ev) {
  ev.preventDefault();
  selectNode(r.id);
  const menu = $("flow-menu");
  const output = findDataset(r.model);
  // Un modèle de paquet n'a pas de script visuel ici, et son fichier n'est pas
  // dans ce projet : tout ce qui écrit doit rester fermé. Le laisser ouvert
  // faisait porter le geste sur le modèle local du même nom, silencieusement.
  const unpacked = `${r.model_name} vient du paquet « ${r.package} » : il ne `
    + `s'édite pas depuis l'atelier.`;
  const items = [
    { act: "open", label: r.external ? "Voir le SQL"
                        : r.managed ? "Ouvrir la recipe" : "Éditer le SQL",
      help: r.external
        ? `Le SQL de ${r.model_name}, tel que le paquet l'écrit. Lecture seule.`
        : r.managed
          ? "Reprend le script visuel, étape par étape."
          : `${r.model_name} est écrit à la main : l'atelier l'ouvre tel quel.` },
    { act: "build", label: "Construire", off: !output,
      help: `Joue dbt build sur ${r.model_name} seul.` },
    { act: "explore", label: "Explorer le résultat", off: !output,
      help: output
        ? `Les lignes de ${r.model_name}, telles qu'elles sont en base.`
        : "Le modèle produit n'est pas dans le Flow." },
    { act: "delete", label: "Supprimer…", danger: true, off: r.external,
      help: r.external ? unpacked
        : r.managed
          ? "Efface le script visuel, et au choix le modèle qu'il écrit."
          : `Efface le modèle ${r.model_name}.` },
  ];

  menu.innerHTML =
    `<div class="grp">${esc(r.model_name)}</div>` +
    items.map((it, i) =>
      `${it.danger ? "<hr>" : ""}<button type="button" data-i="${i}"` +
      `${it.danger ? ' class="danger"' : ""} title="${esc(it.help)}"` +
      `${it.off ? " disabled" : ""}>${esc(it.label)}</button>`).join("");

  menu.querySelectorAll("[data-i]").forEach((b) =>
    b.addEventListener("click", () => {
      menu.classList.remove("on");
      const act = items[+b.dataset.i].act;
      if (act === "open") openRecipeNode(r);
      else if (act === "build") runDbt("build", r.model_name);
      else if (act === "explore") openExplore(r.model);
      else if (act === "delete") openRecipeDelete(r);
    }));

  menu.style.left = Math.min(ev.clientX, window.innerWidth - 270) + "px";
  menu.style.top = Math.min(ev.clientY, window.innerHeight - 30 * (items.length + 2)) + "px";
  menu.classList.add("on");
}


const closeFlowMenu = () => $("flow-menu").classList.remove("on");

/* --------------------------------------------------------------- chargement */


/* --------------------------------------------------------------- chargement */

/* Le numéro du chargement en cours. Deux rafraîchissements qui reviennent dans
   le désordre réinstallaient l'ancien graphe — et ses replis étaient alors
   mémorisés sous le projet courant, y compris après une bascule. On n'applique
   que la dernière réponse demandée, et seulement si elle décrit le projet
   qu'on regarde encore. */
let flowGen = 0;

async function loadFlow(refresh = false) {
  const gen = ++flowGen;
  const { stale } = currentProject();
  try {
    loadFold();
    const q = [];
    if (refresh) q.push("refresh=true");
    if (state.fold.length) q.push("fold=" + encodeURIComponent(state.fold.join(",")));
    const f = await api(`/api/flow${q.length ? "?" + q.join("&") : ""}`);
    if (gen !== flowGen || stale()) return;
    state.flow = f;
    // Le serveur dit ce qu'il a réellement replié : un repli devenu impossible
    // — son nœud d'ancrage a disparu, ou il est lui-même dans un bloc — ne doit
    // pas survivre en fantôme dans le stockage.
    if (String(f.folded) !== String(state.fold)) { state.fold = (f.folded || []).slice(); saveFold(); }

    renderFlow();
    const groups = f.datasets.filter((d) => d.node_type === "group");
    const ds = f.datasets.length - groups.length;
    const caches = groups.reduce((n, b) => n + b.count, 0);
    $("flow-counts").innerHTML =
      `<b>${f.recipes.length}</b> recipes &nbsp; <b>${ds}</b> datasets` +
      (caches ? ` &nbsp;<span class="fold-note">+ ${caches} repliés</span>` : "");
    $("btn-unfold-all").hidden = !groups.length;
    if (f.manifest_error) toast("dbt parse", f.manifest_error, "error");
    if (state.selected && !f.datasets.some((d) => d.id === state.selected)
        && !f.recipes.some((r) => r.id === state.selected)) {
      state.selected = null;
      renderPanel();
    }
  } catch (e) {
    if (gen !== flowGen || stale()) return;
    toast("Flow indisponible", e.message, "error");
  }
}

/* ------------------------------------------------------------------ rendu */


/* ------------------------------------------------------------------ rendu */

function renderFlow() {
  const f = state.flow;
  const flow = $("flow");
  flow.querySelectorAll(".fnode").forEach((n) => n.remove());
  flow.style.width = f.width + "px";
  flow.style.height = f.height + "px";
  fnodeEls = new Map();

  const svgEl = $("flow-edges");
  svgEl.setAttribute("viewBox", `0 0 ${f.width} ${f.height}`);
  svgEl.setAttribute("width", f.width);
  svgEl.setAttribute("height", f.height);

  const groupW = f.group_w || 136;
  const pos = {};
  f.datasets.forEach((d) => (pos[d.id] = {
    x: d.cx, y: d.cy + f.ds_size / 2,
    r: (d.node_type === "group" ? groupW : f.ds_size) / 2,
  }));
  f.recipes.forEach((r) => (pos[r.id] = { x: r.cx, y: r.cy + f.ds_size / 2, r: f.rc_size / 2 }));

  const drawn = [];
  const paths = [];
  f.edges.forEach((e) => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return;
    const x1 = a.x + a.r + 2, x2 = b.x - b.r - 6;
    let d;
    if (e.via != null) {
      // Arête qui enjambe une colonne : le layout lui a réservé une voie libre,
      // sans quoi elle passerait derrière les nœuds et on ne la verrait pas.
      // Elle sort et rentre par le haut (ou le bas) des formes : entre deux
      // colonnes voisines il n'y a pas la place de redescendre sur le côté.
      const up = e.via < a.y;
      const ya = up ? a.y - a.r - 2 : a.y + a.r + 2;
      const yb = up ? b.y - b.r - 4 : b.y + b.r + 4;
      const k = Math.min(40, (b.x - a.x) / 3);
      d = `M${a.x} ${ya} C${a.x} ${e.via} ${a.x} ${e.via} ${a.x + k} ${e.via}` +
          ` L${b.x - k} ${e.via}` +
          ` C${b.x} ${e.via} ${b.x} ${e.via} ${b.x} ${yb}`;
    } else {
      const dx = Math.max(18, (x2 - x1) * 0.5);
      d = `M${x1} ${a.y} C${x1 + dx} ${a.y} ${x2 - dx} ${b.y} ${x2} ${b.y}`;
    }
    drawn.push(e);
    paths.push(`<path d="${d}"/>`);
  });
  svgEl.innerHTML = paths.join("");
  edgeEls = Array.from(svgEl.querySelectorAll("path"))
    .map((el, i) => ({ el, edge: drawn[i] }));

  buildLineage(f.edges);

  f.datasets.forEach((d) => {
    const node = d.node_type === "group" ? groupNode(d) : datasetNode(d);
    node.addEventListener("contextmenu", (ev) => openFlowMenu(d, ev));
    node.style.left = d.cx + "px";
    node.style.top = d.cy + "px";
    node.style.position = "absolute";
    flow.appendChild(node);
    fnodeEls.set(d.id, node);
  });

  f.recipes.forEach((r) => {
    const node = el("button", `fnode recipe ${r.managed ? "" : r.recipe_type === "sql" ? "sql" : ""}`);
    node.type = "button";
    node.style.left = r.cx + "px";
    node.style.top = (r.cy + (f.ds_size - f.rc_size) / 2) + "px";
    node.style.position = "absolute";
    node.innerHTML =
      `<span class="shape">${svg(RECIPE_ICON[r.recipe_type] || "sql")}</span>` +
      (r.steps ? `<span class="badge">${r.steps}</span>` : "") +
      `<span class="lbl">${esc(r.label)}</span>`;
    node.addEventListener("click", () => selectNode(r.id));
    node.addEventListener("dblclick", () => openRecipeNode(r));
    node.addEventListener("contextmenu", (ev) => openRecipeMenu(r, ev));
    flow.appendChild(node);
    fnodeEls.set(r.id, node);
  });

  paintStates();
  paintFlow();
  // Tant que le cadrage est automatique, il suit la forme du graphe : replier
  // une zone recadre, au lieu de laisser le flux dériver hors de l'écran.
  if ((!view.fitted || !view.touched) && fitView()) view.fitted = true;
  else applyView();
}


function datasetNode(d) {
  const node = el("button", `fnode dataset ${d.kind}`);
  node.type = "button";
  node.title = d.name + (d.path ? `\n${d.path}` : "");
  node.innerHTML =
    `<span class="shape">${svg(d.kind === "seed" ? "seed" : d.kind === "source" ? "source" : "model")}</span>
     <span class="stat idle"></span>
     ${d.kind === "source" ? '<span class="fresh"></span>' : ""}
     <span class="lbl">${esc(d.name)}</span>`;
  node.addEventListener("click", () => selectNode(d.id));
  node.addEventListener("dblclick", () => openExplore(d.id));
  return node;
}


function groupNode(g) {
  const node = el("button", "fnode group");
  node.type = "button";
  const preview = g.member_names.slice(0, 12).join(", ");
  node.title = `${g.name} — ${g.count} datasets repliés. Cliquez pour déplier.\n` +
               preview + (g.count > 12 ? `, … (+${g.count - 12})` : "");
  node.innerHTML =
    `<span class="shape">
       <span class="zn-count">${g.count}</span>
       <span class="zn-sub">datasets</span>
     </span>
     <span class="stat idle"></span>
     <span class="lbl">${esc(g.name)}</span>`;
  node.addEventListener("click", () => removeFold(g.fold));
  return node;
}

/* -------------------------------------------------- lignée, sélection, état */


/* -------------------------------------------------- lignée, sélection, état */

function buildLineage(edges) {
  upstream = new Map();
  downstream = new Map();
  const adds = (m, k, v) => { const a = m.get(k); if (a) a.push(v); else m.set(k, [v]); };
  (edges || []).forEach((e) => { adds(downstream, e.from, e.to); adds(upstream, e.to, e.from); });
}

/** Tout ce qui nourrit un nœud, et tout ce qu'il nourrit — lui compris. */

/** Tout ce qui nourrit un nœud, et tout ce qu'il nourrit — lui compris. */
function lineageOf(id) {
  const seen = new Set([id]);
  [upstream, downstream].forEach((direction) => {
    const file = [id];
    while (file.length) {
      for (const following of direction.get(file.pop()) || []) {
        if (!seen.has(following)) { seen.add(following); file.push(following); }
      }
    }
  });
  return seen;
}

/** Ce que la recherche fait ressortir, ou `null` si on ne cherche rien. */

/** Ce que la recherche fait ressortir, ou `null` si on ne cherche rien. */
function searchHits() {
  const q = state.find.trim().toLowerCase();
  if (!q || !state.flow) return null;
  const hits = new Set();
  (state.flow.datasets || []).forEach((d) => {
    if (String(d.name).toLowerCase().includes(q)) hits.add(d.id);
  });
  (state.flow.recipes || []).forEach((r) => {
    if (String(r.model_name || "").toLowerCase().includes(q)) hits.add(r.id);
  });
  return hits;
}

/* Sélection, lignée et recherche ne touchent que des classes : redessiner tout
   le graphe à chaque clic devenait le vrai coût, passé quelques centaines de
   nœuds. */
function paintFlow() {
  const sel = state.selected;
  const lineage = state.lineage && sel ? lineageOf(sel) : null;
  const hits = searchHits();
  const discards = (id) => (lineage && !lineage.has(id)) || (hits && !hits.has(id));

  fnodeEls.forEach((node, id) => {
    node.classList.toggle("on", id === sel);
    node.classList.toggle("hit", !!hits && hits.has(id));
    node.classList.toggle("off", discards(id));
  });
  edgeEls.forEach(({ el: path, edge }) => {
    path.classList.toggle("hot", edge.from === sel || edge.to === sel);
    path.classList.toggle("off", discards(edge.from) || discards(edge.to));
  });
}


const WORST = ["idle", "skipped", "success", "queued", "warn", "running", "error"];

/** Un bloc replié porte le pire état de ce qu'il cache. */

/** Un bloc replié porte le pire état de ce qu'il cache. */
function statusOf(node, states) {
  if (node.node_type !== "group") return (states[node.id] || {}).status || "idle";
  return (node.members || []).reduce((worst, uid) => {
    const s = (states[uid] || {}).status || "idle";
    return WORST.indexOf(s) > WORST.indexOf(worst) ? s : worst;
  }, "idle");
}


function paintFreshness() {
  ((state.flow || {}).datasets || []).forEach((d) => {
    const node = fnodeEls.get(d.id);
    const dot = node && node.querySelector(".fresh");
    if (!dot) return;
    const f = freshnessOf(d.id);
    dot.className = "fresh" + (f ? ` on ${f.status}` : "");
    dot.title = f
      ? `Fraîcheur : ${FRESH_LABEL[f.status] || f.raw_status}` +
        (f.age_seconds != null ? ` — dernière ligne il y a ${humanDuration(f.age_seconds)}` : "")
      : "";
  });
}


function paintStates() {
  const f = state.flow;
  if (!f) return;
  const states = f.states || {};
  (f.datasets || []).forEach((d) => {
    const node = fnodeEls.get(d.id);
    const dot = node && node.querySelector(".stat");
    if (dot) dot.className = "stat " + statusOf(d, states);
  });
  paintFreshness();
}


function selectNode(id) {
  state.selected = id;
  paintFlow();
  renderPanel();
}


function findRecipe(id) { return (state.flow.recipes || []).find((r) => r.id === id); }
function findDataset(id) { return (state.flow.datasets || []).find((d) => d.id === id); }


async function renderPanel() {
  const head = $("rpanel-head"), body = $("rpanel-body");
  if (!state.selected) {
    head.innerHTML = `<div class="rpanel-title"><h2>Flow</h2></div>
      <p class="rpanel-sub">Sélectionnez un dataset pour agir dessus.</p>`;
    body.innerHTML = "";
    return;
  }

  const rec = findRecipe(state.selected);
  if (rec) return renderRecipePanel(rec);

  const d = findDataset(state.selected);
  if (!d) return;

  head.innerHTML =
    `<div class="rpanel-title">
       <span class="mini">${svg(d.kind === "seed" ? "seed" : d.kind === "source" ? "source" : "model")}</span>
       <h2>${esc(d.name)}</h2>
     </div>
     <p class="rpanel-sub">${esc(d.path || d.relation || "")}</p>`;

  body.innerHTML = "";
  const actions = el("div", "actions-row");
  const explore = el("button", "btn sm", "Explorer");
  explore.addEventListener("click", () => openExplore(d.id));
  actions.appendChild(explore);
  if (d.kind !== "source") {
    const build = el("button", "btn sm", "Construire");
    build.addEventListener("click", () => runDbt("build", d.name));
    actions.appendChild(build);
  }
  // Un dataset installé par `dbt deps` se voit, s'explore et se référence,
  // mais aucun de ses fichiers n'est dans ce projet : documenter, éditer ou
  // renommer écrirait à côté — au mieux dans le vide, au pire sur le modèle
  // local qui porte le même nom.
  const external = d.external === true;
  if (!external) {
    const tBtn = el("button", "btn sm", "Documenter & tester");
    tBtn.addEventListener("click", () => openTestsModal(d.id));
    actions.appendChild(tBtn);
  }
  if (d.kind === "model") {
    const generated = generatedByRecipe(d.id);
    const rw = !generated && !external;
    const sqlBtn = el("button", "btn sm", rw ? "Modifier le SQL" : "Voir le SQL");
    sqlBtn.addEventListener("click", () => openSqlModal(d.id, rw));
    actions.appendChild(sqlBtn);
    // Renommer, c'est renommer *ce* dataset. L'action était sur la recipe, qui
    // n'a pourtant pas de nom à elle : elle porte celui du modèle qu'elle
    // écrit, et on cherchait le renommage là où on voit la table.
    if (!external) {
      const renamed = el("button", "btn sm", "Renommer");
      renamed.title = "Renomme le modèle partout : son fichier, sa doc, "
        + "les ref() qui le nomment, et le script visuel qui l'écrit.";
      renamed.addEventListener("click", () => openDatasetRename(d));
      actions.appendChild(renamed);
    }
  }
  body.appendChild(actions);
  if (external)
    body.appendChild(el("div", "hint",
      `Installé par le paquet <b>${esc(d.package || "")}</b> : il se lit, `
      + `s'explore et se référence, mais ses fichiers vivent dans le dépôt du `
      + `paquet — pas ici.`));

  body.appendChild(el("div", "sec-h", "Recipes visuelles"));
  const grid = el("div", "recipe-grid");
  const types = [
    ["prepare", "Préparer"], ["join", "Joindre"], ["group", "Grouper"],
    ["stack", "Empiler"], ["distinct", "Dédoublonner"], ["sql", "SQL"],
  ];
  types.forEach(([key, label]) => {
    const tile = el("button", `recipe-tile${key === "sql" ? " code" : ""}`);
    tile.type = "button";
    tile.innerHTML = `<span class="ci">${svg(RECIPE_ICON[key])}</span><span>${esc(label)}</span>`;
    tile.addEventListener("click", () => openNewRecipe(key, d));
    grid.appendChild(tile);
  });
  body.appendChild(grid);

  body.appendChild(el("div", "sec-h", "Détails"));
  const facts = [
    ["Type", d.kind], ["Matérialisation", d.materialized],
    ["Schéma", `${d.database || ""}.${d.schema || ""}`.replace(/^\./, "")],
    ["Couche", d.layer],
  ];
  const st = (state.flow.states || {})[d.id];
  if (st) {
    facts.push(["Statut", st.status]);
    if (st.execution_time) facts.push(["Durée", st.execution_time.toFixed(2) + " s"]);
  }
  facts.forEach(([k, v]) => {
    if (!v) return;
    body.appendChild(el("dl", "kv", `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`));
  });

  if (d.kind === "source") renderFreshnessPanel(body, d);

  if (d.tests.length) {
    body.appendChild(el("div", "sec-h", `Tests (${d.tests.length})`));
    const ul = el("ul", "tlist");
    d.tests.forEach((t) => {
      const s = ((state.flow.states || {})[t.id] || {}).status || "idle";
      const li = el("li");
      li.innerHTML =
        `<span class="pill ${esc(s)}">${esc(statusLabel(s))}</span>
         <span class="nm">${esc(t.name)}</span><span class="cm">${esc(t.column || "")}</span>`;
      // Un test rouge qu'on ne peut pas ouvrir ne dit que la moitié : on va
      // chercher les lignes fautives, que dbt sait déjà sélectionner.
      li.classList.add("clic");
      li.title = "Voir les lignes qui font échouer ce test";
      li.addEventListener("click", () => openTestFailures(t));
      ul.appendChild(li);
    });
    body.appendChild(ul);
  }
}

/* La fraîcheur mérite sa place ici : le statut au-dessus dit que le dernier
   build a réussi, pas que les données sont récentes. */
function renderFreshnessPanel(body, d) {
  body.appendChild(el("div", "sec-h", "Fraîcheur"));
  const f = freshnessOf(d.id);
  if (!f) {
    body.appendChild(el("p", "hint",
      `Jamais mesurée. Lancez <code>source freshness</code> depuis la barre du
       haut — et donnez d'abord à cette source sa colonne de chargement, dans
       « Documenter &amp; tester » : sans elle, dbt saute la source.`));
    return;
  }
  body.appendChild(el("dl", "kv",
    `<dt>État</dt><dd><span class="pill ${esc(f.status)}">${esc(FRESH_LABEL[f.status] || f.raw_status)}</span></dd>`));
  if (f.age_seconds != null)
    body.appendChild(el("dl", "kv",
      `<dt>Dernière ligne</dt><dd>il y a ${esc(humanDuration(f.age_seconds))}</dd>`));
  if (f.max_loaded_at)
    body.appendChild(el("dl", "kv",
      `<dt>Chargée le</dt><dd>${esc(String(f.max_loaded_at).replace("T", " ").slice(0, 19))}</dd>`));
  [["warn_after", "Avertir après"], ["error_after", "Échouer après"]].forEach(([k, label]) => {
    const s = (f.criteria || {})[k];
    if (s && s.count)
      body.appendChild(el("dl", "kv",
        `<dt>${esc(label)}</dt><dd>${esc(s.count)} ${esc(PERIOD_LABEL[s.period] || s.period || "")}</dd>`));
  });
  if (f.checked_at)
    body.appendChild(el("p", "hint",
      `Mesurée le ${esc(String(f.checked_at).replace("T", " ").slice(0, 19))}.`));
}


function renderRecipePanel(r) {
  const head = $("rpanel-head"), body = $("rpanel-body");
  head.innerHTML =
    `<div class="rpanel-title">
       <span class="mini" style="background:var(--rc); color:var(--rc-ink); border-radius:50%">${svg(RECIPE_ICON[r.recipe_type] || "sql")}</span>
       <h2>Recipe ${esc(r.label)}</h2>
     </div>
     <p class="rpanel-sub">produit <b>${esc(r.model_name)}</b></p>`;
  body.innerHTML = "";
  const actions = el("div", "actions-row");
  const open = el("button", "btn sm primary",
    r.external ? "Voir le SQL" : r.managed ? "Ouvrir la recipe" : "Éditer le SQL");
  open.addEventListener("click", () => openRecipeNode(r));
  actions.appendChild(open);
  const build = el("button", "btn sm", "Construire");
  build.addEventListener("click", () => runDbt("build", r.model_name));
  actions.appendChild(build);
  // Rien qui écrive sur un modèle de paquet : son fichier n'est pas dans ce
  // projet, et le bouton portait en fait sur le modèle local homonyme.
  if (!r.external) {
    const del = el("button", "btn sm danger", "Supprimer");
    del.title = "Efface le script visuel, et au choix le modèle qu'il écrit.";
    del.addEventListener("click", () => openRecipeDelete(r));
    actions.appendChild(del);
  }
  body.appendChild(actions);

  if (r.external) {
    body.appendChild(el("div", "empty",
      `<b>Modèle de paquet</b>${esc(r.model_name)} vient de
       <code>${esc(r.package)}</code>, installé par <code>dbt deps</code>.
       L'atelier le lit et le construit, mais ne l'édite pas : son fichier est
       dans le dépôt du paquet.`));
  } else if (!r.managed) {
    body.appendChild(el("div", "empty",
      "<b>Recipe SQL</b>Ce modèle a été écrit à la main. L'atelier ne le décompose pas en étapes : il l'affiche tel quel et l'exécute."));
  } else {
    body.appendChild(el("div", "kv",
      `<dt>Étapes actives</dt><dd>${r.steps}</dd>`));
  }
}

/* ------------------------------------------------- supprimer une recipe */

/* Deux choses portent le même nom : le script visuel et le modèle qu'il écrit.
   Les séparer est utile — oublier le script en gardant le SQL rend le modèle à
   la main — mais il faut le dire, sinon on croit avoir tout effacé. */


let rdelTarget = null;
let rdelProject = null;

function resetRecipeDelete() {
  rdelTarget = null;
  rdelProject = null;
}

/** Les datasets qui descendent d'un nœud, de près ou de loin. */

/** Les datasets qui descendent d'un nœud, de près ou de loin. */
function downstreamDatasets(id) {
  const names = [];
  const seen = new Set([id]);
  const file = [id];
  while (file.length) {
    for (const following of downstream.get(file.pop()) || []) {
      if (seen.has(following)) continue;
      seen.add(following);
      file.push(following);
      const d = findDataset(following);
      if (d) names.push(d.name);
    }
  }
  return names;
}


function openRecipeDelete(r) {
  closeFlowMenu();
  if (r.external) {
    return toast("Modèle de paquet",
      `${r.model_name} vient du paquet « ${r.package} » : il ne se supprime `
      + `pas depuis l'atelier.`, "error");
  }
  rdelTarget = r;
  rdelProject = projectDir();
  const output = findDataset(r.model);
  const path = (output && output.path) || `${r.model_name}.sql`;

  $("rdel-title").textContent = `Supprimer la recipe de « ${r.model_name} »`;
  $("rdel-warn").innerHTML = r.managed
    ? `Le script visuel <code>.pliq/recipes/${esc(r.model_name)}.yml</code> sera
       effacé du disque. <b>C'est irréversible.</b>`
    : `<b>${esc(r.model_name)}</b> est écrit à la main : il n'a pas de script
       visuel. Supprimer sa recipe revient à effacer le modèle
       <code>${esc(path)}</code>. <b>C'est irréversible.</b>`;

  const downstream = output ? downstreamDatasets(output.id) : [];
  if (downstream.length) {
    $("rdel-warn").innerHTML +=
      `<br><br>${downstream.length === 1 ? "<b>1 dataset</b> est calculé"
                                   : `<b>${downstream.length} datasets</b> sont calculés`}
       à partir de ${esc(r.model_name)} : ${esc(downstream.slice(0, 6).join(", "))}${
         downstream.length > 6 ? "…" : ""}. Sans le modèle, ${
         downstream.length === 1 ? "il ne se construira plus" : "ils ne se construiront plus"}.`;
  }

  // Un modèle à la main n'a que lui-même à perdre : plutôt qu'une case cochée
  // qu'on ne peut pas décocher, on ne montre pas de choix du tout.
  $("rdel-model").checked = true;
  $("rdel-choix").hidden = !r.managed;
  $("rdel-model-lbl").innerHTML =
    `supprimer aussi le modèle <code>${esc(path)}</code> — décoché, il reste
     dans le Flow, écrit à la main`;

  $("rdel-err").textContent = "";
  $("rdel-go").disabled = false;
  $("rdel-scrim").classList.add("on");
  $("rdel-cancel").focus();
}


let rrenTarget = null;
// L'identité du modèle visé, à côté de son nom : c'est elle qui décide, et
// le serveur refuse si les deux ne désignent pas le même modèle.
// L'identité du modèle visé, à côté de son nom : c'est elle qui décide, et
// le serveur refuse si les deux ne désignent pas le même modèle.
let rrenUid = null;
let rrenProject = null;
let rrenTimer = null;


function openRename(name, uid) {
  closeFlowMenu();
  // Le renommage porte sur les fichiers du projet. Un script modifié à l'écran
  // et pas encore enregistré resterait derrière, sous l'ancien nom.
  if (state.rec && state.rec.spec.name === name && state.rec.dirty) {
    return toast("Recipe non enregistrée",
      "Enregistrez d'abord : le renommage porte sur les fichiers du projet, " +
      "pas sur le script affiché.", "error");
  }
  rrenTarget = name;
  rrenUid = uid || null;
  rrenProject = projectDir();
  $("rren-title").textContent = `Renommer « ${name} »`;
  $("rren-name").value = name;
  $("rren-err").textContent = "";
  $("rren-warn").hidden = true;
  $("rren-go").disabled = true;
  $("rren-hint").innerHTML =
    `Lettres, chiffres et tirets bas. Le modèle garde sa couche : seul son nom
     change, dans le projet comme dans le Flow.`;
  $("rren-scrim").classList.add("on");
  $("rren-name").focus();
  $("rren-name").select();
}


const openDatasetRename = (d) => openRename(d.name, d.id);


function closeRename() {
  clearTimeout(rrenTimer);
  $("rren-scrim").classList.remove("on");
  rrenTarget = null;
  rrenUid = null;
  rrenProject = null;
}


/** L'aperçu du serveur : ce que le renommage toucherait, sans rien écrire. */
async function previewRename() {
  const previous = rrenTarget;
  if (!previous) return;
  const name = $("rren-name").value.trim();
  const stale = () => rrenTarget !== previous || $("rren-name").value.trim() !== name;

  if (!name || name === previous) {
    $("rren-warn").hidden = true;
    return;
  }
  let out;
  try {
    out = await post("/api/recipe/rename",
      { name: previous, new_name: name, dry_run: true, model_uid: rrenUid },
      { project: rrenProject });
  } catch (e) {
    if (stale()) return;
    $("rren-warn").hidden = true;
    $("rren-err").textContent = e.message;
    return;
  }
  if (stale()) return;
  $("rren-err").textContent = "";
  $("rren-go").disabled = false;
  renderRenamePreview(out);
}


function renderRenamePreview(p) {
  const followed = p.updates || [];
  const bits = [`Le modèle ira dans <code>${esc(p.model_path)}</code>.`];

  // Ce que la recipe a de propre, c'est son fichier : le dire lève la question
  // que pose le renommage vu depuis le dataset — « et mon script, alors ? ».
  if (p.recipe_path) {
    bits.push(`Son script visuel suit, en <code>${esc(p.recipe_path)}</code> :
               le renommer d'un côté sans l'autre ferait renaître l'ancien
               modèle au prochain enregistrement.`);
  }

  bits.push(followed.length
    ? `${followed.length === 1 ? "<b>1 fichier</b> suivra" : `<b>${followed.length} fichiers</b> suivront`} :
       ${followed.slice(0, 6).map((f) => `<code>${esc(f)}</code>`).join(", ")}${
         followed.length > 6 ? "…" : ""}.`
    : "Rien d'autre dans le projet ne le nomme.");

  if (p.doc_follows) {
    bits.push(`Sa description et ses tests suivent, à leur place dans
               <code>${esc(p.schema_path)}</code>.`);
  }

  // dbt ne renomme pas ce qu'il a construit : il construit à côté. Le dire
  // ici, c'est éviter qu'on découvre deux tables la semaine suivante.
  if (p.old_relation) {
    bits.push(`La table déjà construite <code>${esc(p.old_relation)}</code> garde
               l'ancien nom. Le prochain <code>dbt build</code> en crée une sous
               le nouveau ; l'ancienne reste à retirer de l'entrepôt à la main.`);
  }
  $("rren-warn").innerHTML = bits.join("<br><br>");
  $("rren-warn").hidden = false;
}



function statusLabel(s) {
  return { success: "passé", error: "échec", warn: "alerte", running: "en cours",
           queued: "en attente", skipped: "ignoré", idle: "jamais joué" }[s] || s;
}

/* --------------------------------------------------------------- EXPLORE */

export {
  applyView,
  centerOnNode,
  clampView,
  closeFlowMenu,
  closeRename,
  findDataset,
  fitView,
  loadFlow,
  paintFlow,
  paintStates,
  renderPanel,
  resetRecipeDelete,
  searchHits,
  unfoldAll,
  view,
  zoomAt,
  zoomBy,
};

/* Les branchements de ce module. Appelé par `main.js` une fois
   tous les modules évalués : aucun effet de bord ne doit avoir lieu
   pendant le chargement, sinon les imports croisés se mordent la
   queue. */
export function wire() {

  $("rdel-cancel").addEventListener("click", () => $("rdel-scrim").classList.remove("on"));
  $("rdel-go").addEventListener("click", async () => {
    const r = rdelTarget;
    if (!r) return;
    $("rdel-err").textContent = "";
    $("rdel-go").disabled = true;
    let out;
    try {
      out = await post("/api/recipe/delete", {
        name: r.model_name, delete_model: $("rdel-model").checked,
        // Le nom seul ne désigne pas un modèle : `orders` peut être celui du
        // projet comme celui d'un paquet. Le serveur refuse si les deux ne se
        // rejoignent pas.
        model_uid: r.model,
      }, { project: rdelProject });
    } catch (e) {
      $("rdel-err").textContent = e.message;
      $("rdel-go").disabled = false;
      return;
    }
    $("rdel-scrim").classList.remove("on");
    rdelTarget = null;
    // Un modèle supprimé doit disparaître des menus de références.
    refreshRefs();

    // L'éditeur ouvert sur ce qu'on vient d'effacer n'a plus rien à enregistrer.
    if (state.view === "recipe" && state.rec && state.rec.spec.name === r.model_name) {
      state.rec = null;
      show("flow");
    }
    state.selected = null;
    toast("Recipe supprimée", (out.deleted || []).join(", "));
    if (out.parse_error) toast("dbt parse", out.parse_error, "error");
    await loadFlow(false);
    renderPanel();
  });


  $("rec-delete").addEventListener("click", () => {
    const rec = state.rec;
    if (!rec) return;
    // Par identité quand on l'a, par nom seulement à défaut : deux recipes
    // peuvent porter le même `model_name` si un paquet déclare un homonyme, et
    // la première trouvée n'est pas forcément celle qui est ouverte.
    const recipes = state.flow.recipes || [];
    const node = (rec.modelUid && recipes.find((r) => r.model === rec.modelUid))
      || recipes.find((r) => r.model_name === rec.spec.name && !r.external);
    if (!node) {
      return toast("Rien à supprimer", "Cette recipe n'a pas encore été enregistrée.");
    }
    openRecipeDelete(node);
  });

  /* ---------------------------------------------------- renommer un dataset */

  /* Le renommage porte sur le *dataset*, et l'action est donc sur lui. Une recipe
     n'a pas de nom à elle : elle porte celui du modèle qu'elle écrit, et son
     script s'appelle `.pliq/recipes/<ce nom>.yml`. L'offrir depuis la recipe
     faisait chercher le renommage à l'endroit où l'on ne voit pas la table.

     Un modèle dbt porte ce nom à cinq endroits : son fichier `.sql`, son script
     visuel, l'entrée de `schema.yml` qui le documente, les `ref()` de ce qui le
     lit, et les entrées des scripts visuels d'aval. Le serveur les suit tous —
     et comme il connaît le DAG, il peut dire lesquels *avant* d'écrire. C'est ce
     que la modale montre pendant qu'on tape : un aperçu, pas une promesse. */


  $("rren-cancel").addEventListener("click", closeRename);
  $("rren-scrim").addEventListener("click", (e) => {
    if (e.target === $("rren-scrim")) closeRename();
  });


  $("rren-name").addEventListener("input", () => {
    clearTimeout(rrenTimer);
    $("rren-go").disabled = true;
    $("rren-err").textContent = "";
    rrenTimer = setTimeout(previewRename, 250);
  });


  $("rren-name").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !$("rren-go").disabled) $("rren-go").click();
  });

  /** L'aperçu du serveur : ce que le renommage toucherait, sans rien écrire. */

  $("rren-go").addEventListener("click", async () => {
    const previous = rrenTarget;
    const name = $("rren-name").value.trim();
    if (!previous || !name) return;
    // Retenu avant l'aller-retour, et avant que `closeRename()` ne l'oublie :
    // c'est le projet de ce renommage-là que l'éditeur rouvert doit porter.
    const project = rrenProject;
    $("rren-err").textContent = "";
    $("rren-go").disabled = true;
    let out;
    try {
      out = await post("/api/recipe/rename",
                       { name: previous, new_name: name, model_uid: rrenUid },
                       { project: rrenProject });
    } catch (e) {
      $("rren-err").textContent = e.message;
      $("rren-go").disabled = false;
      return;
    }
    closeRename();

    // L'éditeur resté ouvert parle d'un fichier qui n'existe plus. On le rouvre
    // sur le nouveau nom, avec les empreintes que le serveur vient d'écrire :
    // sans elles, la sauvegarde suivante se croirait en conflit.
    if (state.view === "recipe" && state.rec && state.rec.spec.name === previous) {
      try {
        const d = await api(`/api/recipe/${encodeURIComponent(out.to)}`
                            + (out.unique_id
                               ? `?model_uid=${encodeURIComponent(out.unique_id)}` : ""),
                            { project: project });
        openRecipeEditor(d.spec, false, d.base, out.unique_id, project);
      } catch (_) {
        state.rec = null;
        show("flow");
      }
    }
    state.selected = null;
    toast("Modèle renommé",
      `${previous} → ${out.to}` +
      ((out.updates || []).length
        ? ` · ${out.updates.length} fichier(s) ont suivi` : ""), "ok");
    if (out.parse_error) toast("dbt parse échoue", out.parse_error, "error");
    // Le graphe a changé de nom : les menus « Entrée » doivent l'apprendre,
    // sinon l'ancien nom y reste jusqu'au rechargement de la page.
    refreshRefs();
    await loadFlow(true);
    renderPanel();
  });

  // Dans l'éditeur, le nom affiché en gros *est* celui du modèle produit : le
  // renommer depuis là ne demande aucun détour par le Flow.

  // Dans l'éditeur, le nom affiché en gros *est* celui du modèle produit : le
  // renommer depuis là ne demande aucun détour par le Flow.
  $("rec-rename").addEventListener("click", () => {
    const rec = state.rec;
    if (!rec) return;
    if (rec.isNew) {
      return toast("Rien à renommer", "Cette recipe n'a pas encore été enregistrée.");
    }
    openRename(rec.spec.name, rec.modelUid);
  });
}
