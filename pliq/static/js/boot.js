/* Le démarrage, et la couche que l'atelier propose pour une recipe. */
import { $, esc } from "./core.js";
import { state } from "./state.js";
import { api, post, refreshRefs, renderCrumb, toast } from "./api.js";
import { loadFlow } from "./flow.js";
import { newRecipeCtx, toSpecInputs } from "./recipe.js";
import { connectWs, logLine, renderDepsWarning, setRunning, updateStates } from "./runs.js";
import { loadLibrary } from "./projects.js";


/* ---------------------------------------------------------------- départ */

async function boot() {
  try {
    const p = await api("/api/project");
    state.project = p;
    renderCrumb();
    renderDepsWarning();
    if (p.deps_needed)
      toast("Dépendances à installer",
            "Ce projet déclare des paquets dbt qui ne sont pas là. Lancez « deps ».");
    else if (p.manifest_error) toast("dbt parse", p.manifest_error, "error");
    if (!p.database_exists)
      toast("Base absente", "Lancez « dbt build » pour créer la base et activer les aperçus.");
  } catch (e) {
    toast("Projet illisible", e.message, "error");
  }

  await loadLibrary();

  await refreshRefs();

  await loadFlow(false);

  try {
    const r = await api("/api/run");
    (r.lines || []).forEach((l) =>
      logLine(l.msg, l.level === "error" ? "error" : l.level === "warn" ? "warn" : ""));
    setRunning(r.run && r.run.running, r.run && r.run.command);
    updateStates(r.states, r.freshness);
  } catch (_) { /* pas de run précédent */ }

  connectWs();
}


async function refreshLayerSuggestion() {
  const sel = $("nr-layer");
  const inputs = toSpecInputs((newRecipeCtx ? newRecipeCtx.inputs : []).filter(Boolean));

  let data = { layer: "staging", reason: "", layers: [] };
  try { data = await post("/api/layers/suggest", { inputs }); } catch (_) { /* défauts */ }
  if (!newRecipeCtx) return;
  newRecipeCtx.suggestion = data;

  const layers = data.layers && data.layers.length
    ? data.layers
    : [{ name: "staging" }, { name: "intermediate" }, { name: "marts" }, { name: "" }];
  const keep = newRecipeCtx.layerTouched ? sel.value : data.layer;

  sel.innerHTML = layers.map((l) => {
    const label = l.name ? "models/" + l.name : "models/ (racine)";
    const mat = l.materialized ? ` — ${l.materialized}` : "";
    return `<option value="${esc(l.name)}">${esc(label)}${esc(mat)}</option>`;
  }).join("");
  sel.value = layers.some((l) => l.name === keep) ? keep : (layers[0] ? layers[0].name : "");

  if (!newRecipeCtx.matTouched) applyLayerMaterialization();
  renderLayerHint();
}


function layerInfo(name) {
  const list = (newRecipeCtx && newRecipeCtx.suggestion && newRecipeCtx.suggestion.layers) || [];
  return list.find((l) => l.name === name) || null;
}


function applyLayerMaterialization() {
  const info = layerInfo($("nr-layer").value);
  if (info && info.materialized) $("nr-mat").value = info.materialized;
}


function renderLayerHint() {
  const hint = $("nr-layer-hint");
  const sug = (newRecipeCtx && newRecipeCtx.suggestion) || {};
  const current = $("nr-layer").value;
  const info = layerInfo(current);
  const bits = [];

  if (sug.layer === current && sug.reason) {
    bits.push(`<b>Suggéré</b> : ${esc(sug.reason)}.`);
  } else if (sug.layer !== undefined && sug.reason) {
    bits.push(`L'atelier proposait <b>${esc(sug.layer || "models/")}</b> — ${esc(sug.reason)}.`);
  }
  if (info && info.materialized) {
    bits.push(`<code>dbt_project.yml</code> matérialise cette couche en <b>${esc(info.materialized)}</b>.`);
  }
  if (info && info.exists === false) {
    bits.push(`Le dossier sera créé.`);
  }
  hint.innerHTML = bits.join(" ");
}

export {
  applyLayerMaterialization,
  boot,
  refreshLayerSuggestion,
  renderLayerHint,
};

/* Les branchements de ce module. Appelé par `main.js` une fois
   tous les modules évalués : aucun effet de bord ne doit avoir lieu
   pendant le chargement, sinon les imports croisés se mordent la
   queue. */
export function wire() {

  /* ==========================================================================
     COUCHE SUGGÉRÉE
     La convention dbt : staging (une vue par table brute) → intermediate
     (calculs) → marts (objets métier). On propose, l'utilisateur dispose.
     ========================================================================== */
}
