/* Voir les lignes d'un dataset, et la grille qui les affiche. */
import { $, el, esc } from "./core.js";
import { state } from "./state.js";
import { api, post, currentProject, show } from "./api.js";
import { findDataset } from "./flow.js";


/* --------------------------------------------------------------- EXPLORE */

/* Le numéro de la demande en cours. Ouvrir A puis B et recevoir A en dernier
   laissait le titre de B au-dessus des lignes de A : des données lues sous un
   nom qui n'est pas le leur, dans l'écran dont c'est justement le métier. */
let exploreGen = 0;

async function openExplore(id) {
  const d = findDataset(id);
  if (!d) return;
  const gen = ++exploreGen;
  const { stale } = currentProject();
  state.explore = d;
  show("explore");
  $("exp-name").textContent = d.name;
  $("exp-grid").innerHTML = `<div class="empty">chargement…</div>`;
  try {
    const limit = parseInt($("exp-limit").value, 10);
    const data = await post(`/api/dataset/${encodeURIComponent(id)}/explore`, { limit });
    if (gen !== exploreGen || stale()) return;
    $("exp-grid").innerHTML = "";
    $("exp-grid").appendChild(buildGrid(data.columns, data.rows));
    $("exp-sample").textContent = `${data.rows.length} lignes échantillonnées`;
    $("exp-sample").className = "sample-badge";
    $("exp-note").innerHTML =
      `${data.columns.length} colonnes` +
      (data.total != null ? ` · ${data.total} lignes au total` : "");
  } catch (e) {
    if (gen !== exploreGen || stale()) return;
    $("exp-grid").innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
  }
}

/* ------------------------------------------------------------------ grille */

const NUMERIC = /int|numeric|double|decimal|float|real|bigint|hugeint/i;


function buildGrid(columns, rows, opts = {}) {
  const tinted = opts.tinted || new Set();
  const table = el("table", "dgrid");

  const thead = el("thead");
  const tr = el("tr");
  columns.forEach((c, i) => {
    const th = el("th", `${c.gone ? "gone " : ""}${tinted.has(c.name) ? "tinted" : ""}`.trim());
    const ok = c.ok || 0, empty = c.empty || 0, nok = c.nok || 0;
    // Une colonne supprimée n'a plus ni type ni profil : les quatre blocs de
    // l'en-tête restent pourtant là, sinon sa case ne s'alignerait plus sur
    // celles de ses voisines.
    th.innerHTML = c.gone
      ? `<div class="hname" data-col="${esc(c.name)}" title="${esc(c.name)} — supprimée">
           <span>${esc(c.name)}</span><span class="caret">▾</span>
         </div>
         <div class="hstore tag-gone">supprimée</div>
         <div class="hmean"></div>
         <div class="qbar"></div>`
      : `<div class="hname" data-col="${esc(c.name)}" title="${esc(c.name)}">
           <span>${esc(c.name)}</span><span class="caret">▾</span>
         </div>
         <div class="hstore">${esc(c.storage || "")}</div>
         <div class="hmean">${esc(c.meaning_label || "")}</div>
         <div class="qbar" title="Valide ${ok}% · Vide ${empty}% · Invalide ${nok}%">
           <i class="q-ok" style="width:${ok}%"></i>
           <i class="q-empty" style="width:${empty}%"></i>
           <i class="q-nok" style="width:${nok}%"></i>
         </div>`;
    tr.appendChild(th);
  });
  thead.appendChild(tr);
  table.appendChild(thead);

  const tbody = el("tbody");
  rows.forEach((r) => {
    const row = el("tr");
    r.forEach((v, i) => {
      const c = columns[i] || {};
      const td = el("td");
      // Vide plutôt que `NULL` : la valeur n'est pas nulle, elle n'existe plus.
      if (c.gone) { td.className = "gone"; row.appendChild(td); return; }
      let cls = tinted.has(c.name) ? "tinted " : "";
      // `NULL` et la chaîne vide ne se filtrent ni ne s'agrègent pareil en
      // SQL : les afficher tous les deux « NULL » est particulièrement
      // trompeur dans un atelier de nettoyage, où la différence est
      // justement ce qu'on vient corriger. Une chaîne d'espaces se voit
      // aussi : sans marque, elle est indiscernable d'une cellule vide.
      if (v === null) { cls += "nul"; td.textContent = "NULL"; }
      else if (v === "") { cls += "nul"; td.textContent = "(vide)"; td.title = "chaîne vide"; }
      else if (typeof v === "string" && v.trim() === "") {
        cls += "nul";
        td.textContent = `(${v.length} espace${v.length > 1 ? "s" : ""})`;
        td.title = "chaîne d'espaces";
      } else {
        if (NUMERIC.test(c.type || "")) cls += "num ";
        td.textContent = String(v);
      }
      td.className = cls.trim();
      row.appendChild(td);
    });
    tbody.appendChild(row);
  });
  table.appendChild(tbody);

  if (opts.onColumnClick) {
    table.querySelectorAll(".hname").forEach((h) =>
      h.addEventListener("click", (ev) => opts.onColumnClick(h.dataset.col, ev)));
  }
  return table;
}

/* ------------------------------------------------------ nouvelle recipe */

export {
  buildGrid,
  openExplore,
};

/* Les branchements de ce module. Appelé par `main.js` une fois
   tous les modules évalués : aucun effet de bord ne doit avoir lieu
   pendant le chargement, sinon les imports croisés se mordent la
   queue. */
export function wire() {
  $("exp-back").addEventListener("click", () => { show("flow"); });
  $("exp-limit").addEventListener("change", () => state.explore && openExplore(state.explore.id));

  /* ------------------------------------------------------------------ grille */
}
