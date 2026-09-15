/* Déplacer, cadrer, replier, retrouver : la manipulation du canevas. */
import { $ } from "./core.js";
import { state } from "./state.js";
import { post } from "./api.js";
import { applyView, centerOnNode, clampView, closeFlowMenu, fitView, paintFlow, renderPanel, searchHits, unfoldAll, view, zoomAt, zoomBy } from "./flow.js";
import { renderScript, slugAlias } from "./recipe.js";


let findTimer = null;

const VISUAL_TYPES = ["join", "group", "stack"];


function aliasOf(inp, i) {
  return slugAlias(inp.alias || inp.ref || inp.table || `input_${i}`, i);
}
/* Jumeau d'`input_label` côté serveur. */
/* Jumeau d'`input_label` côté serveur. */
function inputLabel(inp) {
  if (!inp.ref) return `${inp.source_name}.${inp.table}`;
  const p = [inp.package, inp.version ? `v${inp.version}` : ""].filter(Boolean);
  return inp.ref + (p.length ? ` (${p.join(", ")})` : "");
}
function colsOf(alias) {
  const rec = state.rec;
  return (rec && rec.inputCols && rec.inputCols[alias]) || [];
}


async function loadInputColumns() {
  const rec = state.rec;
  if (!rec) return;
  rec.colError = "";
  try {
    const r = await post("/api/recipe/columns", { spec: rec.spec });
    rec.inputCols = r.columns || {};
  } catch (e) {
    rec.inputCols = {};
    rec.colError = e.message;
  }
  if (state.rec === rec) renderScript();
}

/* --------------------------------------------------------------- fabriques */

export {
  VISUAL_TYPES,
  aliasOf,
  colsOf,
  inputLabel,
  loadInputColumns,
};

/* Les branchements de ce module. Appelé par `main.js` une fois
   tous les modules évalués : aucun effet de bord ne doit avoir lieu
   pendant le chargement, sinon les imports croisés se mordent la
   queue. */
export function wire() {

  /* ------------------------------------------ déplacer et cadrer le canevas */

  (() => {
    const vp = $("flow-view");
    let drag = null;

    vp.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = vp.getBoundingClientRect();
      // Le pincement d'un trackpad arrive ici en molette + ctrl : même geste,
      // même effet que ⌘/Ctrl + molette.
      if (e.ctrlKey || e.metaKey) {
        zoomAt(view.k * Math.exp(-e.deltaY * 0.0022), e.clientX - r.left, e.clientY - r.top);
        return;
      }
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? r.height : 1;
      view.touched = true;
      view.x -= e.deltaX * unit;
      view.y -= e.deltaY * unit;
      clampView();
      applyView();
    }, { passive: false });

    vp.addEventListener("pointerdown", (e) => {
      // Seul le fond déplace. Capturer le pointeur — et couper les événements
      // souris avec `preventDefault` — sur un nœud ou sur la barre de zoom ferait
      // disparaître leur `click` : le bouton ne répondrait plus jamais.
      if (e.target.closest(".fnode, .zoombar")) return;
      if (e.button !== 0 && e.button !== 1) return;
      drag = { x: e.clientX, y: e.clientY, ox: view.x, oy: view.y, moved: false };
      // Un pointeur déjà relâché (doigt parti pendant le geste) fait lever la
      // capture : le déplacement reste bon, on ne veut pas mourir là-dessus.
      try { vp.setPointerCapture(e.pointerId); } catch (_) { /* plus de pointeur */ }
      vp.classList.add("dragging");
      e.preventDefault();
    });

    vp.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      // Quelques pixels de tolérance : sans quoi un clic un peu tremblant sur le
      // fond passerait pour un déplacement et ne désélectionnerait plus rien.
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 3) return;
      drag.moved = true;
      view.touched = true;
      view.x = drag.ox + dx;
      view.y = drag.oy + dy;
      clampView();
      applyView();
    });

    const endDrag = (e) => {
      if (!drag) return;
      const moved = drag.moved;
      drag = null;
      vp.classList.remove("dragging");
      if (vp.hasPointerCapture(e.pointerId)) vp.releasePointerCapture(e.pointerId);
      if (!moved && e.button === 0 && !e.target.closest(".fnode") && state.selected) {
        closeFlowMenu();
        state.selected = null;
        paintFlow();
        renderPanel();
      }
    };
    vp.addEventListener("pointerup", endDrag);
    vp.addEventListener("pointercancel", endDrag);

    vp.addEventListener("keydown", (e) => {
      if (e.target !== vp) return;
      const step = e.shiftKey ? 240 : 80;
      const r = vp.getBoundingClientRect();
      const arrows = {
        ArrowLeft: () => (view.x += step), ArrowRight: () => (view.x -= step),
        ArrowUp: () => (view.y += step), ArrowDown: () => (view.y -= step),
      };
      if (arrows[e.key]) { view.touched = true; arrows[e.key](); clampView(); applyView(); }
      else if (e.key === "+" || e.key === "=") zoomBy(1.25);
      else if (e.key === "-") zoomBy(1 / 1.25);
      else if (e.key === "0") zoomAt(1, r.width / 2, r.height / 2);
      else if (e.key === "f" || e.key === "F") fitView();
      else return;
      e.preventDefault();
    });

    $("btn-zoom-in").addEventListener("click", () => zoomBy(1.25));
    $("btn-zoom-out").addEventListener("click", () => zoomBy(1 / 1.25));
    $("btn-zoom-fit").addEventListener("click", () => { view.touched = false; fitView(); });
    $("btn-zoom-pct").addEventListener("click", () => {
      const r = vp.getBoundingClientRect();
      zoomAt(1, r.width / 2, r.height / 2);
    });
    // Le viewport rétrécit quand le panneau ou la fenêtre changent de taille.
    window.addEventListener("resize", () => {
      if (view.touched) { clampView(); applyView(); } else fitView();
    });
  })();

  /* ------------------------------------------- replier, éclairer, retrouver */


  /* ------------------------------------------- replier, éclairer, retrouver */

  $("btn-unfold-all").addEventListener("click", unfoldAll);
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#flow-menu")) closeFlowMenu();
  });
  // Le menu est posé en coordonnées d'écran : dès que le plan bouge, il ment.
  // Le menu est posé en coordonnées d'écran : dès que le plan bouge, il ment.
  $("flow-view").addEventListener("wheel", closeFlowMenu, { passive: true });
  // Un clic droit dans le vide ferme le menu au lieu d'ouvrir celui du navigateur.
  // Un clic droit dans le vide ferme le menu au lieu d'ouvrir celui du navigateur.
  $("flow-view").addEventListener("contextmenu", (e) => {
    if (e.target.closest(".fnode")) return;
    e.preventDefault();
    closeFlowMenu();
  });


  $("btn-lineage").setAttribute("aria-pressed", String(state.lineage));
  $("btn-lineage").addEventListener("click", () => {
    state.lineage = !state.lineage;
    $("btn-lineage").setAttribute("aria-pressed", String(state.lineage));
    paintFlow();
  });

  $("flow-find").addEventListener("input", () => {
    clearTimeout(findTimer);
    findTimer = setTimeout(() => { state.find = $("flow-find").value; paintFlow(); }, 120);
  });
  $("flow-find").addEventListener("keydown", (e) => {
    if (e.key === "Escape") { $("flow-find").value = ""; }
    else if (e.key !== "Enter") return;
    clearTimeout(findTimer);
    state.find = $("flow-find").value;
    paintFlow();
    const hits = searchHits();
    if (hits && hits.size) centerOnNode(hits.values().next().value, 0.8);
    e.preventDefault();
  });


  // Échap n'est plus traité ici : les deux listes partielles qui vivaient dans
  // ce fichier et dans `recipe.js` en oubliaient chacune trois. `modals.js`
  // parcourt la seule liste qui nomme les dix, et ferme celle du dessus.

  /* ==========================================================================
     ÉDITEURS VISUELS : JOINDRE, GROUPER, EMPILER
     Ces recipes ont un SQL simple mais des réglages qui se voient mieux qu'ils
     ne s'écrivent : type de jointure, clés, colonnes retenues, mesures.
     ========================================================================== */
}
