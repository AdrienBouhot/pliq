/* Les modales : une seule liste, un seul Échap, un focus qui revient.

   Les raccourcis étaient écrits à deux endroits — `canvas.js` et `recipe.js` —
   et chacun en oubliait : Échap ne fermait ni « Renommer », ni « Documenter &
   tester », ni « Remplacer le SQL ». Une seule des dix modales portait
   `role="dialog"`, aucune ne piégeait le focus, aucune ne le rendait à
   l'élément qui l'avait ouverte, et le fond restait tabulable.

   Rien de tout cela n'est propre à une modale en particulier : c'est le
   comportement d'une modale. Il est donc posé ici une fois, sur la liste que
   `projects.js` tient déjà, et il ne peut plus être oublié pour la onzième. */

import { $ } from "./core.js";
import { FORMS, closeForm } from "./projects.js";

const FOCUSABLE_SELECTOR = [
  "a[href]", "button:not([disabled])", "input:not([disabled])",
  "select:not([disabled])", "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/* L'élément qui avait le focus quand chaque modale s'est ouverte. Une pile,
   parce qu'une modale peut en ouvrir une autre — « Enregistrer » ouvre
   « Remplacer le SQL » — et que le retour doit alors se faire en sens
   inverse. */
const backs = new Map();

const isOpenModal = (box) => !!box && box.classList.contains("on");

/** Les modales ouvertes, dans l'ordre de la liste : la dernière est au-dessus. */
function openOnes() {
  return FORMS.map(([id]) => $(id)).filter(isOpenModal);
}

function focusables(box) {
  return [...box.querySelectorAll(FOCUSABLE_SELECTOR)].filter(
    (n) => !n.hidden && n.offsetParent !== null);
}

/** Nomme le dialogue pour un lecteur d'écran, une fois pour toutes. */
function nameOf(box) {
  const modal = box.classList.contains("modal") ? box : box.querySelector(".modal");
  const target = modal || box;
  if (target.getAttribute("role") !== "dialog") {
    target.setAttribute("role", "dialog");
    target.setAttribute("aria-modal", "true");
  }
  if (target.getAttribute("aria-labelledby")) return;
  const title = target.querySelector("h2, h3");
  if (!title) return;
  if (!title.id) title.id = `${box.id}-titre`;
  target.setAttribute("aria-labelledby", title.id);
}

function opening(box) {
  nameOf(box);
  backs.set(box.id, document.activeElement);
  const targets = focusables(box);
  // Le premier champ plutôt que le premier bouton quand il y en a un : c'est
  // là que la saisie commence.
  const field = targets.find((n) => /^(INPUT|TEXTAREA|SELECT)$/.test(n.tagName));
  (field || targets[0] || box).focus({ preventScroll: true });
}

function closing(box) {
  const back = backs.get(box.id);
  backs.delete(box.id);
  // Rendre le focus à ce qui a ouvert la modale : sans ça, il repart au début
  // du document et la navigation au clavier recommence de zéro.
  if (back && document.contains(back)) back.focus({ preventScroll: true });
}

function installModals() {
  FORMS.forEach(([id]) => {
    const box = $(id);
    if (!box) return;
    nameOf(box);
    // On observe la classe plutôt que d'envelopper chaque ouverture : elles
    // sont écrites dans cinq fichiers, et une seule oubliée suffirait à
    // reperdre le focus.
    new MutationObserver(() => {
      const isOpen = isOpenModal(box);
      if (isOpen && !backs.has(box.id)) opening(box);
      else if (!isOpen && backs.has(box.id)) closing(box);
    }).observe(box, { attributes: true, attributeFilter: ["class"] });
  });

  document.addEventListener("keydown", (e) => {
    const stack = openOnes();
    if (!stack.length) return;
    const above = stack[stack.length - 1];

    if (e.key === "Escape") {
      // Une seule liste, donc plus d'oubli possible : `closeForm` sait aussi
      // remettre à zéro ce que la modale portait.
      closeForm(above.id);
      e.preventDefault();
      return;
    }
    if (e.key !== "Tab") return;
    // Le piège à focus : sans lui, la tabulation sort de la modale et parcourt
    // un fond qui n'est pas censé être atteignable.
    const targets = focusables(above);
    if (!targets.length) return;
    const first = targets[0];
    const last = targets[targets.length - 1];
    const active = document.activeElement;
    if (!above.contains(active)) {
      (e.shiftKey ? last : first).focus({ preventScroll: true });
      e.preventDefault();
    } else if (e.shiftKey && active === first) {
      last.focus({ preventScroll: true });
      e.preventDefault();
    } else if (!e.shiftKey && active === last) {
      first.focus({ preventScroll: true });
      e.preventDefault();
    }
  });
}

export { installModals };
