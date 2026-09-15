/* Parler au serveur, et dire de quel projet on croit parler. */
import { $, el, esc } from "./core.js";
import { state } from "./state.js";
import { loadFlow, view } from "./flow.js";
import { loadHome } from "./projects.js";
import { leaveRecipe } from "./recipe.js";


/* ------------------------------------------------------------------- API */

async function api(path, opts = {}) {
  let res;
  // Le projet que cet onglet croit modifier part avec la requête. Le projet
  // actif est global au serveur : un autre onglet peut en avoir ouvert un
  // autre depuis, et une écriture atterrirait alors dans le mauvais. Le
  // serveur compare et refuse plutôt que d'écrire ailleurs.
  const headers = { "Content-Type": "application/json" };
  // `opts.project` est le projet que *ce formulaire-là* avait sous les yeux
  // quand il s'est rempli. Sans lui, on envoyait le projet actif — donc on
  // était toujours d'accord avec le serveur, y compris juste après une
  // bascule : la garde ne pouvait alors rien refuser.
  const project = "project" in opts ? opts.project : projectDir();
  if (project) headers["X-Pliq-Project"] = project;
  try {
    res = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  } catch (_) {
    // `fetch` ne rejette que si la requête n'est jamais partie, ou jamais
    // revenue. « Failed to fetch » n'apprend rien à personne : dans un atelier
    // local, c'est presque toujours le serveur qui n'est plus là, et la page
    // qui lui a survécu.
    throw new Error("L'atelier ne répond plus — le serveur pliq est-il toujours lancé ?");
  }
  let body = null;
  try { body = await res.json(); } catch (_) { /* vide */ }
  if (!res.ok) {
    const err = new Error((body && body.error) || `${res.status} ${res.statusText}`);
    // Le serveur nomme les refus sur lesquels l'écran a quelque chose à
    // proposer. Sans ce code, il faudrait reconnaître une phrase française.
    if (body && body.code) err.code = body.code;
    throw err;
  }
  return body;
}
const post = (p, b, opts) =>
  api(p, { method: "POST", body: JSON.stringify(b || {}), ...(opts || {}) });
const put = (p, b, opts) =>
  api(p, { method: "PUT", body: JSON.stringify(b || {}), ...(opts || {}) });

/* Le projet ouvert à cet instant. Un écran qui lit des données en garde une
   copie : c'est celle-là qu'il rendra en écrivant, et pas celle d'un projet
   que quelqu'un aurait ouvert entre-temps depuis un autre onglet. */
const projectDir = () => (state.project && state.project.project_dir) || null;

/* Le projet sous lequel un écran se remplit, retenu *avant* sa requête.

   L'atelier n'a qu'un projet ouvert à la fois, et c'est le serveur qui le
   tient : basculer de A vers B pendant qu'une lecture est en vol fait arriver
   des données de A dans un écran qui croit montrer B. Le formulaire rendait
   alors, à l'enregistrement, le projet *d'arrivée* — donc la garde
   `X-Pliq-Project` validait au lieu de refuser, et la documentation destinée
   à A s'écrivait dans B.

   `perime()` se lit au retour de chaque `await` : une réponse qui décrit un
   projet qu'on a quitté s'abandonne, elle ne s'affiche pas. */
function currentProject() {
  const project = projectDir();
  return { project, stale: () => projectDir() !== project };
}


/* La liste des datasets référençables, rechargée quand le graphe change.

   `state.refs` n'était rempli qu'au démarrage, à la bascule de projet et après
   une déclaration de source. Ni l'enregistrement d'une recipe, ni un
   renommage, ni une suppression ne la rafraîchissaient — et `openNewRecipe`
   ne la relisait que si elle était *vide*. Un modèle qu'on venait de créer
   manquait donc aux menus « Entrée », et un modèle renommé y figurait encore
   sous son ancien nom jusqu'au rechargement de la page.

   Une génération plutôt qu'un simple `await` : deux rafraîchissements qui se
   croisent ne doivent pas réinstaller la liste la plus ancienne. */
let refsGen = 0;

async function refreshRefs() {
  const gen = ++refsGen;
  const { stale } = currentProject();
  try {
    const r = await api("/api/refs");
    if (gen !== refsGen || stale()) return;
    state.refs = r.refs || [];
  } catch (_) {
    /* La liste précédente reste : elle vaut mieux qu'une liste vide. */
  }
}


function toast(title, message, kind = "") {
  const t = el("div", `toast ${kind}`);
  t.appendChild(el("b", null, esc(title)));
  if (message) t.appendChild(el("span", null, esc(message)));
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), kind === "error" || kind === "warn" ? 10000 : 4200);
}

/* ------------------------------------------------------------------ vues */


/* ------------------------------------------------------------------ vues */

function show(view) {
  state.view = view;
  ["flow", "explore", "recipe", "home"].forEach((v) =>
    $("view-" + v).classList.toggle("on", v === view));
  $("top-tools").style.display = view === "flow" ? "flex" : "none";
  const atHome = view === "home";
  $("btn-run").style.display = atHome ? "none" : "";
  $("btn-log").style.display = atHome ? "none" : "";
  renderCrumb();
}

/* La cible ne figurait que sur les cartes de l'accueil et dans la bannière du
   terminal : l'écran où l'on lance les builds n'en disait rien. Elle décide
   pourtant de la base qu'on écrit, et elle peut changer sans qu'on l'ait
   demandé — une bascule de projet qui ne déclare pas la cible de session s'en
   remet au défaut du profil. Autant l'avoir sous les yeux. */
function renderTarget() {
  const dot = $("top-target");
  const target = state.project && state.project.target;
  dot.hidden = state.view === "home" || !target;
  if (dot.hidden) return;
  dot.textContent = target;
  dot.title =
    `Toutes les commandes dbt lancées d'ici visent la cible « ${target} »`
    + (state.project.adapter ? ` (${state.project.adapter})` : "") + ".";
}


function renderCrumb() {
  const p = state.project;
  renderTarget();
  if (state.view === "home") {
    $("crumb").innerHTML = `<b>Projets</b>`;
    return;
  }
  const parts = [`<button data-go="home">Projets</button><span class="sep">/</span>`,
                 `<b>${esc(p ? p.name : "…")}</b>`, `<span class="sep">/</span>`];
  if (state.view === "flow") parts.push("Flow");
  else if (state.view === "explore")
    parts.push(`<button data-go="flow">Flow</button><span class="sep">/</span>${esc(state.explore ? state.explore.name : "")}`);
  else if (state.view === "recipe")
    parts.push(`<button data-go="flow">Flow</button><span class="sep">/</span>recipe ${esc(state.rec ? state.rec.spec.name : "")}`);
  $("crumb").innerHTML = parts.join(" ");
  $("crumb").querySelectorAll("[data-go]").forEach((b) =>
    b.addEventListener("click", async () => {
      // Le fil d'Ariane quitte l'écran de recipe comme le bouton « retour » :
      // il ne posait, lui, aucune question, et le brouillon partait sans un mot.
      if (!(await leaveRecipe())) return;
      if (b.dataset.go === "home") { show("home"); loadHome(); }
      else { show("flow"); loadFlow(); }
    }));
}

/* ------------------------------------------------------------------ FLOW */

/* On déplace le plan, pas la fenêtre.
   `.flow` porte une transformation translate+scale que le viewport découpe :
   zoomer ou se déplacer ne recalcule ni le layout ni le DOM, ce qui tient
   encore à plusieurs centaines de nœuds. */

export {
  api,
  post,
  projectDir,
  currentProject,
  put,
  refreshRefs,
  renderCrumb,
  show,
  toast,
};
