/* Lancer dbt, suivre son journal, et la fraîcheur des sources. */
import { $, el, esc } from "./core.js";
import { state } from "./state.js";
import { post, show, toast } from "./api.js";
import { loadFlow, paintStates } from "./flow.js";
import { selectRecipeTab } from "./recipe.js";
import { reloadProject } from "./projects.js";


/* ------------------------------------------------------------ coloration */

const SQL_RE = /(\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\})|(--[^\n]*)|('(?:[^'])*')|\b(\d+(?:\.\d+)?)\b|\b(with|select|from|where|and|or|as|left|right|inner|outer|full|cross|join|on|group|by|order|case|when|then|else|end|cast|distinct|count|sum|min|max|avg|coalesce|lower|upper|not|null|is|in|between|union|all|limit|filter|round|replace|trim|split_part|year|month|initcap)\b/gi;

// Un textarea ne se colore pas. On glisse donc un `<pre>` coloré dessous et on
// rend le texte du textarea transparent : frappe, curseur, sélection, annuler
// restent natifs, la couleur est peinte derrière, à la même place.

// Un textarea ne se colore pas. On glisse donc un `<pre>` coloré dessous et on
// rend le texte du textarea transparent : frappe, curseur, sélection, annuler
// restent natifs, la couleur est peinte derrière, à la même place.
function sqlEditor(value, onInput) {
  const box = el("div", "sqled");
  const hl = el("pre");
  hl.setAttribute("aria-hidden", "true");
  const ta = el("textarea");
  ta.spellcheck = false;
  ta.value = value || "";
  // Le `\n` final donne au calque la hauteur d'une ligne vide en cours de
  // frappe : sans lui, la dernière ligne saute sous le bord.
  const paint = () => { hl.innerHTML = hlSql(ta.value) + "\n"; };
  paint();
  ta.addEventListener("input", () => { paint(); if (onInput) onInput(ta.value); });
  ta.addEventListener("scroll", () => { hl.scrollTop = ta.scrollTop; });
  box.appendChild(hl);
  box.appendChild(ta);
  box.textarea = ta;
  return box;
}


function hlSql(code) {
  return esc(code).replace(SQL_RE, (m, j, c, s, n, k) => {
    if (j) return `<span class="j">${m}</span>`;
    if (c) return `<span class="c">${m}</span>`;
    if (s) return `<span class="s">${m}</span>`;
    if (n) return `<span class="n">${m}</span>`;
    if (k) return `<span class="k">${m}</span>`;
    return m;
  });
}

/* ------------------------------------------------------------------ runs */


/* ------------------------------------------------------------------ runs */

function logLine(text, cls = "") {
  const pre = $("log");
  const line = el("span", cls);
  line.innerHTML = `<span class="t">${new Date().toTimeString().slice(0, 8)}</span>  ${esc(text)}`;
  pre.appendChild(line);
  pre.scrollTop = pre.scrollHeight;
  while (pre.children.length > 1000) pre.removeChild(pre.firstChild);
}


// Les commandes pour lesquelles `--full-refresh` a un sens : dbt l'ignore
// ailleurs, et l'offrir laisserait croire qu'il fait quelque chose.
const FULL_REFRESH_COMMANDS = ["build", "run", "seed"];

async function runDbt(command, select_) {
  if (state.running) return toast("Exécution en cours", "Attendez la fin du run courant.");
  const cmd = command || $("command-sel").value;
  const sel = select_ !== undefined ? select_ : $("select-input").value;
  // `--exclude` et `--full-refresh` : l'API les accepte depuis toujours, la
  // barre du haut ne les envoyait pas, et la table de couverture du README
  // les annonçait pourtant. Ils sont transmis quand ils veulent dire quelque
  // chose, et seulement pour un lancement depuis la barre — `runDbt("deps")`
  // ne doit pas hériter de l'exclusion qui traîne dans le champ.
  const bar = command === undefined && select_ === undefined;
  const exclude = bar ? $("exclude-input").value : "";
  const full = bar && FULL_REFRESH_COMMANDS.includes(cmd) && $("full-refresh").checked;
  $("drawer").classList.add("open");
  try {
    await post("/api/run", {
      command: cmd, select: sel, exclude, full_refresh: full,
    });
  } catch (e) { toast("Lancement impossible", e.message, "error"); }
}


/* La case n'a de sens que pour certaines commandes : elle disparaît pour les
   autres plutôt que de rester cochable sans effet. */
function renderRunOptions() {
  const cmd = $("command-sel").value;
  $("full-refresh-lab").hidden = !FULL_REFRESH_COMMANDS.includes(cmd);
}

/* Un projet existant qui déclare des paquets ne parse pas tant que `dbt deps`
   n'a pas tourné : l'atelier n'affichait qu'un message brut de dbt, et rien ne
   disait quoi faire. */
function renderDepsWarning() {
  const p = state.project;
  $("deps-warn").hidden = !(p && p.deps_needed);
}

const commandLabel = () => {
  const c = $("command-sel").value;
  return c === "freshness" ? "dbt source freshness" : "dbt " + c;
};

function setRunning(on, cmd) {
  state.running = on;
  $("btn-run").disabled = on;
  $("btn-run").querySelector(".dot").classList.toggle("spin", on);
  $("run-label").textContent = on ? "en cours…" : commandLabel();
  if (on && cmd) $("log-note").textContent = `dbt ${cmd} en cours`;
}


function updateStates(states, freshness) {
  if (!state.flow) return;
  state.flow.states = Object.assign(state.flow.states || {}, states || {});
  if (freshness) state.flow.freshness = freshness;
  paintStates();
}

/* --- fraîcheur des sources ---

   Un modèle vert construit à partir d'une source figée depuis trois semaines
   est vert quand même : le build a réussi. C'est le genre de vérité partielle
   qu'un Flow ne doit pas laisser passer. */


const FRESH_LABEL = {
  success: "à jour", warn: "vieillissante", error: "périmée",
};


const PERIOD_LABEL = { minute: "minutes", hour: "heures", day: "jours" };


function freshnessOf(id) {
  return ((state.flow || {}).freshness || {})[id] || null;
}


function humanDuration(s) {
  if (s == null) return "";
  const n = Math.round(s);
  if (n < 90) return `${n} s`;
  if (n < 5400) return `${Math.round(n / 60)} min`;
  if (n < 172800) return `${Math.round(n / 3600)} h`;
  return `${Math.round(n / 86400)} jours`;
}

// Échecs de connexion consécutifs. Un rechargement de page en produit un seul :
// on n'alerte qu'au-delà, pour ne pas faire clignoter la barre à chaque F5.

// Échecs de connexion consécutifs. Un rechargement de page en produit un seul :
// on n'alerte qu'au-delà, pour ne pas faire clignoter la barre à chaque F5.
let wsFails = 0;
let wsTimer = null;
const WS_ALERT = 3;


function scheduleWs() {
  clearTimeout(wsTimer);
  // Palier à 10 s : inutile de marteler un serveur qui ne répondra pas.
  wsTimer = setTimeout(connectWs, Math.min(1800 * Math.max(1, wsFails), 10000));
}


function onWsDown() {
  wsFails += 1;
  if (wsFails >= WS_ALERT) $("ws-warn").hidden = false;
  scheduleWs();
}


function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  let ws;
  try {
    ws = new WebSocket(`${proto}://${location.host}/ws`);
  } catch (e) {
    // Rien ne s'est ouvert : onclose ne viendra pas, on relance nous-mêmes.
    setRunning(false);
    onWsDown();
    return;
  }
  ws.onopen = () => { wsFails = 0; $("ws-warn").hidden = true; };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "hello") {
      updateStates(m.states, m.freshness);
      setRunning(m.run && m.run.running, m.run && m.run.command);
      // `hello` porte désormais l'identité du projet et sa génération : un
      // onglet qui a manqué un `project_changed` ou un `run_done` pendant une
      // coupure restait sinon sur une vue périmée, sans que rien ne le lui
      // dise. On compare, et on recharge seulement si quelque chose a bougé.
      resync(m);
    } else if (m.type === "log") {
      logLine(m.msg, m.level === "error" ? "error" : m.level === "warn" ? "warn" : "");
      if (m.node && m.node_status) updateStates({ [m.node]: { status: mapStatus(m.node_status) } });
    } else if (m.type === "run_start") {
      setRunning(true, m.command);
      logLine(`$ dbt ${m.command}${m.select ? " --select " + m.select : ""}`, "cmd");
    } else if (m.type === "run_done") {
      setRunning(false);
      updateStates(m.states, m.freshness);
      const ok = m.run.success;
      logLine(`${ok ? "Terminé" : "Terminé avec des erreurs"} en ${(m.run.duration || 0).toFixed(1)} s`,
              ok ? "done" : "error");
      $("log-note").textContent = ok ? "dernière exécution réussie" : "dernière exécution en échec";
      loadFlow(false);
      toast(ok ? "Exécution terminée" : "Exécution en échec", `dbt ${m.run.command}`, ok ? "ok" : "error");
    } else if (m.type === "flow_changed") {
      loadFlow(false);
    } else if (m.type === "project_changed") {
      onProjectChanged(m);
    }
  };
  ws.onclose = () => { setRunning(false); onWsDown(); };
}

/* La dernière génération de projet vue par cet onglet, et le dernier run
   connu. Une reconnexion les confronte à ce que `hello` annonce. */
let viewGeneration = null;
let viewRun = null;

async function resync(m) {
  const sameProject = !m.project_dir
    || m.project_dir === (state.project && state.project.project_dir);
  const generation = m.generation == null ? null : m.generation;
  const run = m.run ? `${m.run.run_id || ""}:${m.run.finished_at || ""}` : "";

  const firstOne = viewGeneration === null && viewRun === null;
  const projectMoved = !sameProject
    || (viewGeneration !== null && generation !== null && generation !== viewGeneration);
  const runMoved = !firstOne && run !== viewRun;

  viewGeneration = generation;
  viewRun = run;
  if (firstOne) return;  // la connexion initiale vient de tout charger

  if (projectMoved) {
    await onProjectChanged({ path: m.project_dir, name: m.project });
    return;
  }
  // Même projet, mais un run s'est terminé pendant la coupure : le graphe et
  // les états qu'il affiche sont d'avant.
  if (runMoved) loadFlow(false);
}


/* Un autre onglet — ou cette page — a ouvert un autre projet. L'atelier n'en
   sert qu'un à la fois : continuer à afficher l'ancien laisse l'écran mentir,
   et une écriture partirait dans le nouveau. On se recale donc, en prévenant
   quand du travail non enregistré est perdu de vue. */
async function onProjectChanged(m) {
  const before = state.project && state.project.project_dir;
  if (before && m.path === before) return;   // c'est cet onglet qui a basculé

  const lost = state.rec && state.rec.dirty ? state.rec.spec.name : null;
  const closed = await reloadProject();
  if (state.view !== "home") show("flow");

  const closes = closed.length
    ? ` Fermé, faute de porter sur ce projet : ${closed.join(", ")}.`
    : "";
  if (lost)
    toast("Projet changé ailleurs",
          `L'atelier a ouvert « ${m.name} ». La recipe « ${lost} » n'était pas `
          + "enregistrée : elle n'a été écrite ni ici, ni là-bas." + closes, "error");
  else if (closed.length)
    toast("Projet changé ailleurs", `L'atelier a ouvert « ${m.name} ».` + closes, "warn");
  else
    toast("Projet ouvert", m.name);
}


function mapStatus(s) {
  return { started: "running", compiling: "running", executing: "running",
           success: "success", pass: "success", warn: "warn", error: "error",
           fail: "error", skipped: "skipped" }[s] || s;
}

export {
  FRESH_LABEL,
  PERIOD_LABEL,
  connectWs,
  freshnessOf,
  hlSql,
  humanDuration,
  logLine,
  renderDepsWarning,
  runDbt,
  setRunning,
  sqlEditor,
  updateStates,
};

/* Les branchements de ce module. Appelé par `main.js` une fois
   tous les modules évalués : aucun effet de bord ne doit avoir lieu
   pendant le chargement, sinon les imports croisés se mordent la
   queue. */
export function wire() {

  $("deps-warn").addEventListener("click", async () => {
    await runDbt("deps", "");
    $("deps-warn").hidden = true;
  });


  $("btn-run").addEventListener("click", () => runDbt());
  $("command-sel").addEventListener("change", () => {
    $("run-label").textContent = commandLabel();
    renderRunOptions();
  });
  renderRunOptions();
  $("btn-log").addEventListener("click", () => $("drawer").classList.toggle("open"));
  $("btn-close-log").addEventListener("click", () => $("drawer").classList.remove("open"));
  $("btn-clear-log").addEventListener("click", () => ($("log").innerHTML = ""));


  document.querySelectorAll("[data-stab]").forEach((tab) => {
    tab.addEventListener("click", () => selectRecipeTab(tab.dataset.stab));
  });

  /* ---------------------------------------------------------------- départ */
}
