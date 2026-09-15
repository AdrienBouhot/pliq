/* Les petits éditeurs réutilisables, qui ne connaissent que le DOM.
 *
 * Ce module est une feuille : il n'importe que `core.js` et n'appelle
 * personne en retour. C'est ce qui permet à l'éditeur de recipe et à la
 * fiche d'un dataset de partager une liste de valeurs sans se référencer
 * l'un l'autre.
 */
import { el } from "./core.js";


function valuesEditor(params, key, opts = {}) {
  const box = el("div", "values-list");
  const read = () => (Array.isArray(params[key]) ? params[key] : []);
  // Le widget ne sait pas ce qu'est une recipe : il dit qu'on a changé
  // quelque chose, et l'appelant décide de ce que cela veut dire. Le défaut
  // était `edited()`, et c'est ce seul mot qui retenait la liste de valeurs
  // dans l'éditeur de recipe — la fiche de tests d'un dataset existant, qui
  // n'a pas de recipe, devait déjà passer outre.
  if (typeof opts.onChange !== "function") {
    throw new TypeError("valuesEditor : `onChange` est requis.");
  }
  const change = opts.onChange;

  const render = () => {
    box.innerHTML = "";
    read().forEach((val, i) => {
      const row = el("div", "values-row");
      const inp = el("input", "inp");
      inp.value = val == null ? "" : String(val);
      inp.addEventListener("change", () => {
        const list = read().slice();
        list[i] = inp.value;
        params[key] = list;
        change();
      });
      const minus = el("button", "btn ghost sm");
      minus.type = "button";
      minus.textContent = "−";
      minus.title = "Retirer cette valeur";
      minus.addEventListener("click", () => {
        params[key] = read().filter((_, n) => n !== i);
        render();
        change();
      });
      row.appendChild(inp);
      row.appendChild(minus);
      box.appendChild(row);
    });
    const plus = el("button", "btn ghost sm");
    plus.type = "button";
    plus.textContent = opts.addRow || "+ Ajouter une valeur";
    plus.addEventListener("click", () => {
      params[key] = read().concat([""]);
      render();
      const fields = box.querySelectorAll("input");
      if (fields.length) fields[fields.length - 1].focus();
    });
    box.appendChild(plus);
  };

  render();
  return box;
}

export { valuesEditor };
