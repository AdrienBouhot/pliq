/* L'éditeur de recipe : le script, ses étapes, l'aperçu, l'enregistrement.
 *
 * Il tenait aussi la configuration de sortie, la fiche de tests d'un dataset
 * et l'éditeur de listes de valeurs — deux mille trois cents lignes pour
 * quatre métiers. Ils sont partis dans `output.js`, `card.js` et
 * `widgets.js`, qui ne dépendent que de feuilles et n'appellent rien ici :
 * c'est ce module qui les connaît, jamais l'inverse.
 */
import { $, FIELDS, OPS, RECIPE_ICON, WINDOW_FNS, el, esc, svg, uid } from "./core.js";
import { MAT_HELP, state } from "./state.js";
import { api, post, projectDir, currentProject, put, refreshRefs, show, toast } from "./api.js";
import { loadFlow } from "./flow.js";
import { buildGrid } from "./explore.js";
import { recipeTestsModel, renderRecipeTests, testsProblem } from "./tests.js";
import { hlSql, sqlEditor } from "./runs.js";
import { refreshLayerSuggestion } from "./boot.js";
import { VISUAL_TYPES, inputLabel, loadInputColumns } from "./canvas.js";
import { renderGroupEditor, renderJoinEditor, renderStackEditor } from "./editors.js";
import { askConfirm } from "./confirm.js";
import { countTests } from "./card.js";
import { installOutput, renderOutputPane } from "./output.js";
import { valuesEditor } from "./widgets.js";


/* ------------------------------------------------------ nouvelle recipe */

let newRecipeCtx = null;


async function openNewRecipe(type, dataset) {
  // Relue à chaque ouverture, et pas seulement quand elle est vide : un modèle
  // créé ou renommé depuis le dernier chargement manquait sinon au menu.
  await refreshRefs();
  if (!state.refs.length) return toast(
    "Références illisibles",
    "L'atelier n'a trouvé aucun dataset à mettre en entrée.", "error");
  // L'arité est celle que le serveur déclare — « 1 », « 1+ », « 2+ » — et non
  // une liste de types recopiée ici : les deux finiraient par diverger, et
  // c'est le compilateur qui refuserait la recipe, trop tard.
  const arity = (state.recipeTypes[type] || {}).inputs
    || (type === "join" || type === "stack" ? "2+" : "1");
  const min = parseInt(arity, 10) || 1;
  const multi = arity.endsWith("+");
  newRecipeCtx = { type, inputs: [refFor(dataset)], multi, min };
  while (newRecipeCtx.inputs.length < min) newRecipeCtx.inputs.push(null);

  $("nr-icon").innerHTML = svg(RECIPE_ICON[type] || "sql");
  $("nr-title").textContent = "Nouvelle recipe " + labelOf(type);
  $("nr-in-title").textContent = multi ? "Datasets d'entrée" : "Dataset d'entrée";
  $("nr-add-input").hidden = !multi;
  $("nr-name").value = suggestName(dataset.name, type);
  $("nr-desc").value = "";
  $("nr-err").textContent = "";
  renderMatHint();
  renderNewInputs();
  refreshLayerSuggestion();
  $("scrim").classList.add("on");
  $("nr-name").focus();
}


function labelOf(t) {
  return { prepare: "Préparer", join: "Joindre", group: "Grouper", stack: "Empiler",
           distinct: "Dédoublonner", sql: "SQL" }[t] || t;
}
function suggestName(base, type) {
  const clean = base.replace(/[^a-zA-Z0-9_]/g, "_");
  return { prepare: clean + "_prepared", join: clean + "_joined", group: clean + "_grouped",
           stack: clean + "_stacked", distinct: clean + "_distinct", sql: clean + "_sql" }[type]
    || clean + "_out";
}
function refFor(d) {
  const r = state.refs.find((x) => x.id === d.id);
  // Le repli sert quand l'inventaire des refs n'a pas encore vu ce dataset —
  // il doit porter la même identité, `id` compris : c'est par lui que le
  // sélecteur retrouve le choix.
  return r || { kind: "ref", label: d.name, ref: d.name, id: d.id,
                package: d.external ? (d.package || "") : "",
                version: d.version || "" };
}


function renderNewInputs() {
  const box = $("nr-inputs");
  box.innerHTML = "";
  newRecipeCtx.inputs.forEach((inp, i) => {
    const fld = el("div", "fld");
    // La valeur de l'option est l'`id` du nœud, pas son libellé : deux
    // datasets peuvent porter le même nom — un homonyme de paquet, deux
    // versions — et retrouver le choix par le libellé rendait le premier des
    // deux, donc pas toujours celui qu'on venait de désigner.
    const options = state.refs.map((r) =>
      `<option value="${esc(r.id)}"${inp && r.id === inp.id ? " selected" : ""}>${esc(r.label)}</option>`).join("");
    fld.innerHTML =
      `<label>Entrée ${i + 1}</label>
       <select class="inp" data-in="${i}"><option value="">— choisir —</option>${options}</select>`;
    box.appendChild(fld);
  });
  box.querySelectorAll("[data-in]").forEach((s) =>
    s.addEventListener("change", () => {
      newRecipeCtx.inputs[+s.dataset.in] = state.refs.find((r) => r.id === s.value) || null;
      refreshLayerSuggestion();
    }));
}

// L'incrémental demande une stratégie et, souvent, une clé unique — deux
// choses qui se choisissent sur les vraies colonnes, donc pas ici. On le dit,
// et l'éditeur s'ouvrira directement sur l'onglet qui les demande.
function renderMatHint() {
  const mat = $("nr-mat").value;
  $("nr-mat-hint").innerHTML = esc(MAT_HELP[mat] || "") + (mat === "incremental"
    ? ` <b>L'onglet Sortie s'ouvrira pour régler la stratégie et la clé unique.</b>`
    : "");
}
function toSpecInput(r) {
  if (r.kind === "source")
    return { source_name: r.source_name, table: r.table, alias: r.table };
  const inp = { ref: r.ref, alias: r.ref };
  if (r.package) inp.package = r.package;
  if (r.version) inp.version = r.version;
  return inp;
}

/* Les entrées d'une recipe, avec des alias qui tiennent comme noms de CTE et
   qui ne se ressemblent pas. Deux fois le même dataset — une autojointure — et
   les deux CTE s'appelaient pareil : l'entrepôt répondait `Duplicate CTE name`,
   loin de la case où le choix avait été fait. Le serveur refuse le doublon ;
   ici on fait en sorte qu'il n'y en ait jamais. */
function toSpecInputs(rows) {
  const taken = new Set();
  return rows.map((r, i) => {
    const inp = toSpecInput(r);
    inp.alias = freeAlias(inp.alias, i, taken);
    return inp;
  });
}

/* Jumeau d'`input_alias` côté serveur : un nom dbt n'est pas un identifiant —
   `order-items` est une table de source valide — et un CTE, si. */
function slugAlias(rawText, i) {
  const t = String(rawText || "").trim().replace(/[^A-Za-z0-9_]/g, "_");
  if (!t) return `input_${i}`;
  return /^[A-Za-z_]/.test(t) ? t : `_${t}`;
}


function freeAlias(rawText, i, taken) {
  const base = slugAlias(rawText, i);
  let name = base, n = 1;
  while (taken.has(name)) { n += 1; name = `${base}_${n}`; }
  taken.add(name);
  return name;
}

/* Le Jinja qui désigne une entrée de recipe, modèle ou source. Jumeau de
   `ref_sql` côté serveur : les deux doivent écrire la même chose. */
function jinjaRef(inp) {
  if (!inp.ref) return `{{ source(${jinjaStr(inp.source_name)}, ${jinjaStr(inp.table)}) }}`;
  const args = [jinjaStr(inp.ref)];
  if (inp.package) args.unshift(jinjaStr(inp.package));
  if (inp.version) args.push(`v=${/^\d+$/.test(String(inp.version)) ? inp.version : jinjaStr(inp.version)}`);
  return `{{ ref(${args.join(", ")}) }}`;
}

/* Jumeau de `jinja_str` côté serveur. Jinja lit ses chaînes comme Python :
   l'antislash s'échappe en premier, sinon il échappe l'échappement. */
function jinjaStr(value) {
  return `'${String(value == null ? "" : value).replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`;
}

/* ------------------------------------------------------- éditeur recipe */


/* ------------------------------------------------------- éditeur recipe */

async function openRecipeNode(r) {
  if (!r.managed) return openSqlModal(r.model, !r.external);
  const ctx = currentProject();
  try {
    // `model_uid` avec le nom : le nom seul ne dit pas de quel paquet on parle,
    // et deux modèles homonymes n'ont pas le même script visuel — ni le même
    // droit d'être édités.
    const d = await api(`/api/recipe/${encodeURIComponent(r.model_name)}`
                        + `?model_uid=${encodeURIComponent(r.model)}`,
                        { project: ctx.project });
    if (ctx.stale()) return;
    openRecipeEditor(d.spec, false, d.base, r.model, ctx.project);
  } catch (e) {
    toast("Recipe illisible", e.message, "error");
  }
}


function selectRecipeTab(key) {
  document.querySelectorAll("[data-stab]").forEach((t) =>
    t.classList.toggle("on", t.dataset.stab === key));
  ["script", "output", "tests"].forEach((k) =>
    $("spane-" + k).classList.toggle("on", k === key));
  if (key === "output") renderOutputPane();
  if (key === "tests") renderRecipeTests();
}

// `base` porte les empreintes des fichiers tels que le serveur vient de les
// lire. On les lui rend à l'enregistrement : c'est ce qui lui permet de refuser
// d'écraser un modèle modifié entre-temps, ici, dans un autre onglet ou à la
// main dans un éditeur de texte.
function openRecipeEditor(spec, isNew, base, modelUid, project) {
  state.rec = { spec, preview: null, previewStep: null, openStep: null, dirty: isNew,
                isNew, base: base || null, inputCols: null, colError: "",
                // L'identité du modèle produit, rendue à chaque écriture. Nulle
                // pour une recipe neuve : le modèle n'existe pas encore.
                modelUid: modelUid || null,
                // Le projet d'où vient cette recipe, rendu à chaque écriture.
                // Il vient de l'appelant, qui l'a retenu avant sa lecture :
                // le relire ici nommerait le projet où l'on vient de basculer.
                project: project || projectDir(),
                // L'état d'ouverture est le point de départ de l'historique, et
                // — sauf pour une recipe neuve — celui qui est sur le disque.
                hist: [JSON.stringify(spec)], histIdx: 0,
                // L'état enregistré est repéré par sa *valeur*, pas par son
                // rang dans l'historique : « annuler » tronque la branche
                // future (`hist.length = histIdx + 1`), et un index conservé
                // désignait alors une révision qui n'existe plus — la
                // suivante prenait sa place, et le bandeau annonçait
                // ENREGISTRÉ un script qui n'a jamais touché le disque.
                // Quitter l'écran ne demandait alors aucune confirmation.
                savedSnap: isNew ? null : JSON.stringify(spec) };
  show("recipe");
  // Rien à supprimer tant que rien n'est écrit.
  $("rec-delete").hidden = isNew;
  // Renommer, c'est déplacer des fichiers : il n'y en a pas tant qu'on n'a
  // pas enregistré une première fois.
  $("rec-rename").hidden = isNew;
  $("rec-icon").innerHTML = svg(RECIPE_ICON[spec.type] || "sql");
  $("rec-name").textContent = spec.name;
  $("rec-io").innerHTML =
    (spec.inputs || []).map((i) => `<code>${esc(inputLabel(i))}</code>`).join(" + ") +
    ` → <code>${esc(spec.name)}</code>`;
  markSaved();
  // Une recipe qui naît incrémentale a des questions en attente : on ouvre là
  // où elles se posent, pas sur le script.
  selectRecipeTab(isNew && (spec.output || {}).materialized === "incremental"
    ? "output" : "script");
  renderScript();
  renderOutputPane();
  if (VISUAL_TYPES.includes(spec.type)) loadInputColumns();
  refreshPreview();
}


const HIST_MAX = 80;


function pushHistory() {
  const rec = state.rec;
  if (!rec || !rec.hist) return;
  const snap = JSON.stringify(rec.spec);
  if (rec.hist[rec.histIdx] === snap) return;  // rien n'a bougé
  // Modifier après un « annuler » écrase ce qu'on avait rétabli : c'est ce
  // que font tous les éditeurs, et ça évite un arbre d'historique.
  rec.hist.length = rec.histIdx + 1;
  rec.hist.push(snap);
  if (rec.hist.length > HIST_MAX) rec.hist.shift();
  rec.histIdx = rec.hist.length - 1;
}


function travelHistory(delta) {
  const rec = state.rec;
  if (!rec || !rec.hist) return false;
  const target = rec.histIdx + delta;
  if (target < 0 || target >= rec.hist.length) return false;
  rec.histIdx = target;
  rec.spec = JSON.parse(rec.hist[target]);
  // Revenir exactement sur l'état enregistré, c'est n'avoir plus rien à
  // enregistrer — le bandeau doit le dire. La comparaison porte sur le
  // contenu : c'est la seule chose que la troncature de l'historique ne
  // puisse pas fausser.
  rec.dirty = rec.hist[target] !== rec.savedSnap;
  rec.openStep = null;
  rec.previewStep = null;
  rec.diag = null;
  rec.diagStale = false;
  markSaved();
  renderScript();
  if ($("spane-output").classList.contains("on")) renderOutputPane();
  if ($("spane-tests").classList.contains("on")) renderRecipeTests();
  refreshPreview();
  return true;
}


function renderHistoryButtons() {
  const rec = state.rec;
  $("btn-undo").disabled = !rec || !rec.hist || rec.histIdx <= 0;
  $("btn-redo").disabled = !rec || !rec.hist || rec.histIdx >= rec.hist.length - 1;
}


/* La seule question « puis-je quitter cet écran ? ».

   Le bouton « retour » la posait ; le fil d'Ariane, l'accueil, l'ouverture
   d'un autre projet, le remplacement par une autre recipe et la fermeture de
   l'onglet ne la posaient pas. `reloadProject` met même `rec` à null sans
   rien demander. Une garde qui ne couvre pas tous les chemins de sortie
   n'est pas une garde : elle ne protège que celui qu'on a pensé à protéger.

   Rend une promesse de vrai si l'on peut partir. La question se pose dans une
   modale de l'atelier, qui ne bloque pas le fil : chaque sortie l'attend. */
async function leaveRecipe(what = "La recipe n'est pas enregistrée.") {
  if (!state.rec || !state.rec.dirty) return true;
  return askConfirm({
    title: "Quitter sans enregistrer ?",
    message: `${what} Les modifications du script seront perdues.`,
    ok: "Quitter sans enregistrer",
    cancel: "Rester",
    danger: true,
  });
}


function markSaved() {
  const tag = $("rec-saved");
  if (!state.rec) return;
  tag.textContent = state.rec.dirty ? "NON ENREGISTRÉ" : "ENREGISTRÉ";
  tag.className = "saved-tag" + (state.rec.dirty ? " dirty" : "");
  renderHistoryButtons();
}


function touch() {
  if (!state.rec) return;
  state.rec.dirty = true;
  pushHistory();
  markSaved();
}

/* ---- script ---- */


/* ---- script ---- */

function renderScript() {
  const rec = state.rec;
  const list = $("script-list");
  list.innerHTML = "";
  const steps = rec.spec.steps || [];
  const active = steps.filter((s) => s.enabled !== false).length;
  $("script-count").innerHTML = steps.length
    ? `<b>${active}</b> étape${active > 1 ? "s" : ""} active${active > 1 ? "s" : ""} sur ${steps.length}`
    : "aucune étape";

  if (rec.spec.type === "sql") {
    const box = el("div");
    box.innerHTML = `<div class="fld"><label>SQL du modèle (Jinja dbt autorisé)</label></div>`;
    const ed = sqlEditor(rec.spec.sql || "", (v) => { rec.spec.sql = v; touch(); });
    ed.style.height = "calc(100vh - 260px)";
    ed.textarea.addEventListener("change", refreshPreview);
    box.appendChild(ed);
    list.appendChild(box);
    $("btn-add-step").style.display = "none";
    return;
  }
  if (VISUAL_TYPES.includes(rec.spec.type)) {
    $("btn-add-step").style.display = "none";
    $("script-count").textContent = labelOf(rec.spec.type);
    if (!rec.inputCols && !rec.colError) {
      list.appendChild(el("div", "script-empty", "<b>Lecture des colonnes…</b>"));
      return;
    }
    if (rec.spec.type === "join") renderJoinEditor(list);
    else if (rec.spec.type === "group") renderGroupEditor(list);
    else renderStackEditor(list);
    return;
  }
  if (rec.spec.type !== "prepare") {
    list.appendChild(el("div", "script-empty",
      `<b>Recipe ${esc(labelOf(rec.spec.type))}</b>Ce type se configure par ses entrées et sorties. Utilisez « Voir le SQL » pour vérifier le résultat.`));
    $("btn-add-step").style.display = "none";
    return;
  }
  $("btn-add-step").style.display = "";

  if (!steps.length) {
    list.appendChild(el("div", "script-empty",
      "<b>Aucune étape</b>Cliquez sur l'en-tête d'une colonne de la grille, ou sur « Ajouter une étape »."));
    return;
  }

  const deltas = (rec.preview && rec.preview.deltas) || [];
  let activeIndex = -1;
  steps.forEach((step, i) => {
    if (step.enabled !== false) activeIndex++;
    const d = step.enabled !== false ? deltas[activeIndex] : null;
    list.appendChild(stepCard(step, i, d));
  });
}


function stepCard(step, index, delta) {
  const rec = state.rec;
  const open = rec.openStep === step.id;
  const previewing = rec.previewStep === index;
  const card = el("div", `step${step.enabled === false ? " off" : ""}${open ? " open on" : ""}`);

  const counts = delta
    ? `<span class="counts">
         ${delta.created.length ? `<span class="c" title="Colonnes créées">+${delta.created.length}</span>` : ""}
         ${delta.modified.length ? `<span class="m" title="Colonnes modifiées">~${delta.modified.length}</span>` : ""}
         ${delta.deleted.length ? `<span class="d" title="Colonnes supprimées">−${delta.deleted.length}</span>` : ""}
       </span>` : "";

  const steps = rec.spec.steps || [];
  const main = el("div", "step-main");
  main.innerHTML =
    `<span class="num">${index + 1}</span>
     <span class="desc">${esc(describe(step, delta))}</span>
     ${counts}
     <span class="acts">
       <button data-act="up" title="Monter l'étape"${index === 0 ? " disabled" : ""}>${svg("up")}</button>
       <button data-act="down" title="Descendre l'étape"${index === steps.length - 1 ? " disabled" : ""}>${svg("down")}</button>
       <button data-act="eye" class="${previewing ? "on" : ""}" title="Voir l'impact de cette étape">${svg("eye")}</button>
       <button data-act="power" title="${step.enabled === false ? "Activer" : "Désactiver"} l'étape">${svg("power")}</button>
       <button data-act="dup" title="Dupliquer l'étape">${svg("copy")}</button>
       <button data-act="del" title="Supprimer l'étape">${svg("trash")}</button>
     </span>`;
  card.appendChild(main);

  main.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-act]")) return;
    rec.openStep = open ? null : step.id;
    renderScript();
  });
  main.querySelector('[data-act="eye"]').addEventListener("click", (ev) => {
    ev.stopPropagation();
    rec.previewStep = previewing ? null : index;
    renderScript();
    refreshPreview();
  });
  main.querySelector('[data-act="power"]').addEventListener("click", (ev) => {
    ev.stopPropagation();
    step.enabled = step.enabled === false;
    touch(); renderScript(); refreshPreview();
  });

  /* L'œil suit un *rang*, pas une étape : déplacer, dupliquer ou supprimer
     au-dessus de lui le faisait montrer le résultat de l'étape d'à côté, sans
     que le libellé change. On le déplace avec. */
  const move = (delta) => {
    const target = index + delta;
    if (target < 0 || target >= steps.length) return;
    [steps[index], steps[target]] = [steps[target], steps[index]];
    if (rec.previewStep === index) rec.previewStep = target;
    else if (rec.previewStep === target) rec.previewStep = index;
    touch(); renderScript(); refreshPreview();
  };
  main.querySelector('[data-act="up"]').addEventListener("click", (ev) => {
    ev.stopPropagation(); move(-1);
  });
  main.querySelector('[data-act="down"]').addEventListener("click", (ev) => {
    ev.stopPropagation(); move(1);
  });

  main.querySelector('[data-act="dup"]').addEventListener("click", (ev) => {
    ev.stopPropagation();
    const copy = JSON.parse(JSON.stringify(step));
    copy.id = uid();
    steps.splice(index + 1, 0, copy);
    rec.openStep = copy.id;
    if (rec.previewStep != null && rec.previewStep > index) rec.previewStep += 1;
    touch(); renderScript(); refreshPreview();
  });

  main.querySelector('[data-act="del"]').addEventListener("click", (ev) => {
    ev.stopPropagation();
    removeStepAt(index);
  });

  if (open) card.appendChild(paramForm(step, index));
  return card;
}

/* L'œil suit un rang : retirer une étape au-dessus de lui le décale, et le
   retirer *lui* le rend à la sortie du script. Rétablir une colonne peut faire
   disparaître l'étape qui la supprimait : les deux chemins passent par ici. */
function removeStepAt(index) {
  const rec = state.rec;
  const steps = rec.spec.steps || [];
  if (index < 0 || index >= steps.length) return;
  steps.splice(index, 1);
  if (rec.previewStep === index) rec.previewStep = null;
  else if (rec.previewStep != null && rec.previewStep > index) rec.previewStep -= 1;
  touch(); renderScript(); refreshPreview();
}


/* La phrase d'une étape.

   Le serveur l'écrit déjà — c'est elle qui finit en commentaire au-dessus du
   CTE, dans le fichier que le produit présente comme la source de vérité — et
   il la rend maintenant dans le `label` de chaque delta. On lit donc la
   sienne : les deux versions de cette phrase divergeaient sur quatre types
   d'étape, dont le tri, où le fichier perdait le sens du classement.

   Le repli ci-dessous ne sert qu'avant la première réponse du serveur, et
   quand l'aperçu est en erreur : il n'y a alors pas de delta. */
function describe(step, delta) {
  if (delta && delta.label) return delta.label;
  const p = step.params || {};
  const t = step.type;
  const proc = state.processors.find((x) => x.key === t);
  const F = {
    rename: () => (p.renames || []).length === 1
      ? `Renommer ${p.renames[0].from} en ${p.renames[0].to}`
      : `Renommer ${(p.renames || []).length} colonnes`,
    keep_delete: () => {
      const n = (p.columns || []).length;
      return `${p.action === "keep" ? "Conserver" : "Supprimer"} ${n} colonne${n > 1 ? "s" : ""}`;
    },
    formula: () => `Calculer ${p.into} = ${p.expression}`,
    if_then_else: () => `Calculer ${p.into} par condition`,
    filter_value: () => {
      const a = { remove: "Retirer", keep: "Garder", clear: "Vider" }[p.action || "remove"];
      const op = (OPS.find((o) => o[0] === p.operator) || ["", ""])[1];
      return `${a} les lignes où ${p.column} ${op} ${(p.values || []).join(", ")}`.trim();
    },
    filter_formula: () => `${p.action === "remove" ? "Retirer" : "Garder"} les lignes où ${p.condition}`,
    remove_empty: () => `Supprimer les lignes où ${p.column} est vide`,
    fill_empty: () => `Remplir ${p.column} vide par « ${p.value} »`,
    find_replace: () => `Remplacer « ${p.find} » par « ${p.replace} » dans ${p.column}`,
    text_transform: () => `Mettre ${p.column} en ${(FIELDS.text_transform[1].opts.find((o) => o[0] === p.mode) || ["", p.mode])[1]}`,
    change_type: () => `Convertir ${p.column} en ${p.to}`,
    round: () => `Arrondir ${p.column} à ${p.decimals || 0} décimale(s)`,
    parse_date: () => `Parser ${p.column} en date`,
    extract_date_parts: () => `Extraire ${(p.parts || []).join(", ")} de ${p.column}`,
    concat_columns: () => `Concaténer ${(p.columns || []).length} colonnes dans ${p.into}`,
    split_column: () => `Découper ${p.column} sur « ${p.separator} »`,
    distinct_rows: () => "Dédoublonner les lignes",
    sort: () => `Trier par ${p.column}${p.descending ? " (décroissant)" : ""}`,
    dedup_key: () => {
      const sortSpec = (p.order_by || [])[0] || {};
      return `Une ligne par ${(p.keys || []).join(", ") || "?"} — `
           + `${sortSpec.descending ? "plus grand" : "plus petit"} ${sortSpec.column || "?"}`;
    },
    window_function: () => {
      // Le libellé long sert à choisir dans la liste ; sur la carte, la
      // parenthèse qui explique les ex æquo prend trois lignes pour rien.
      const what = String((WINDOW_FNS.find((w) => w[0] === p.fn) || ["", p.fn])[1])
        .replace(/\s*\(.*\)\s*$/, "");
      const partitionBy = (p.partition_by || []).join(", ");
      return `Calculer ${p.into} : ${what}` + (partitionBy ? `, par ${partitionBy}` : "");
    },
    unpivot: () => `Dépivoter ${(p.columns || []).length} colonnes en `
                 + `${p.name_into || "variable"} / ${p.value_into || "valeur"}`,
    pivot: () => {
      const n = (p.values || []).length;
      return `Pivoter ${p.name_column} en ${n} colonne${n > 1 ? "s" : ""}`;
    },
  };
  try { return F[t] ? F[t]() : (proc ? proc.label : t); }
  catch (_) { return proc ? proc.label : t; }
}

/* ---- formulaire de paramètres ---- */


/* ---- formulaire de paramètres ---- */

function columnsBefore(index) {
  const rec = state.rec;
  const pv = rec.preview;
  if (!pv) return [];
  const steps = rec.spec.steps || [];
  const activeBefore = steps.slice(0, index).filter((s) => s.enabled !== false).length;
  const deltas = pv.deltas || [];
  const d = deltas[activeBefore];
  if (d && d.before) return d.before;
  // Pas de delta pour cette étape : soit elle est désactivée et en fin de
  // script — les colonnes qui y entrent sont alors celles qui en sortent —,
  // soit le suivi des colonnes s'est arrêté avant elle sur une étape que le
  // serveur n'a pas su compiler. Le dernier état connu reste la meilleure
  // réponse : il vaut mieux que les colonnes de l'aperçu, qui s'arrêtent à
  // l'œil et ignorent tout ce que les étapes suivantes ont déjà fait.
  const last = deltas[deltas.length - 1];
  if (last && last.after) return last.after;
  return (pv.columns || []).map((c) => c.name);
}


/* Une liste de valeurs, une par champ.

   Le contenu exact est conservé : virgule, espaces de tête et de queue,
   chaîne vide. C'est le point du contrôle précédent — un seul champ découpé
   sur la virgule — qui ne pouvait ni les représenter ni les relire. */
function paramForm(step, index) {
  const wrap = el("div", "step-form");
  const fields = FIELDS[step.type] || [];
  const cols = columnsBefore(index);
  step.params = step.params || {};

  if (!fields.length) {
    wrap.appendChild(el("div", "empty", "Cette étape n'a pas de paramètre."));
    return wrap;
  }

  fields.forEach((f) => {
    // Un champ qui ne veut rien dire pour la fonction choisie n'est pas
    // affiché : une moyenne glissante n'a pas de décalage, un classement n'a
    // pas de colonne mesurée.
    if (f.when && !f.when(step.params)) return;
    const fld = el("div", "fld");
    fld.appendChild(el("label", null, esc(f.label || f.k)));
    const v = step.params[f.k];

    if (f.t === "select" || f.t === "col") {
      const opts = f.t === "col" ? cols.map((c) => [c, c]) : f.opts;
      const sel = el("select", "inp");
      sel.innerHTML = opts.map(([val, lab]) =>
        `<option value="${esc(val)}"${String(v) === String(val) ? " selected" : ""}>${esc(lab)}</option>`).join("");
      if (v == null && opts.length) step.params[f.k] = opts[0][0];
      sel.addEventListener("change", () => {
        step.params[f.k] = sel.value;
        edited();
        // Changer d'opérateur change les champs à afficher : « est vide » n'en
        // a aucun, « vaut » en a un, « est dans » en a une liste.
        if (step.type === "filter_value" && f.k === "operator") renderScript();
      });
      fld.appendChild(sel);
    } else if (f.t === "number") {
      const inp = el("input", "inp");
      inp.type = "number";
      inp.value = v == null ? 0 : v;
      inp.addEventListener("change", () => { step.params[f.k] = parseInt(inp.value, 10) || 0; edited(); });
      fld.appendChild(inp);
    } else if (f.t === "bool") {
      const lab = el("label", "chips");
      lab.innerHTML = `<label><input type="checkbox"${v ? " checked" : ""}> ${esc(f.label)}</label>`;
      lab.querySelector("input").addEventListener("change", (e) => {
        step.params[f.k] = e.target.checked; edited();
      });
      fld.innerHTML = "";
      fld.appendChild(lab);
    } else if (f.t === "values") {
      // Une valeur par champ, et non une chaîne à découper sur la virgule :
      // `split(",")` suivi de `trim()` rendait impossible de saisir une valeur
      // qui contient une virgule, une chaîne vide ou des espaces
      // significatifs — et rééditer une configuration existante qui en portait
      // une l'abîmait en silence.
      const op = step.type === "filter_value" ? (step.params.operator || "eq") : null;
      const withoutValue = op && (state.caps.no_value_operators || []).includes(op);
      const singleValue = op && (state.caps.single_value_operators || []).includes(op);
      if (withoutValue) return;  // « est vide » ne compare à rien
      if (singleValue) {
        // Un seul champ : le compilateur refuse désormais une seconde valeur
        // plutôt que de la jeter, et l'écran doit dire la même chose que lui.
        fld.querySelector("label").textContent = "Valeur";
        const inp = el("input", "inp");
        inp.value = (v || [])[0] != null ? String((v || [])[0]) : "";
        inp.addEventListener("change", () => {
          step.params[f.k] = inp.value === "" ? [] : [inp.value];
          edited();
        });
        fld.appendChild(inp);
        fld.appendChild(el("p", "hint",
          "Pour comparer à plusieurs valeurs, prenez l'opérateur « est dans »."));
      } else {
        fld.appendChild(valuesEditor(step.params, f.k, { onChange: edited }));
      }
    } else if (f.t === "cols") {
      const chips = el("div", "chips");
      const chosen = new Set(v || []);
      cols.forEach((c) => {
        const lab = el("label");
        lab.innerHTML = `<input type="checkbox"${chosen.has(c) ? " checked" : ""}> ${esc(c)}`;
        lab.querySelector("input").addEventListener("change", (e) => {
          if (e.target.checked) chosen.add(c); else chosen.delete(c);
          step.params[f.k] = [...chosen];
          edited();
        });
        chips.appendChild(lab);
      });
      fld.appendChild(chips);
    } else if (f.t === "chips") {
      const chips = el("div", "chips");
      const chosen = new Set(v || []);
      f.opts.forEach(([val, lab_]) => {
        const lab = el("label");
        lab.innerHTML = `<input type="checkbox"${chosen.has(val) ? " checked" : ""}> ${esc(lab_)}`;
        lab.querySelector("input").addEventListener("change", (e) => {
          if (e.target.checked) chosen.add(val); else chosen.delete(val);
          step.params[f.k] = [...chosen];
          edited();
        });
        chips.appendChild(lab);
      });
      fld.appendChild(chips);
    } else if (f.t === "order") {
      const box = el("div");
      const rows = v && v.length ? v : (step.params[f.k] = []);
      const draw = () => {
        box.innerHTML = "";
        rows.forEach((r, i) => {
          const line = el("div", "rowline");
          line.innerHTML =
            `<select class="inp" data-of="${i}">${cols.map((c) =>
              `<option value="${esc(c)}"${c === r.column ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>
             <select class="inp" data-od="${i}">
               <option value=""${r.descending ? "" : " selected"}>du plus petit au plus grand</option>
               <option value="1"${r.descending ? " selected" : ""}>du plus grand au plus petit</option>
             </select>
             <button class="xbtn" data-ox="${i}" type="button">×</button>`;
          box.appendChild(line);
        });
        if (!rows.length)
          box.appendChild(el("p", "hint", "Aucun tri : ajoutez-en un."));
        const add = el("button", "btn ghost sm", "+ Colonne de tri");
        add.type = "button";
        add.addEventListener("click", () => {
          rows.push({ column: cols[0] || "", descending: true });
          draw(); edited();
        });
        box.appendChild(add);
        box.querySelectorAll("[data-of]").forEach((s) =>
          s.addEventListener("change", () => { rows[+s.dataset.of].column = s.value; edited(); }));
        box.querySelectorAll("[data-od]").forEach((s) =>
          s.addEventListener("change", () => { rows[+s.dataset.od].descending = !!s.value; edited(); }));
        box.querySelectorAll("[data-ox]").forEach((b) =>
          b.addEventListener("click", () => { rows.splice(+b.dataset.ox, 1); draw(); edited(); }));
      };
      draw();
      // Les valeurs vides sont toujours reléguées en dernier : une date
      // manquante ne doit pas passer pour la plus récente.
      fld.appendChild(box);
    } else if (f.t === "renames") {
      const box = el("div");
      const rows = v && v.length ? v : (step.params[f.k] = [{ from: cols[0] || "", to: "" }]);
      const draw = () => {
        box.innerHTML = "";
        rows.forEach((r, i) => {
          const line = el("div", "rowline");
          line.innerHTML =
            `<select class="inp" data-rf="${i}">${cols.map((c) =>
              `<option value="${esc(c)}"${c === r.from ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>
             <input class="inp" data-rt="${i}" placeholder="nouveau nom" value="${esc(r.to || "")}">
             <button class="xbtn" data-rx="${i}" type="button">×</button>`;
          box.appendChild(line);
        });
        const add = el("button", "btn ghost sm", "+ Renommage");
        add.type = "button";
        add.addEventListener("click", () => { rows.push({ from: cols[0] || "", to: "" }); draw(); });
        box.appendChild(add);
        box.querySelectorAll("[data-rf]").forEach((s) =>
          s.addEventListener("change", () => { rows[+s.dataset.rf].from = s.value; edited(); }));
        box.querySelectorAll("[data-rt]").forEach((s) =>
          s.addEventListener("change", () => { rows[+s.dataset.rt].to = s.value.trim(); edited(); }));
        box.querySelectorAll("[data-rx]").forEach((b) =>
          b.addEventListener("click", () => { rows.splice(+b.dataset.rx, 1); draw(); edited(); }));
      };
      draw();
      fld.appendChild(box);
    } else {
      const inp = el("input", "inp");
      inp.value = v == null ? "" : v;
      inp.spellcheck = false;
      inp.addEventListener("change", () => { step.params[f.k] = inp.value; edited(); });
      fld.appendChild(inp);
    }
    wrap.appendChild(fld);
  });
  return wrap;
}


function edited() {
  // Un diagnostic porte sur la jointure telle qu'elle était : le laisser
  // affiché tel quel ferait passer des chiffres périmés pour à jour.
  if (state.rec && state.rec.diag) state.rec.diagStale = true;
  touch(); renderScript(); refreshPreview();
}

/* ---- aperçu ---- */

/* Deux choses qu'un aperçu « qui se recalcule tout seul » cache trop bien.

   Son coût, d'abord : le `limit` ne borne que ce qui redescend, la requête,
   elle, s'exécute en entier dans l'entrepôt. Sur un entrepôt facturé à la
   requête, taper dans un champ se paie — d'où le mode manuel et la durée
   affichée à côté de la grille.

   L'ordre des réponses, ensuite : deux requêtes parties à 220 ms d'écart ne
   reviennent pas forcément dans cet ordre. Sans numéro de série, la plus lente
   écrase la plus récente et la grille montre un état du script qui n'est plus
   celui qu'on lit à gauche, sans que rien ne le signale. */


let previewTimer = null;
let previewSeq = 0;


const AUTO_KEY = "pliq.preview.auto";
const previewAuto = () => $("rec-auto").value !== "manual";


function refreshPreview() {
  clearTimeout(previewTimer);
  if (!state.rec) return;
  state.rec.stale = true;
  if (previewAuto()) previewTimer = setTimeout(doPreview, 220);
  else renderGridBar();
}


function renderGridBar() {
  const rec = state.rec;
  if (!rec) return;
  const pv = rec.preview;
  const pending = !!rec.stale && !rec.previewBusy;

  const btn = $("rec-refresh");
  btn.hidden = !pending;
  btn.classList.toggle("primary", pending);
  $("rec-cost").textContent =
    rec.previewMs == null ? "" : `${(rec.previewMs / 1000).toFixed(1)} s`;
  $("rec-check").hidden = !pv;

  if (rec.previewBusy) {
    $("rec-grid-note").textContent = "aperçu en cours…";
    return;
  }
  if (!pv) {
    $("rec-grid-note").textContent = pending ? "aperçu en attente" : "—";
    if (pending && !$("rec-grid").querySelector(".err-box"))
      $("rec-grid").innerHTML =
        `<div class="empty">Aperçu manuel : rien n'est envoyé à l'entrepôt tant
         que vous ne cliquez pas <b>Recalculer</b>.</div>`;
    return;
  }
  const ghosts = (rec.ghosts && rec.ghosts.size) || 0;
  $("rec-grid-note").innerHTML =
    (rec.previewStep != null
      ? `aperçu après l'étape <b style="color:var(--ink)">${rec.previewStep + 1}</b>`
      : "sortie du script") +
    ` — ${pv.rows.length} lignes · ${pv.columns.length} colonnes` +
    (ghosts
      ? ` · <span class="gone-note">${ghosts} supprimée${ghosts > 1 ? "s" : ""}</span>`
      : "") +
    (pending ? ` <span class="stale">· le script a changé depuis</span>` : "");
  $("rec-sample").textContent = `Échantillon · ${pv.rows.length} lignes`;
  $("rec-sample").classList.toggle("stale", pending);
}

/* ---- colonnes supprimées -------------------------------------------------

   Une colonne retirée par une étape disparaissait purement et simplement de
   la grille : on perdait de vue ce qu'on venait d'enlever, et rien ne disait
   où elle se trouvait. Elle garde donc sa place, barrée et hachurée, et son
   menu propose de la rétablir.

   Seule la suppression explicite fait un fantôme. Renommer retire aussi
   l'ancien nom, mais la colonne est toujours là sous un autre ; déplier et
   pivoter refont la table entière, et les fantômes d'avant n'y ont plus de
   place — ils s'effacent avec elle. */


const RESHAPERS = new Set(["unpivot", "pivot"]);


function ghostLayout(rec, pv) {
  const byId = new Map((rec.spec.steps || []).map((s) => [s.id, s]));
  // Les fantômes décrivent la grille, donc ce que l'aperçu a vraiment exécuté :
  // une colonne que l'étape 7 supprime est bien vivante dans une grille arrêtée
  // à l'étape 2, et l'y griser serait un mensonge.
  const deltas = (pv.deltas || []).filter((d) => d.applied !== false);
  const ghosts = new Map();
  let order = deltas.length
    ? (deltas[0].before || []).slice()
    : pv.columns.map((c) => c.name);

  deltas.forEach((d) => {
    const step = byId.get(d.step);
    const after = d.after || [];
    if (step && RESHAPERS.has(step.type)) {
      ghosts.clear();
      order = after.slice();
      return;
    }
    if (step && step.type === "keep_delete")
      (d.deleted || []).forEach((c) => ghosts.set(c, step));
    // Une colonne recréée sous le nom d'une supprimée redevient une vraie
    // colonne : deux en-têtes du même nom se contrediraient.
    (d.created || []).forEach((c) => ghosts.delete(c));

    order = placeGhosts(order, after, ghosts);
  });
  return { order, ghosts };
}

/* L'ordre des colonnes vivantes est celui que l'étape vient de produire : lui
   seul dit où une colonne renommée ou créée se range. Les fantômes s'y
   glissent ensuite, chacun derrière la colonne vivante qui le précédait —
   ainsi une colonne supprimée garde la place qu'elle occupait. */
function placeGhosts(previous, alive, ghosts) {
  const out = alive.slice();
  previous.forEach((name, i) => {
    if (!ghosts.has(name) || out.includes(name)) return;
    let anchor = -1;
    for (let j = i - 1; j >= 0; j--) {
      const k = out.indexOf(previous[j]);
      if (k >= 0) { anchor = k; break; }
    }
    out.splice(anchor + 1, 0, name);
  });
  return out;
}


function withGhosts(rec, pv) {
  const { order, ghosts } = ghostLayout(rec, pv);
  rec.ghosts = ghosts;
  if (!ghosts.size) return { columns: pv.columns, rows: pv.rows };

  const rank = new Map(pv.columns.map((c, i) => [c.name, i]));
  const columns = [], taken = [];
  order.forEach((name) => {
    if (rank.has(name)) { columns.push(pv.columns[rank.get(name)]); taken.push(rank.get(name)); }
    else if (ghosts.has(name)) { columns.push({ name, gone: true }); taken.push(-1); }
  });
  // Filet : une colonne bien vivante que l'ordre reconstitué aurait laissée de
  // côté revient en fin de grille, plutôt que de manquer sans que rien ne le
  // dise. L'aperçu doit montrer toutes les colonnes qu'il a ramenées.
  pv.columns.forEach((c, i) => {
    if (!order.includes(c.name)) { columns.push(c); taken.push(i); }
  });
  return {
    columns,
    rows: pv.rows.map((r) => taken.map((i) => (i < 0 ? null : r[i]))),
  };
}


function restoreColumn(name) {
  const rec = state.rec;
  const step = rec && rec.ghosts && rec.ghosts.get(name);
  if (!step) return;
  const index = (rec.spec.steps || []).indexOf(step);
  if (index < 0) return;
  const p = step.params || (step.params = {});
  const cols = p.columns || [];
  if (p.action === "keep") {
    p.columns = cols.concat([name]);
  } else {
    p.columns = cols.filter((c) => c !== name);
    // Une étape « Supprimer » qui ne supprime plus rien ne ferait que traverser
    // le script : elle part avec la dernière colonne qu'elle retenait.
    if (!p.columns.length) return removeStepAt(index);
  }
  touch(); renderScript(); refreshPreview();
}


async function doPreview() {
  const rec = state.rec;
  if (!rec) return;
  const grid = $("rec-grid");
  const limit = parseInt($("rec-limit").value, 10);

  let upto = null;
  if (rec.previewStep != null) {
    const steps = rec.spec.steps || [];
    upto = steps.slice(0, rec.previewStep + 1).filter((s) => s.enabled !== false).length;
  }

  const seq = ++previewSeq;
  // L'empreinte de ce qui part, en plus du numéro de série. Le numéro
  // n'avance qu'au *lancement* d'une requête : en mode manuel, modifier le
  // script pendant un aperçu n'en lance aucune, la réponse en vol portait
  // donc encore le bon numéro, et elle remettait `stale` à faux sur un script
  // qu'elle ne décrit plus. En automatique, la même fenêtre existe pendant le
  // debounce. Seule la valeur du script peut trancher.
  const sent = JSON.stringify({ spec: rec.spec, upto });
  const t0 = performance.now();
  rec.previewBusy = true;
  renderGridBar();

  let pv;
  try {
    pv = await post("/api/recipe/preview", { spec: rec.spec, upto, limit });
  } catch (e) {
    // Le drapeau se relâche dans tous les cas : une recipe qu'on quitte
    // pendant sa requête resterait sinon marquée « en cours » pour toujours.
    rec.previewBusy = false;
    if (seq !== previewSeq || state.rec !== rec) return;
    rec.previewMs = performance.now() - t0;
    rec.ghosts = null;
    grid.innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
    renderGridBar();
    $("rec-grid-note").textContent = "aperçu impossible";
    return;
  }
  rec.previewBusy = false;
  // Seule la dernière demande a le droit de s'afficher : une réponse partie
  // avant elle et revenue après montrerait un script qu'on n'écrit plus.
  if (seq !== previewSeq || state.rec !== rec) return;

  // À jour seulement si le script n'a pas bougé depuis l'envoi.
  rec.stale = JSON.stringify({ spec: rec.spec, upto }) !== sent;
  rec.previewMs = performance.now() - t0;
  rec.preview = pv;
  // Le contrôle complet portait sur la version précédente du script : le
  // garder à l'écran ferait passer des chiffres périmés pour à jour.
  rec.fullCheck = null;

  const tinted = new Set();
  // La teinte montre l'apport de l'étape sous l'œil : le dernier delta exécuté,
  // pas le dernier du script — ils ne coïncident plus quand l'aperçu s'arrête.
  const applied = (pv.deltas || []).filter((d) => d.applied !== false);
  if (rec.previewStep != null && applied.length) {
    const d = applied[applied.length - 1];
    (d.created || []).forEach((c) => tinted.add(c));
    (d.modified || []).forEach((c) => tinted.add(c));
  }
  const view = withGhosts(rec, pv);
  grid.innerHTML = "";
  grid.appendChild(buildGrid(view.columns, view.rows, { tinted, onColumnClick: openColumnMenu }));
  renderGridBar();
  renderScript();
  if ($("spane-output").classList.contains("on")) renderOutputPane();
  if ($("spane-tests").classList.contains("on")) renderRecipeTests();
  else $("rtests-count").textContent = countTests(recipeTestsModel()) || "";
}


/* ---- menu de colonne ---- */

function openColumnMenu(colName, ev) {
  const rec = state.rec;
  if (!rec || !rec.preview) return;
  const menu = $("cmenu");
  const ghost = rec.ghosts && rec.ghosts.get(colName);
  const sugg = ghost ? [] : (rec.preview.suggestions || {})[colName] || [];

  let html = `<div class="grp">${esc(colName)}</div>`;
  if (ghost) {
    const rank = (rec.spec.steps || []).indexOf(ghost) + 1;
    html += `<div class="note">Supprimée à l'étape ${rank}.</div>
             <button data-restore="1">Rétablir la colonne</button>`;
  }
  let lastCat = null;
  sugg.forEach((s, i) => {
    if (s.category !== lastCat) {
      html += `<div class="grp">${esc(s.category)}</div>`;
      lastCat = s.category;
    }
    html += `<button data-sugg="${i}">${esc(s.label)}</button>`;
  });
  menu.innerHTML = html;
  const render = menu.querySelector("[data-restore]");
  if (render)
    render.addEventListener("click", () => {
      restoreColumn(colName);
      menu.classList.remove("on");
    });
  menu.querySelectorAll("[data-sugg]").forEach((b) =>
    b.addEventListener("click", () => {
      const s = sugg[+b.dataset.sugg];
      addStep(s.processor, JSON.parse(JSON.stringify(s.params)));
      menu.classList.remove("on");
    }));

  const r = ev.currentTarget.getBoundingClientRect();
  menu.style.left = Math.min(r.left, window.innerWidth - 280) + "px";
  menu.style.top = (r.bottom + 2) + "px";
  menu.classList.add("on");
}


function addStep(type, params) {
  const rec = state.rec;
  const step = { id: uid(), type, enabled: true, params: params || {} };
  rec.spec.steps = rec.spec.steps || [];
  rec.spec.steps.push(step);
  rec.openStep = step.id;
  rec.previewStep = null;
  touch();
  renderScript();
  refreshPreview();
  $("script-list").scrollTop = $("script-list").scrollHeight;
}

/* ---- bibliothèque de processeurs ---- */


function renderLibrary() {
  const q = state.lib.search;
  const all = state.processors;
  const matching = all.filter((p) =>
    !q || p.label.toLowerCase().includes(q) || p.summary.toLowerCase().includes(q));
  const shown = state.lib.cat
    ? matching.filter((p) => p.category === state.lib.cat)
    : matching;

  $("lib-count").innerHTML = `<b>${matching.length}</b> processeurs`;

  const shorts = $("lib-shorts");
  shorts.innerHTML = "";
  all.filter((p) => p.shortcut).forEach((p) => {
    const b = el("button", null, esc(p.label));
    b.type = "button";
    b.addEventListener("click", () => { addStep(p.key, {}); $("lib").classList.remove("on"); });
    shorts.appendChild(b);
  });

  const cats = $("lib-cats");
  cats.innerHTML = "";
  const allBtn = el("button", state.lib.cat ? "" : "on",
    `Toutes <span class="n">${matching.length}</span>`);
  allBtn.addEventListener("click", () => { state.lib.cat = null; renderLibrary(); });
  cats.appendChild(allBtn);
  state.categories.forEach((c) => {
    const n = matching.filter((p) => p.category === c).length;
    if (!n && q) return;
    const b = el("button", state.lib.cat === c ? "on" : "", `${esc(c)} <span class="n">${n}</span>`);
    b.addEventListener("click", () => { state.lib.cat = c; renderLibrary(); });
    cats.appendChild(b);
  });

  const list = $("lib-list");
  list.innerHTML = "";
  if (!shown.length) list.appendChild(el("div", "empty", "Aucun processeur ne correspond."));
  shown.forEach((p) => {
    const b = el("button", state.lib.pick === p.key ? "on" : "",
      `${esc(p.label)}<span class="c">${esc(p.category)}</span>`);
    b.addEventListener("mouseenter", () => { state.lib.pick = p.key; renderHelp(p); });
    b.addEventListener("click", () => { addStep(p.key, {}); $("lib").classList.remove("on"); });
    list.appendChild(b);
  });
  if (shown.length) renderHelp(shown.find((p) => p.key === state.lib.pick) || shown[0]);
}


function renderHelp(p) {
  if (!p) return;
  $("lib-help").innerHTML =
    `<h3>${esc(p.label)}</h3><p>${esc(p.summary)}</p>` +
    (p.help ? `<p>${esc(p.help)}</p>` : "");
}

/* ---- enregistrement ---- */


/* ---- enregistrement ---- */

function incrementalProblem(out) {
  if ((out.materialized || "view") !== "incremental") return "";
  const strat = out.incremental_strategy || "delete+insert";

  if (strat === "microbatch") {
    const mb = out.microbatch || {};
    if (!mb.event_time)
      return "La stratégie « microbatch » a besoin d'une colonne de temps : c'est "
           + "elle qui découpe le travail en tranches. dbt refuse de parser le "
           + "projet sans elle.";
    if (!mb.begin)
      return "La stratégie « microbatch » a besoin d'une date de départ : dbt ne "
           + "construit aucune tranche antérieure.";
    if (!(state.caps.batch_sizes || []).includes(mb.batch_size || ""))
      return "Choisissez une taille de tranche : "
           + (state.caps.batch_sizes || []).join(", ") + ".";
    return "";
  }

  if (state.caps.strategies_needing_key.includes(strat) && !(out.unique_key || []).length)
    return `La stratégie « ${strat} » remplace des lignes existantes : `
         + `choisissez au moins une clé unique, ou passez en « append ».`;

  const n = parseInt(((out.incremental || {}).lookback || {}).n, 10) || 0;
  if (n > 0 && strat === "append")
    return "Une fenêtre de reprise ne va pas avec « append » : les lignes relues "
         + "seraient réinsérées en doublon au lieu de remplacer les anciennes. "
         + "Passez en « delete+insert » ou « merge », ou retirez la fenêtre.";
  return "";
}


async function saveRecipe(run) {
  const rec = state.rec;
  if (!rec) return;
  const gap = incrementalProblem(rec.spec.output || {});
  if (gap) {
    selectRecipeTab("output");
    return toast("Sortie incomplète", gap, "error");
  }
  const problem = testsProblem(recipeTestsModel());
  if (problem) return toast("Test incomplet", problem, "error");

  /* L'atelier connaît le DAG et les scripts visuels : il peut dire avant
     d'écrire qu'un renommage va casser trois recipes en aval. Sans ça, on
     l'apprend au `dbt build` suivant, une fois le fichier déjà remplacé.

     Une recipe neuve n'a rien à casser, et l'impact reste un service, pas un
     verrou : s'il échoue, on enregistre quand même. */
  if (!rec.isNew) {
    let imp = null;
    try { imp = await post("/api/recipe/impact", { spec: rec.spec }); }
    catch (_) { /* pas de diagnostic : ce n'est pas une raison de bloquer */ }
    if (state.rec !== rec) return;
    if (imp && (imp.impacts || []).length) return openImpactModal(imp, run);
  }
  return doSave(run);
}


let impactTarget = null;


function openImpactModal(imp, run) {
  impactTarget = { run, rec: state.rec };
  const n = imp.impacts.length;
  const renamed = imp.impacts.filter((i) => i.renamed_to).length;
  // Dire ce qui arrive vraiment : une colonne renommée ne disparaît pas, elle
  // change de nom — et c'est précisément ce qui se répare.
  const what = n === 1 ? "<b>Une colonne</b>" : `<b>${n} colonnes</b>`;
  const sort = renamed === n
    ? (n === 1 ? "change de nom dans" : "changent de nom dans")
    : (n === 1 ? "va disparaître de" : "vont disparaître de");
  $("imp-warn").innerHTML =
    `${what} ${sort} <code>${esc(state.rec.spec.name)}</code>, et ` +
    (n === 1 ? "des modèles la nomment" : "des modèles les nomment") +
    " encore sous l'ancien nom. Leur prochain <code>dbt build</code> échouera.";

  const list = $("imp-list");
  list.innerHTML = "";
  imp.impacts.forEach((i) => {
    const box = el("div", "impact");
    const kv = el("dl", "kv");
    kv.innerHTML =
      `<dt><code>${esc(i.column)}</code></dt>
       <dd>${i.used_by.map((u) =>
         `<b>${esc(u.model)}</b> <span class="cm">${u.where === "script" ? "script visuel" : "SQL écrit à la main"}</span>`
       ).join(" · ")}</dd>`;
    box.appendChild(kv);
    // Dire vers quoi la colonne est partie : « renommée en total » se répare
    // autrement que « supprimée ». C'est à l'utilisateur d'ouvrir ces modèles,
    // l'atelier ne réécrit plus l'aval à sa place.
    if (i.renamed_to) {
      const note = el("p", "cm");
      note.innerHTML =
        `Renommée en <code>${esc(i.renamed_to)}</code> — à reprendre chez eux.`;
      box.appendChild(note);
    }
    list.appendChild(box);
  });
  $("imp-err").textContent = "";
  $("imp-scrim").classList.add("on");
  $("imp-cancel").focus();
}


const closeImpact = () => {
  $("imp-scrim").classList.remove("on");
  impactTarget = null;
};
let sqlDivTarget = null;


const closeSqlDiv = () => {
  $("sqldiv-scrim").classList.remove("on");
  sqlDivTarget = null;
};

function openSqlDivergeModal(message, run) {
  sqlDivTarget = { run, rec: state.rec };
  $("sqldiv-warn").textContent = message;
  $("sqldiv-scrim").classList.add("on");
  $("sqldiv-cancel").focus();
}


async function doSave(run, overwriteSql = false) {
  const rec = state.rec;
  if (!rec) return;
  // La révision qui part, retenue avant l'aller-retour : l'utilisateur peut
  // continuer à travailler pendant l'enregistrement, et ce qu'il change alors
  // n'est pas dans le fichier. Déclarer « ENREGISTRÉ » l'état d'arrivée
  // effaçait ces modifications-là sans même prévenir en quittant l'écran.
  const sent = JSON.stringify(rec.spec);
  try {
    const r = await post("/api/recipe/save", {
      spec: rec.spec, run: !!run, is_new: !!rec.isNew, base: rec.base,
      model_uid: rec.modelUid, overwrite_sql: !!overwriteSql,
    }, { project: rec.project });
    if (state.rec !== rec) return;
    rec.isNew = false;
    rec.savedSnap = sent;
    rec.dirty = JSON.stringify(rec.spec) !== sent;
    // La version qu'on vient d'écrire devient celle dont on repart : sans ça,
    // le deuxième enregistrement se croirait en conflit avec le premier.
    rec.base = r.base || null;
    // Une recipe neuve vient d'obtenir son nœud : l'éditeur en retient
    // l'identité, que les écritures suivantes rendront au serveur.
    if (r.unique_id) rec.modelUid = r.unique_id;
    $("rec-delete").hidden = false;
    $("rec-rename").hidden = false;
    markSaved();
    toast("Recipe enregistrée", [r.path, r.recipe_path].join("\n"), "ok");
    if (r.parse_error) toast("dbt parse échoue", r.parse_error, "error");
    if (run) { show("flow"); $("drawer").classList.add("open"); }
    // Le manifeste vient de changer : les menus de références aussi.
    refreshRefs();
    loadFlow(true);
  } catch (e) {
    if (e.code === "sql_diverge" && state.rec === rec) {
      return openSqlDivergeModal(e.message, run);
    }
    toast("Enregistrement impossible", e.message, "error");
  }
}

// Un modèle écrit à la main s'édite ici : c'est son fichier .sql qui est la
// source de vérité. Un modèle produit par une recipe, lui, est regénéré à
// chaque enregistrement de la recipe — l'éditer ici ne servirait qu'à perdre
// la modification au prochain « Enregistrer ».
let sqlTarget = null;


function resetSqlTargets() {
  sqlTarget = null;
  sqlDivTarget = null;
}


async function openSqlModal(uidNode, editable = false) {
  const ctx = currentProject();
  try {
    const d = await api(`/api/dataset/${encodeURIComponent(uidNode)}`,
                        { project: ctx.project });
    if (ctx.stale()) return;
    // `d.editable` est le dernier mot : un modèle installé par `dbt deps` n'a
    // pas son fichier dans ce projet, et l'enregistrer écrirait à côté.
    const rw = !!editable && d.kind === "model" && d.editable !== false;
    // `base` : l'empreinte du fichier tel qu'on vient de le lire. On la rend à
    // la sauvegarde, qui refuse alors d'écraser une version écrite entre-temps
    // par un autre onglet ou un éditeur de texte.
    sqlTarget = { uid: uidNode, editable: rw, base: d.sql_digest || null,
                  project: ctx.project };

    $("sql-title").textContent = rw ? "SQL du modèle" : "SQL dbt généré";
    $("sql-path").textContent = d.path || "";
    $("sql-err").textContent = "";
    $("sql-note").textContent = rw
      ? "Jinja dbt autorisé : ref(), source(), macros."
      : (d.editable === false && d.package
          ? `Installé par le paquet « ${d.package} » : il se lit et se référence, `
            + `mais se modifie dans le dépôt du paquet.`
          : generatedByRecipe(uidNode)
              ? "Généré par une recipe de l'atelier : ouvrez la recipe pour le modifier."
              : "");
    $("sql-view").hidden = rw;
    $("sql-edit-host").hidden = !rw;
    $("sql-save").hidden = !rw;
    if (rw) {
      const host = $("sql-edit-host");
      host.innerHTML = "";
      sqlTarget.editor = sqlEditor(d.raw_sql || "");
      host.appendChild(sqlTarget.editor);
    } else {
      $("sql-view").innerHTML = hlSql(d.raw_sql || "(pas de SQL)");
    }

    $("sql-scrim").classList.add("on");
    if (rw) sqlTarget.editor.textarea.focus();
  } catch (e) {
    toast("SQL illisible", e.message, "error");
  }
}


function generatedByRecipe(uidNode) {
  return (state.flow.recipes || []).some((r) => r.model === uidNode && r.managed);
}



/* ============================ ONGLET SORTIE ============================ */

/* Ce que les autres modules appellent. La liste était celle de tout ce que
   le module déclare — soixante-seize noms pour `recipe.js`, dont dix-sept
   servaient — et un export qui ne sort jamais du fichier n'est pas une
   interface : c'est une invitation à en dépendre, et une frontière qu'on ne
   peut plus lire. Un test échoue si un nom réapparaît ici sans preneur. */
export {
  edited,
  generatedByRecipe,
  newRecipeCtx,
  openNewRecipe,
  openRecipeEditor,
  openRecipeNode,
  openSqlModal,
  leaveRecipe,
  renderScript,
  resetSqlTargets,
  selectRecipeTab,
  slugAlias,
  toSpecInputs,
  touch,
};

/* Les branchements de ce module. Appelé par `main.js` une fois
   tous les modules évalués : aucun effet de bord ne doit avoir lieu
   pendant le chargement, sinon les imports croisés se mordent la
   queue. */
export function wire() {
  // L'onglet Sortie ne connaît pas son éditeur : c'est lui qu'on lui donne.
  installOutput({ edited, refreshPreview, touch });

  $("nr-mat").addEventListener("change", renderMatHint);


  $("nr-add-input").addEventListener("click", () => {
    newRecipeCtx.inputs.push(null);
    renderNewInputs();
  });
  $("nr-cancel").addEventListener("click", () => $("scrim").classList.remove("on"));
  $("scrim").addEventListener("click", (e) => { if (e.target === $("scrim")) $("scrim").classList.remove("on"); });


  $("nr-create").addEventListener("click", () => {
    const name = $("nr-name").value.trim();
    if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(name)) {
      $("nr-err").textContent = "Nom invalide : lettres, chiffres et tirets bas.";
      return;
    }
    const inputs = newRecipeCtx.inputs.filter(Boolean);
    if (!inputs.length) { $("nr-err").textContent = "Choisissez au moins un dataset d'entrée."; return; }
    if (inputs.length < newRecipeCtx.min) {
      $("nr-err").textContent = `Cette recipe demande au moins ${newRecipeCtx.min} entrées.`; return;
    }
    if (!newRecipeCtx.multi && inputs.length > 1) {
      $("nr-err").textContent = "Cette recipe ne prend qu'une entrée."; return;
    }

    const spec = {
      name,
      type: newRecipeCtx.type,
      inputs: toSpecInputs(inputs),
      output: {
        layer: $("nr-layer").value,
        materialized: $("nr-mat").value,
        description: $("nr-desc").value.trim(),
      },
      steps: [],
    };
    if (spec.type === "join") {
      spec.joins = inputs.slice(1).map(() => ({ type: "left", on: [{ left: "", right: "" }] }));
    }
    if (spec.type === "group") {
      spec.group_by = [];
      spec.aggregations = [{ fn: "count", column: "*", alias: "nb_lignes" }];
    }
    if (spec.type === "distinct") { spec.type = "prepare"; spec.steps = [{ id: uid(), type: "distinct_rows", enabled: true, params: {} }]; }
    // Une source ne se référence pas comme un modèle : `ref('raw.orders')` ne
    // compile pas. C'est `toSpecInput` qui sait faire la différence — le SQL de
    // départ doit la faire aussi.
    if (spec.type === "sql") spec.sql = `select *\nfrom ${jinjaRef(spec.inputs[0])}\n`;
    $("scrim").classList.remove("on");
    openRecipeEditor(spec, true);
  });

  /* L'entrée de recipe que fait un choix de l'inventaire. Le paquet et la
     version font partie de l'identité du dataset choisi : les laisser tomber ici
     — ce que faisait cette fonction — rendait un `ref('orders')` nu, que dbt
     résout dans le projet ouvert. L'atelier montrait le modèle du paquet et
     compilait celui du projet, sans que rien ne le dise. */

  /* Le navigateur ne laisse pas choisir le texte : il affiche le sien dès que
     le gestionnaire annule l'événement. C'est le seul moyen d'intercepter une
     fermeture d'onglet ou un rechargement — ni l'un ni l'autre ne passe par
     « retour ». */
  window.addEventListener("beforeunload", (e) => {
    if (!state.rec || !state.rec.dirty) return;
    e.preventDefault();
    e.returnValue = "";
  });

  $("rec-back").addEventListener("click", async () => {
    if (!(await leaveRecipe())) return;
    show("flow");
    loadFlow(true);
  });

  /* ---- historique d'édition ----

     Un script d'étapes est une liste : l'historique est donc une pile d'états
     entiers de la spec, et non un journal d'opérations. C'est grossier, mais
     une recipe pèse quelques kilo-octets, et ça évite d'avoir à écrire l'inverse
     de chaque geste — l'endroit exact où un « annuler » se met à mentir.

     Rien n'est écrit hors de l'onglet : le `.sql` sur le disque reste la seule
     vérité, et l'historique meurt avec la page. */


  $("btn-undo").addEventListener("click", () => travelHistory(-1));
  $("btn-redo").addEventListener("click", () => travelHistory(1));


  document.addEventListener("keydown", (e) => {
    if (state.view !== "recipe" || !state.rec) return;
    if (!(e.metaKey || e.ctrlKey)) return;
    const keyOf = (e.key || "").toLowerCase();
    if (keyOf !== "z" && keyOf !== "y") return;
    // Dans un champ de saisie, ⌘Z appartient au champ : c'est le texte en cours
    // qu'on annule, pas l'étape.
    const target = e.target;
    if (target && /^(input|textarea)$/i.test(target.tagName || "")) return;
    e.preventDefault();
    travelHistory(keyOf === "y" || e.shiftKey ? 1 : -1);
  });


  $("rec-limit").addEventListener("change", doPreview);
  $("rec-refresh").addEventListener("click", doPreview);
  $("rec-auto").addEventListener("change", () => {
    try { localStorage.setItem(AUTO_KEY, $("rec-auto").value); } catch (_) { /* navigation privée */ }
    if (previewAuto() && state.rec && state.rec.stale) doPreview();
    else renderGridBar();
  });
  try {
    const keeps = localStorage.getItem(AUTO_KEY);
    if (keeps) $("rec-auto").value = keeps;
  } catch (_) { /* navigation privée */ }

  /* ---- menu de colonne ---- */


  document.addEventListener("click", (e) => {
    if (!e.target.closest("#cmenu") && !e.target.closest(".hname")) {
      $("cmenu").classList.remove("on");
    }
  });


  /* ---- bibliothèque de processeurs ---- */

  $("btn-add-step").addEventListener("click", () => {
    state.lib = { cat: null, search: "", pick: null };
    $("lib-search").value = "";
    renderLibrary();
    $("lib").classList.add("on");
    $("lib-search").focus();
  });
  $("lib-close").addEventListener("click", () => $("lib").classList.remove("on"));
  $("lib-search").addEventListener("input", () => {
    state.lib.search = $("lib-search").value.toLowerCase();
    renderLibrary();
  });
  // Le menu contextuel n'est pas une modale : il n'a ni focus à rendre ni
  // contenu à remettre à zéro, et il se ferme donc ici. Les dix modales, elles,
  // sont l'affaire de `modals.js`, qui tient la seule liste complète.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $("cmenu").classList.remove("on");
  });

  $("imp-cancel").addEventListener("click", closeImpact);
  $("imp-scrim").addEventListener("click", (e) => {
    if (e.target === $("imp-scrim")) closeImpact();
  });
  $("imp-go").addEventListener("click", () => {
    const t = impactTarget;
    closeImpact();
    if (t && state.rec === t.rec) doSave(t.run);
  });

  /* Le `.sql` sur disque ne dit plus ce que le script visuel produit : quelqu'un
     l'a écrit à la main. Le serveur refuse plutôt que de le régénérer en silence,
     et l'écran pose la question — rouvrir la recipe n'y changerait rien, c'est un
     choix à faire. Même geste que la modale d'impact : on dit ce qui va être
     remplacé, puis on laisse décider. */
  $("sqldiv-cancel").addEventListener("click", closeSqlDiv);
  $("sqldiv-scrim").addEventListener("click", (e) => {
    if (e.target === $("sqldiv-scrim")) closeSqlDiv();
  });
  $("sqldiv-go").addEventListener("click", () => {
    const t = sqlDivTarget;
    closeSqlDiv();
    if (t && state.rec === t.rec) doSave(t.run, true);
  });

  $("rec-save").addEventListener("click", () => saveRecipe(false));
  $("rec-run").addEventListener("click", () => saveRecipe(true));


  $("rec-sql-btn").addEventListener("click", async () => {
    const rec = state.rec;
    if (!rec) return;
    try {
      const r = await post("/api/recipe/compile", { spec: rec.spec });
      $("sql-path").textContent = r.path;
      $("sql-view").innerHTML = hlSql(r.sql);
      $("sql-scrim").classList.add("on");
    } catch (e) {
      toast("Compilation impossible", e.message, "error");
    }
  });
  $("sql-close").addEventListener("click", () => $("sql-scrim").classList.remove("on"));
  $("sql-scrim").addEventListener("click", (e) => {
    if (e.target === $("sql-scrim")) $("sql-scrim").classList.remove("on");
  });

  // Un modèle écrit à la main s'édite ici : c'est son fichier .sql qui est la
  // source de vérité. Un modèle produit par une recipe, lui, est regénéré à
  // chaque enregistrement de la recipe — l'éditer ici ne servirait qu'à perdre
  // la modification au prochain « Enregistrer ».

  $("sql-save").addEventListener("click", async () => {
    if (!sqlTarget || !sqlTarget.editable) return;
    const btn = $("sql-save");
    const target = sqlTarget;
    // Le texte tel qu'il part. Le bouton se grise pendant la requête, mais la
    // zone de saisie, elle, reste vivante : fermer la modale au retour jetait
    // sans un mot ce qui avait été tapé entre-temps.
    const sent = target.editor.textarea.value;
    btn.disabled = true;
    $("sql-err").textContent = "";
    try {
      const r = await put(`/api/dataset/${encodeURIComponent(target.uid)}/sql`,
                          { sql: sent, base: target.base },
                          { project: target.project });
      if (sqlTarget !== target) return;  // la modale a changé de modèle entre-temps
      // L'empreinte de ce qui vient d'être écrit : sans elle, un deuxième
      // enregistrement se croirait en conflit avec le premier.
      target.base = r.digest || null;
      const stillThere = target.editor.textarea.value !== sent;
      if (!stillThere) $("sql-scrim").classList.remove("on");
      toast(
        stillThere ? "SQL enregistré — mais vous avez continué à écrire" : "SQL enregistré",
        stillThere
          ? `${r.path}\nLa version enregistrée est celle d'avant vos dernières `
            + `frappes. Enregistrez à nouveau pour les écrire.`
          : r.path,
        stillThere ? "warn" : "ok",
      );
      if (r.parse_error) toast("dbt parse échoue", r.parse_error, "error");
      loadFlow(true);
    } catch (e) {
      if (sqlTarget === target) $("sql-err").textContent = e.message;
    } finally {
      btn.disabled = false;
    }
  });

}
