/* La fiche d'un dataset : ses tests, sa documentation, sa fraîcheur.
 *
 * Elle vivait dans `recipe.js`, qui en faisait deux cent quatre-vingts de ses
 * deux mille trois cents lignes sans rien partager avec le reste : l'onglet
 * Tests d'une recipe et la fiche d'un modèle existant affichent le même
 * éditeur, et c'est `tests.js` qui l'ouvre dans les deux cas.
 *
 * Elle ne dépend que de `core.js`, `state.js` et `widgets.js`, tous trois
 * feuilles : aucun import ne revient vers l'éditeur de recipe. Le `onChange`
 * que chaque fonction reçoit est ce qui remplace ce retour — c'est l'appelant
 * qui sait s'il faut marquer une recipe modifiée ou enregistrer une fiche.
 */
import { $, el, esc } from "./core.js";
import { SEVERITIES, state } from "./state.js";
import { valuesEditor } from "./widgets.js";


const FLAG_TESTS = [
  ["not_null", "not_null", "Refuse les valeurs vides"],
  ["unique", "unique", "Refuse les doublons"],
];


function hasTest(col, name) { return (col.tests || []).some((t) => t.name === name); }


function toggleTest(col, name) {
  col.tests = col.tests || [];
  const i = col.tests.findIndex((t) => t.name === name);
  if (i >= 0) col.tests.splice(i, 1);
  else col.tests.push({ name, severity: "error" });
}


function countTests(model) {
  return (model.columns || []).reduce((n, c) => n + (c.tests || []).length, 0);
}


function modelRefs() {
  return state.refs.filter((r) => r.kind === "ref").map((r) => r.ref);
}


function severitySelect(test, onChange) {
  const sel = el("select", "inp");
  sel.title = "Sévérité : bloquant fait échouer le run, avertissement le signale seulement";
  sel.innerHTML = SEVERITIES
    .map(([v, l]) => `<option value="${v}"${(test.severity || "error") === v ? " selected" : ""}>${esc(l)}</option>`)
    .join("");
  sel.addEventListener("change", () => { test.severity = sel.value; onChange(); });
  return sel;
}


function testColumnCard(col, onChange) {
  const card = el("div", `tcol${col.missing ? " missing" : ""}`);
  const head = el("div", "tcol-h");
  head.innerHTML =
    `<span class="cn">${esc(col.name)}</span>` +
    `<span class="ct">${esc(col.type || "—")}</span>` +
    (col.missing ? `<span class="miss">absente de l'entrepôt</span>` : "");
  card.appendChild(head);

  // --- bascules simples
  const flags = el("div", "tflags");
  FLAG_TESTS.forEach(([name, label, title]) => {
    const b = el("button", `tflag${hasTest(col, name) ? " on" : ""}`, esc(label));
    b.type = "button";
    b.title = title;
    b.addEventListener("click", () => { toggleTest(col, name); onChange(); });
    flags.appendChild(b);
  });

  const addValues = el("button", "tflag add", "+ valeurs autorisées");
  addValues.type = "button";
  addValues.disabled = hasTest(col, "accepted_values");
  addValues.addEventListener("click", () => {
    (col.tests = col.tests || []).push({ name: "accepted_values", values: [], severity: "error" });
    onChange();
  });
  flags.appendChild(addValues);

  const addRel = el("button", "tflag add", "+ relation");
  addRel.type = "button";
  addRel.addEventListener("click", () => {
    (col.tests = col.tests || []).push({
      name: "relationships", to: modelRefs()[0] || "", field: "id", severity: "error",
    });
    onChange();
  });
  flags.appendChild(addRel);
  card.appendChild(flags);

  // --- tests paramétrés
  (col.tests || []).forEach((t, i) => {
    if (t.name === "accepted_values") {
      const row = el("div", "trow");
      row.appendChild(el("span", "tn", "accepted_values"));
      // Une valeur par champ : une valeur autorisée qui contient une virgule
      // ou des espaces significatifs ne pouvait ni se saisir ni se relire.
      t.values = Array.isArray(t.values) ? t.values : [];
      const list = valuesEditor(t, "values", {
        addRow: "+ Ajouter une valeur", onChange,
      });
      if (!t.values.length) list.classList.add("bad");
      row.appendChild(list);
      row.appendChild(severitySelect(t, onChange));
      row.appendChild(removeBtn(col, i, onChange));
      card.appendChild(row);
    } else if (t.name === "relationships") {
      const row = el("div", "trow");
      row.appendChild(el("span", "tn", "→"));
      if (t.to_expr) {
        // La relation pointe ailleurs que vers un modèle du projet : une
        // source, un paquet, une version. On montre l'expression telle qu'elle
        // est écrite plutôt que de la remplacer par un modèle au hasard — la
        // réécrire est le seul moyen de ne pas casser le test.
        const expr = el("span", "inp ro", esc(t.to_expr));
        expr.title = "Écrite à la main dans le YAML : conservée telle quelle.";
        row.appendChild(expr);
      } else {
        const sel = el("select", "inp");
        sel.innerHTML = modelRefs()
          .map((m) => `<option value="${esc(m)}"${t.to === m ? " selected" : ""}>${esc(m)}</option>`)
          .join("");
        // Un `<select>` affiche sa première option quand aucune ne correspond :
        // sans ça, le test partirait vers un modèle que personne n'a choisi.
        if (!t.to) { t.to = sel.value; }
        sel.addEventListener("change", () => { t.to = sel.value; onChange(); });
        row.appendChild(sel);
      }
      const fld = el("input", "inp");
      fld.value = t.field || "id";
      fld.placeholder = "colonne cible";
      fld.spellcheck = false;
      fld.addEventListener("change", () => { t.field = fld.value.trim(); onChange(); });
      row.appendChild(fld);
      row.appendChild(severitySelect(t, onChange));
      row.appendChild(removeBtn(col, i, onChange));
      card.appendChild(row);
    } else if (!FLAG_TESTS.some(([n]) => n === t.name)) {
      const row = el("div", "trow");
      row.appendChild(el("span", "tn", esc(t.name)));
      row.appendChild(el("span", null, '<span style="font-size:11.5px;color:var(--muted)">test générique, non modifiable ici</span>'));
      row.appendChild(removeBtn(col, i, onChange));
      card.appendChild(row);
    } else if ((t.severity || "error") !== "error") {
      const row = el("div", "trow");
      row.appendChild(el("span", "tn", esc(t.name)));
      row.appendChild(severitySelect(t, onChange));
      card.appendChild(row);
    }
  });

  // --- description
  const d = el("input", "inp txt desc-in");
  d.value = col.description || "";
  d.placeholder = "Description de la colonne (va dans dbt docs)";
  d.addEventListener("change", () => { col.description = d.value; onChange(); });
  card.appendChild(d);

  return card;
}


function removeBtn(col, index, onChange) {
  const b = el("button", "xbtn", "×");
  b.type = "button";
  b.title = "Retirer ce test";
  b.addEventListener("click", () => { col.tests.splice(index, 1); onChange(); });
  return b;
}


const KIND_LABEL = { model: "modèle", seed: "seed", source: "table source",
                     snapshot: "snapshot" };


function renderTestsEditor(container, model, onChange, opts = {}) {
  container.innerHTML = "";
  if (opts.intro) container.appendChild(el("p", "hint", opts.intro));

  // --- la table elle-même
  const card = el("div", "tcol doc");
  card.appendChild(el("div", "tcol-h",
    `<span class="cn">${esc(model.model || "ce dataset")}</span>
     <span class="ct">${esc(KIND_LABEL[model.kind] || "dataset")}</span>`));

  const f = el("div", "fld");
  f.appendChild(el("label", null, "Description"));
  const di = el("textarea", "inp txt doc-ta");
  di.value = model.description || "";
  di.rows = 2;
  di.placeholder = "À quoi sert ce dataset ? D'où viennent les données ?";
  di.addEventListener("change", () => { model.description = di.value; onChange(); });
  f.appendChild(di);
  f.appendChild(el("p", "hint",
    "Ressort dans <code>dbt docs</code> et dans le catalogue."));
  card.appendChild(f);

  if (opts.tags !== false) {
    const tf = el("div", "fld");
    tf.appendChild(el("label", null, "Tags"));
    // Un tag par champ, comme partout ailleurs : la virgule n'est pas un
    // séparateur de tags dbt, c'est un caractère qu'un tag peut porter.
    model.tags = Array.isArray(model.tags) ? model.tags : [];
    tf.appendChild(valuesEditor(model, "tags", {
      addRow: "+ Ajouter un tag", onChange,
    }));
    tf.appendChild(el("p", "hint",
      "Sélecteur dbt : <code>dbt build --select tag:finance</code>."));
    card.appendChild(tf);
  }
  container.appendChild(card);

  if (model.kind === "source") renderFreshnessFields(container, model, onChange);

  if (!model.columns || !model.columns.length) {
    container.appendChild(el("div", "empty",
      "<b>Colonnes inconnues</b>Construisez le dataset une première fois pour que l'atelier puisse les lister. La description ci-dessus est déjà enregistrable."));
    return;
  }

  container.appendChild(el("div", "sec-h", `Colonnes (${model.columns.length})`));
  model.columns.forEach((col) => container.appendChild(testColumnCard(col, onChange)));
}

/* La fraîcheur d'une source se règle ici, parce que c'est ici qu'on écrit le
   `sources.yml`. Sans `loaded_at_field`, `dbt source freshness` saute la table
   — elle n'est alors pas « à jour », elle n'est pas mesurée, et le Flow ne
   montre rien. Les deux réglages vont donc ensemble. */


function renderFreshnessFields(container, model, onChange) {
  const fr = (model.freshness = model.freshness || {});
  const periods = model.freshness_periods || ["minute", "hour", "day"];
  const cols = (model.columns || []).map((c) => c.name);

  const card = el("div", "tcol doc");
  card.appendChild(el("div", "tcol-h",
    `<span class="cn">Fraîcheur</span><span class="ct">dbt source freshness</span>`));

  const f = el("div", "fld");
  f.appendChild(el("label", null, "Colonne de chargement"));
  // Champ libre et non liste déroulante : dbt attend un *horodatage*, et
  // beaucoup de tables n'ont qu'une date. Il accepte une expression pour ça —
  // `cast(order_date as timestamp)` — et c'est souvent la seule réponse.
  const listId = "fresh-cols";
  const inp = el("input", "inp");
  inp.value = fr.loaded_at_field || "";
  inp.spellcheck = false;
  inp.placeholder = "ingested_at, ou cast(order_date as timestamp)";
  inp.setAttribute("list", listId);
  inp.addEventListener("change", () => {
    fr.loaded_at_field = inp.value.trim();
    onChange();
  });
  f.appendChild(inp);
  const list = el("datalist");
  list.id = listId;
  list.innerHTML = cols.map((c) => `<option value="${esc(c)}"></option>`).join("");
  f.appendChild(list);
  f.appendChild(el("p", "hint",
    "La date d'arrivée des lignes — <code>ingested_at</code>, <code>_loaded_at</code>… " +
    "Sans elle, dbt saute cette source, et sa fraîcheur reste <i>inconnue</i> plutôt " +
    "que bonne. dbt veut un horodatage : sur une simple date, écrivez " +
    "<code>cast(la_colonne as timestamp)</code>."));
  card.appendChild(f);

  [["warn_after", "Avertir au-delà de"], ["error_after", "Échouer au-delà de"]]
    .forEach(([key, label]) => {
      const threshold = fr[key] || {};
      const row = el("div", "fld");
      row.appendChild(el("label", null, label));
      const line = el("div", "rowline");
      line.innerHTML =
        `<input class="inp" type="number" min="1" placeholder="—" data-n
                value="${esc(threshold.count == null ? "" : threshold.count)}">
         <select class="inp" data-p>${periods.map((u) =>
           `<option value="${esc(u)}"${(threshold.period || "hour") === u ? " selected" : ""}>${
             u === "minute" ? "minutes" : u === "day" ? "jours" : "heures"}</option>`).join("")}</select>`;
      const reread = () => {
        const n = parseInt(line.querySelector("[data-n]").value, 10);
        if (!n || n < 1) delete fr[key];
        else fr[key] = { count: n, period: line.querySelector("[data-p]").value };
        onChange();
      };
      line.querySelector("[data-n]").addEventListener("change", reread);
      line.querySelector("[data-p]").addEventListener("change", reread);
      row.appendChild(line);
      card.appendChild(row);
    });

  card.appendChild(el("p", "hint",
    "Vide = pas de seuil. L'état apparaît sur le nœud du Flow après un " +
    "<code>source freshness</code>."));
  container.appendChild(card);
}

export { countTests, renderTestsEditor };
