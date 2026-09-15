/* L'onglet Sortie d'une recipe : matérialisation, incrémental, tags.
 *
 * Trois cents des deux mille trois cents lignes de `recipe.js`, qui ne
 * partageaient rien avec le script ni avec l'aperçu : ce que dbt doit écrire,
 * et comment. C'est la configuration que le rapport de revue nommait à côté
 * des formulaires, de l'aperçu et de l'enregistrement.
 *
 * Le module ne dépend que de feuilles — `core.js`, `state.js`, `widgets.js` —
 * et n'importe rien de l'éditeur de recipe.
 */
import { $, el, esc } from "./core.js";
import { BATCH_HELP, MAT_HELP, OSC_HELP, STRAT_HELP, state } from "./state.js";
import { valuesEditor } from "./widgets.js";


/* Ce que cet onglet doit dire à l'éditeur de recipe quand on y touche :
   « c'est modifié », « refais l'aperçu », « les deux plus le script ».

   `recipe.js` les fournit dans son `brancher()` plutôt que ce module ne les
   importe. Un import les ferait revenir dans le maillage que le découpage
   défait : `output.js` ne dépendrait plus seulement de feuilles, les deux
   fichiers redeviendraient indissociables, et on n'aurait fait que déplacer
   les lignes. Le sens du lien est celui-ci, et un seul : l'éditeur de recipe
   connaît son onglet Sortie, l'onglet Sortie ne connaît pas son éditeur. */
function missing(name) {
  throw new Error(`output.js : « ${name} » n'a pas été installé par recipe.js.`);
}

/* Personne ne peut avoir ouvert l'onglet avant `installOutput` : il ne
   s'affiche qu'à l'ouverture d'une recipe, bien après les branchements. Ces
   trois fonctions ne sont là que pour qu'un oubli se voie tout de suite. */
let signals = {
  edited: () => missing("edited"),
  refreshPreview: () => missing("refreshPreview"),
  touch: () => missing("touch"),
};

function installOutput(provided) {
  signals = provided;
}


function outColumns() {
  const pv = state.rec && state.rec.preview;
  if (!pv) return [];
  // L'onglet Sortie décrit le modèle entier — sa clé incrémentale, ses tests,
  // sa documentation —, pas ce que l'œil montre. Quand l'aperçu s'arrête au
  // milieu, ses colonnes ne sont pas celles que le script produit.
  const deltas = pv.deltas || [];
  const last = deltas[deltas.length - 1];
  if (last && last.applied === false) return last.after || [];
  return pv.columns.map((c) => c.name);
}


function renderOutputPane() {
  const rec = state.rec;
  if (!rec) return;
  const out = (rec.spec.output = rec.spec.output || {});
  const body = $("output-body");
  const cols = outColumns();
  body.innerHTML = "";

  const field = (label, node) => {
    const f = el("div", "fld");
    f.appendChild(el("label", null, esc(label)));
    f.appendChild(node);
    body.appendChild(f);
    return f;
  };

  // --- description
  const desc = el("input", "inp txt");
  desc.value = out.description || "";
  desc.placeholder = "À quoi sert ce dataset ?";
  desc.addEventListener("change", () => { out.description = desc.value; signals.touch(); });
  field("Description", desc);

  // --- matérialisation
  const mat = el("select", "inp");
  mat.innerHTML = state.caps.materializations
    .map((m) => `<option value="${esc(m)}"${(out.materialized || "view") === m ? " selected" : ""}>${esc(m)}</option>`)
    .join("");
  mat.addEventListener("change", () => {
    out.materialized = mat.value;
    signals.touch(); renderOutputPane(); signals.refreshPreview();
  });
  field("Matérialisation", mat);
  body.appendChild(el("p", "hint", esc(MAT_HELP[out.materialized || "view"])));

  if ((out.materialized || "view") !== "incremental") {
    renderTagsField(body, out);
    return;
  }

  // Ce que le panneau affiche est ce que le script contiendra : les défauts
  // sont écrits dans la spec, pas seulement dessinés. Le défaut vient du
  // serveur, qui le choisit selon l'entrepôt : `delete+insert` n'existe pas
  // sur BigQuery, et l'écrire ici produisait un modèle que dbt refuse.
  const defaultStrategy = state.caps.default_incremental_strategy || "delete+insert";
  out.incremental_strategy = out.incremental_strategy || defaultStrategy;
  out.on_schema_change = out.on_schema_change || "append_new_columns";
  out.incremental = out.incremental || { column: "", operator: "gt" };
  out.unique_key = out.unique_key || [];

  const hint = (html) => body.appendChild(el("p", "hint", html));
  const warn = (html) => body.appendChild(el("p", "warnbox", html));

  // --- stratégie
  const strat = el("select", "inp");
  strat.innerHTML = state.caps.incremental_strategies
    .map((x) => `<option value="${esc(x)}"${out.incremental_strategy === x ? " selected" : ""}>${esc(x)}</option>`)
    .join("");
  // Une stratégie enregistrée que cet entrepôt ne connaît pas doit rester
  // visible plutôt que d'être remplacée en silence : c'est un refus à lire,
  // pas un réglage à corriger sans le dire.
  if (!(state.caps.incremental_strategies || []).includes(out.incremental_strategy)) {
    strat.innerHTML += `<option value="${esc(out.incremental_strategy)}" selected>`
      + `${esc(out.incremental_strategy)} — indisponible sur cet entrepôt</option>`;
  }
  strat.addEventListener("change", () => {
    out.incremental_strategy = strat.value; signals.touch(); renderOutputPane(); signals.refreshPreview();
  });
  field("Stratégie", strat);
  hint(esc(STRAT_HELP[out.incremental_strategy] || ""));
  if (!(state.caps.incremental_strategies || []).includes(out.incremental_strategy))
    warn(`dbt ne sait pas exécuter « ${esc(out.incremental_strategy)} » sur `
         + `l'entrepôt de ce projet : le build échouerait. Choisissez `
         + `${(state.caps.incremental_strategies || []).map(esc).join(", ")}.`);

  // La règle d'une jointure incrémentale, dite là où on la règle : elle ne se
  // lisait nulle part, et l'écran laissait donc croire que tout l'amont était
  // relu.
  if (rec.spec.type === "join" && (rec.spec.inputs || []).length > 1)
    hint("Sur une jointure, seule la <b>première entrée</b> est bornée : les "
         + "autres ne sont relues que pour les lignes qui passent le filtre. "
         + "Une valeur modifiée d'un côté qui ne bouge pas — un libellé de "
         + "dimension — ne remontera qu'au prochain passage de sa ligne "
         + "pilote. Si vos dimensions changent, passez la sortie en "
         + "« table ».");

  // Microbatch ne se règle pas comme les autres : c'est dbt qui borne, tranche
  // par tranche, et il exige trois réglages que le panneau ne posait pas.
  if (out.incremental_strategy === "microbatch") microbatchFields(body, field, hint, warn, out, cols);
  else if (rec.spec.type === "sql") {
    // Une recipe SQL est rendue telle quelle : l'atelier n'a nulle part où
    // poser un `where`. La colonne de repère et la fenêtre de reprise
    // s'affichaient pourtant ici, avec la phrase « chaque exécution ne lira
    // que les lignes dont … dépasse le maximum déjà présent » — et rien de
    // tout cela n'arrivait dans le fichier. En « append », la table entière
    // était réinsérée à chaque build. Le compilateur refuse maintenant ces
    // réglages ; l'écran dit ce qu'il faut écrire à la main.
    delete out.incremental;
    const boundColumn = (cols && cols[0]) || "ts";
    hint("Sur une recipe <b>SQL</b>, la borne incrémentale s'écrit dans votre "
         + "requête : l'atelier ne pose pas de filtre dans un SQL qu'il ne "
         + "compose pas.");
    body.appendChild(el("pre", "code",
      `{% if is_incremental() %}\nwhere ${esc(boundColumn)} > (select max(${esc(boundColumn)}) from {{ this }})\n{% endif %}`));
  } else classicIncrementalFields(body, field, hint, warn, out, cols);

  // --- on_schema_change
  const osc = el("select", "inp");
  osc.innerHTML = state.caps.on_schema_change
    .map((x) => `<option value="${esc(x)}"${(out.on_schema_change || "append_new_columns") === x ? " selected" : ""}>${esc(x)}</option>`)
    .join("");
  osc.addEventListener("change", () => { out.on_schema_change = osc.value; signals.touch(); renderOutputPane(); });
  field("Si le schéma change", osc);
  hint(esc(OSC_HELP[out.on_schema_change || "append_new_columns"]));

  renderTagsField(body, out);
}

/* Les réglages que dbt exige d'un modèle microbatch. Sans eux, il refuse de
   parser le projet : l'atelier proposait la stratégie et écrivait un modèle
   qui ne compilait pas. */
function microbatchFields(body, field, hint, warn, out, cols) {
  const mb = (out.microbatch = out.microbatch ||
    { event_time: "", begin: "", batch_size: "day", lookback: "" });

  const when = el("select", "inp");
  when.innerHTML = `<option value="">— à choisir —</option>` + cols
    .map((c) => `<option value="${esc(c)}"${mb.event_time === c ? " selected" : ""}>${esc(c)}</option>`)
    .join("");
  when.addEventListener("change", () => {
    mb.event_time = when.value; signals.touch(); renderOutputPane(); signals.refreshPreview();
  });
  field("Colonne de temps", when);
  if (!cols.length) hint("Lancez l'aperçu pour voir les colonnes.");

  if (!mb.event_time) {
    warn(`Sans colonne de temps, dbt <b>refuse de parser le projet</b>. C'est
          elle qui découpe le travail en tranches, et c'est elle que dbt réécrit
          tranche par tranche. L'enregistrement sera refusé.`);
  } else {
    hint(`Chaque exécution traite une tranche à la fois et réécrit dans la table
          tout ce qui tombe dans sa fenêtre. Rien n'est ajouté à la main dans le
          SQL&nbsp;: c'est la matérialisation de dbt qui filtre.`);
  }

  const begin = el("input", "inp");
  begin.type = "date";
  begin.value = String(mb.begin || "").slice(0, 10);
  begin.addEventListener("change", () => { mb.begin = begin.value; signals.touch(); renderOutputPane(); });
  field("Construire depuis le", begin);
  if (!mb.begin) {
    warn(`Sans date de départ, dbt <b>refuse de parser le projet</b> : il ne sait
          pas où commence l'historique à rattraper.`);
  }

  const size = el("select", "inp");
  size.innerHTML = (state.caps.batch_sizes || [])
    .map((x) => `<option value="${esc(x)}"${(mb.batch_size || "day") === x ? " selected" : ""}>${esc(x)}</option>`)
    .join("");
  size.addEventListener("change", () => { mb.batch_size = size.value; signals.touch(); renderOutputPane(); });
  field("Taille de tranche", size);
  hint(esc(BATCH_HELP[mb.batch_size || "day"] || ""));

  const look = el("input", "inp");
  look.type = "number";
  look.min = "0";
  look.placeholder = "1";
  look.value = mb.lookback == null || mb.lookback === "" ? "" : mb.lookback;
  look.addEventListener("change", () => {
    mb.lookback = look.value.trim() === "" ? "" : Math.max(0, parseInt(look.value, 10) || 0);
    signals.touch(); renderOutputPane();
  });
  field("Tranches à recalculer", look);
  hint(`Combien de tranches déjà construites chaque exécution recalcule en plus
        de la dernière, pour rattraper ce qui arrive en retard. Vide = le défaut
        de dbt, une tranche.`);

  // dbt n'en dit rien d'autre qu'une ligne de log : une entrée sans colonne de
  // temps est relue *en entier* pour chaque tranche.
  (((state.rec && state.rec.preview) || {}).warnings || []).forEach((w) => warn(esc(w)));
}

/* append / delete+insert / merge : c'est le modèle qui porte sa borne, dans un
   bloc `is_incremental()`. */
function classicIncrementalFields(body, field, hint, warn, out, cols) {
  const keys = new Set(out.unique_key || []);
  const chips = el("div", "chips");
  if (!cols.length) {
    chips.appendChild(el("span", null, '<span style="color:var(--faint);font-size:11.5px">Lancez l\'aperçu pour voir les colonnes.</span>'));
  }
  cols.forEach((c) => {
    const lab = el("label");
    lab.innerHTML = `<input type="checkbox"${keys.has(c) ? " checked" : ""}> ${esc(c)}`;
    lab.querySelector("input").addEventListener("change", (e) => {
      if (e.target.checked) keys.add(c); else keys.delete(c);
      out.unique_key = [...keys];
      signals.touch(); renderOutputPane();
    });
    chips.appendChild(lab);
  });
  field("Clé unique", chips);

  const needsKey = state.caps.strategies_needing_key.includes(out.incremental_strategy || "delete+insert");
  if (needsKey && !keys.size) {
    warn(`La stratégie <b>${esc(out.incremental_strategy)}</b> remplace des lignes existantes : sans clé unique, dbt ne sait pas lesquelles. L'enregistrement sera refusé.`);
  }

  // --- colonne de repère
  const inc = (out.incremental = out.incremental || { column: "", operator: "gt" });
  const pick = el("select", "inp");
  pick.innerHTML = `<option value="">— aucune —</option>` + cols
    .map((c) => `<option value="${esc(c)}"${inc.column === c ? " selected" : ""}>${esc(c)}</option>`)
    .join("");
  pick.addEventListener("change", () => { inc.column = pick.value; signals.touch(); renderOutputPane(); signals.refreshPreview(); });
  field("Colonne de repère", pick);

  const op = el("select", "inp");
  op.innerHTML = [["gt", "strictement postérieur ( > )"], ["gte", "à partir de ( ≥ )"]]
    .map(([v, l]) => `<option value="${v}"${(inc.operator || "gt") === v ? " selected" : ""}>${esc(l)}</option>`)
    .join("");
  op.addEventListener("change", () => { inc.operator = op.value; signals.touch(); renderOutputPane(); signals.refreshPreview(); });
  field("Comparaison", op);

  if (!inc.column) {
    warn("Sans colonne de repère, chaque exécution relira <b>tout</b> l'historique de la source : l'incrémental ne fera gagner que l'écriture, pas la lecture.");
    return;
  }
  hint(`Chaque exécution ne lira que les lignes dont <code>${esc(inc.column)}</code> dépasse le maximum déjà présent dans la table.`);

  // --- fenêtre de reprise
  const look = (inc.lookback = inc.lookback || { n: "", unit: "day" });
  const row = el("div", "rowline");
  row.innerHTML =
    `<input class="inp" type="number" min="0" placeholder="0" data-lb-n
            value="${esc(look.n == null || look.n === "" ? "" : look.n)}">
     <select class="inp" data-lb-u>${(state.caps.lookback_units || ["hour", "day"])
       .map((u) => `<option value="${esc(u)}"${(look.unit || "day") === u ? " selected" : ""}>${u === "hour" ? "heures" : "jours"}</option>`)
       .join("")}</select>`;
  row.querySelector("[data-lb-n]").addEventListener("change", (e) => {
    const v = e.target.value.trim();
    look.n = v === "" ? "" : Math.max(0, parseInt(v, 10) || 0);
    signals.touch(); renderOutputPane(); signals.refreshPreview();
  });
  row.querySelector("[data-lb-u]").addEventListener("change", (e) => {
    look.unit = e.target.value; signals.touch(); renderOutputPane(); signals.refreshPreview();
  });
  field("Fenêtre de reprise", row);

  const n = parseInt(look.n, 10) || 0;
  const unit = (look.unit || "day") === "hour" ? "heure" : "jour";
  if (!n) {
    hint(`Une ligne arrivée en retard porte une date <i>antérieure</i> au maximum
          déjà écrit : la borne stricte la laisse dehors <b>définitivement</b>, et
          aucun build n'échoue pour le dire. Reculer la borne de quelques ${unit}s
          la rattrape au passage suivant.`);
  } else if ((out.incremental_strategy || "delete+insert") === "append") {
    warn(`Une fenêtre de reprise ne va pas avec <b>append</b> : les lignes relues
          seraient réinsérées à côté de celles déjà écrites, sans rien remplacer.
          L'enregistrement sera refusé.`);
  } else {
    hint(`Chaque exécution relira tout ce qui dépasse
          <code>max(${esc(inc.column)})</code> moins ${n} ${unit}${n > 1 ? "s" : ""}.
          Les lignes relues remplacent celles déjà écrites, par la clé unique.`);
  }
}


function renderTagsField(body, out) {
  const f = el("div", "fld");
  f.appendChild(el("label", null, "Tags"));
  // Un tag par champ, pour la même raison que les valeurs d'un filtre : un
  // tag qui contient une virgule — « chiffre d'affaires, net » — ne se
  // représentait pas, et rééditer une configuration qui en portait un la
  // coupait en deux sans rien dire.
  out.tags = Array.isArray(out.tags) ? out.tags : [];
  f.appendChild(
    valuesEditor(out, "tags", { addRow: "+ Ajouter un tag", onChange: signals.edited }),
  );
  body.appendChild(f);
}

export { installOutput, renderOutputPane };
