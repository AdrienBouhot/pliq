/* Les éditeurs des recipes qui ne sont pas des scripts d'étapes. */
import { $, el, esc, svg } from "./core.js";
import { state } from "./state.js";
import { post } from "./api.js";
import { edited, renderScript } from "./recipe.js";
import { aliasOf, colsOf } from "./canvas.js";


/* --------------------------------------------------------------- fabriques */

function fieldRow(label, node, hint) {
  const f = el("div", "fld");
  if (label) f.appendChild(el("label", null, esc(label)));
  f.appendChild(node);
  if (hint) f.appendChild(el("p", "hint", hint));
  return f;
}


function colSelect(alias, value, onChange, extra) {
  const sel = el("select", "inp");
  const opts = [`<option value="">— colonne —</option>`]
    .concat((extra || []).map((e) => `<option value="${esc(e)}"${e === value ? " selected" : ""}>${esc(e)}</option>`))
    .concat(colsOf(alias).map((c) =>
      `<option value="${esc(c.name)}"${c.name === value ? " selected" : ""}>${esc(c.name)}</option>`));
  sel.innerHTML = opts.join("");
  sel.value = value || "";
  sel.addEventListener("change", () => onChange(sel.value));
  return sel;
}


function iconBtn(icon, title, onClick) {
  const b = el("button", "xbtn", svg(icon, 13));
  b.type = "button";
  b.title = title;
  b.addEventListener("click", onClick);
  return b;
}

/* ------------------------------------------------------------------ JOIN */


/* ------------------------------------------------------------------ JOIN */

const JOIN_TYPES = [
  ["inner", "inner — seulement les lignes appariées des deux côtés"],
  ["left", "left — toutes les lignes de gauche, complétées si possible"],
  ["right", "right — toutes les lignes de droite"],
  ["full", "full — tout des deux côtés"],
  ["cross", "cross — toutes les combinaisons, sans clé"],
];


function guessKey(leftAlias, rightAlias) {
  const left = colsOf(leftAlias).map((c) => c.name);
  const right = colsOf(rightAlias).map((c) => c.name);
  const common = left.filter((n) => right.includes(n));
  if (!common.length) return null;
  const id = common.find((n) => /(^|_)id$/i.test(n)) || common[0];
  return { left: id, right: id };
}


function renderJoinEditor(list) {
  const rec = state.rec, spec = rec.spec;
  const inputs = spec.inputs || [];
  const aliases = inputs.map(aliasOf);
  spec.joins = spec.joins || [];
  while (spec.joins.length < aliases.length - 1)
    spec.joins.push({ type: "left", on: [{ left: "", right: "" }] });
  spec.joins.length = Math.max(0, aliases.length - 1);

  if (rec.colError) {
    list.appendChild(el("div", "warnbox", esc(rec.colError)));
    return;
  }

  list.appendChild(el("div", "jhead",
    `<b>${esc(aliases[0])}</b><span>dataset principal — ses lignes commandent</span>`));

  aliases.slice(1).forEach((alias, n) => {
    const cfg = spec.joins[n];
    const card = el("div", "jcard");
    card.appendChild(el("div", "jcard-h",
      `<span class="ci">${svg("join", 14)}</span>
       <b>${esc(aliases[0])}</b> <i>avec</i> <b>${esc(alias)}</b>`));

    const sel = el("select", "inp");
    sel.innerHTML = JOIN_TYPES.map(([k, label]) =>
      `<option value="${k}"${cfg.type === k ? " selected" : ""}>${esc(label)}</option>`).join("");
    sel.addEventListener("change", () => {
      cfg.type = sel.value;
      edited();
    });
    card.appendChild(fieldRow("Type de jointure", sel));

    if (cfg.type !== "cross") {
      // Clé devinée au premier affichage : deux colonnes du même nom, c'est
      // presque toujours la bonne. Signalé, et modifiable.
      rec.guessed = rec.guessed || {};
      const empty = (cfg.on || []).length === 1 && !cfg.on[0].left && !cfg.on[0].right;
      if (empty) {
        const g = guessKey(aliases[0], alias);
        if (g) { cfg.on = [g]; rec.guessed[n] = true; }
      }
      const guessed = !!rec.guessed[n];

      const keys = el("div", "jkeys");
      (cfg.on || []).forEach((k, i) => {
        const row = el("div", "jrow");
        row.appendChild(colSelect(aliases[0], k.left, (v) => { k.left = v; edited(); }));
        row.appendChild(el("span", "eq", "="));
        row.appendChild(colSelect(alias, k.right, (v) => { k.right = v; edited(); }));
        row.appendChild(iconBtn("trash", "Retirer cette clé", () => {
          cfg.on.splice(i, 1);
          if (!cfg.on.length) cfg.on.push({ left: "", right: "" });
          edited();
        }));
        keys.appendChild(row);
      });
      card.appendChild(fieldRow("Clés de rapprochement", keys,
        guessed ? "Clé devinée d'après les noms de colonnes — vérifiez-la."
                : "Toutes les clés doivent être vraies en même temps : <code>and</code>."));

      const add = el("button", "btn ghost sm", "+ Ajouter une clé");
      add.type = "button";
      add.addEventListener("click", () => {
        cfg.on.push({ left: "", right: "" });
        edited();
      });
      card.appendChild(add);
    } else {
      card.appendChild(el("p", "hint",
        "Une jointure croisée multiplie les lignes : <b>" +
        "toutes les combinaisons</b> des deux datasets. Sans clé."));
    }
    list.appendChild(card);
  });

  renderJoinDiagnostic(list);
  renderJoinColumns(list, aliases);
}

/* ---------------------------------------------------- diagnostic de jointure

   Un `dbt build` ne dira jamais qu'une jointure vient de tripler le chiffre
   d'affaires : le SQL est valide, les colonnes existent, rien n'échoue. Les
   contrôles de noms attrapent ce que dbt aurait refusé ; celui-ci attrape ce
   qu'il aurait accepté.

   À la demande, et pas à chaque frappe : chaque mesure est un agrégat sur la
   table entière. C'est ce qui le rend fiable — un contrôle d'unicité sur deux
   cents lignes d'échantillon n'est pas un contrôle d'unicité — et c'est ce qui
   le rend cher. */


const DIAG_LABEL = { ok: "ok", warn: "attention", bad: "problème" };


function renderJoinDiagnostic(list) {
  const rec = state.rec;
  const box = el("div", "jcard diag");
  const head = el("div", "jcard-h");
  head.innerHTML = `<b>Ce que la jointure fait aux lignes</b>`;
  box.appendChild(head);

  const btn = el("button", "btn sm", rec.diagBusy ? "Contrôle en cours…" : "Vérifier la jointure");
  btn.type = "button";
  btn.disabled = !!rec.diagBusy;
  btn.addEventListener("click", diagnoseJoin);
  box.appendChild(btn);

  if (rec.diagError) {
    box.appendChild(el("div", "err-box", esc(rec.diagError)));
  } else if (!rec.diag) {
    box.appendChild(el("p", "hint",
      `Unicité des clés, relation 1:1 ou 1:N, lignes sans correspondance,
       multiplication des lignes. Le contrôle porte sur les <b>tables
       entières</b>, pas sur l'échantillon de la grille — il coûte donc une
       requête complète par mesure.`));
  } else {
    if (rec.diagStale)
      box.appendChild(el("p", "warnbox",
        "La jointure a changé depuis ce contrôle. Relancez-le."));
    const ul = el("div", "diag-list");
    rec.diag.forEach((f) => {
      const item = el("div", `diag ${esc(f.level)}`);
      item.innerHTML =
        `<span class="pill ${esc(f.level)}">${esc(DIAG_LABEL[f.level] || f.level)}</span>
         <b>${esc(f.title)}</b><span>${esc(f.detail)}</span>`;
      ul.appendChild(item);
    });
    box.appendChild(ul);
    box.appendChild(el("p", "hint", "Contrôle complet — tables entières."));
  }
  list.appendChild(box);
}


async function diagnoseJoin() {
  const rec = state.rec;
  if (!rec || rec.diagBusy) return;
  rec.diagBusy = true;
  rec.diagError = "";
  // L'empreinte du script *envoyé*, et non l'identité de `rec` : l'objet reste
  // le même quand on modifie la jointure pendant la requête, si bien qu'un
  // diagnostic parti d'une version antérieure remettait `diagStale` à faux et
  // effaçait l'avertissement « la jointure a changé ». Seule sa valeur peut
  // dire s'il décrit encore ce qui est à l'écran.
  const sent = JSON.stringify(rec.spec);
  renderScript();
  try {
    const r = await post("/api/recipe/diagnose", { spec: rec.spec });
    if (state.rec !== rec) return;
    rec.diag = r.findings || [];
    rec.diagStale = JSON.stringify(rec.spec) !== sent;
  } catch (e) {
    if (state.rec === rec) { rec.diagError = e.message; rec.diag = null; }
  } finally {
    if (state.rec === rec) { rec.diagBusy = false; renderScript(); }
  }
}


function joinCollisions(aliases) {
  // Insensible à la casse, comme le compilateur et comme l'entrepôt : `id` et
  // `ID` sont une collision, et l'avertissement ne se déclenchait pas pour
  // elle — le serveur, lui, la refuse désormais.
  const seen = {}, dup = new Set();
  aliases.forEach((a) => colsOf(a).forEach((c) => {
    const key = String(c.name).toLowerCase();
    if (seen[key] && seen[key] !== a) dup.add(c.name);
    seen[key] = a;
  }));
  return dup;
}


function explicitSelection(aliases) {
  // Toutes les colonnes, les doublons suffixés par leur dataset d'origine.
  const used = new Set();
  const out = [];
  aliases.forEach((a) => colsOf(a).forEach((c) => {
    let name = c.name;
    if (used.has(name)) name = `${c.name}_${a}`;
    let i = 2;
    while (used.has(name)) name = `${c.name}_${a}_${i++}`;
    used.add(name);
    out.push({ from: a, column: c.name, as: name });
  }));
  return out;
}


function renderJoinColumns(list, aliases) {
  const rec = state.rec, spec = rec.spec;
  const explicit = (spec.select || []).length > 0;
  const dup = joinCollisions(aliases);

  const box = el("div", "jcard");
  box.appendChild(el("div", "jcard-h", `<b>Colonnes retenues</b>`));

  const modes = el("div", "jmodes");
  [["all", "Toutes les colonnes"], ["pick", "Choisir les colonnes"]].forEach(([k, label]) => {
    const b = el("button", "tflag" + ((k === "pick") === explicit ? " on" : ""), esc(label));
    b.type = "button";
    b.addEventListener("click", () => {
      spec.select = k === "pick" ? explicitSelection(aliases) : [];
      edited();
    });
    modes.appendChild(b);
  });
  box.appendChild(modes);

  if (!explicit) {
    if (dup.size) {
      box.appendChild(el("div", "warnbox",
        `<b>${esc([...dup].join(", "))}</b> existe dans plusieurs datasets. En gardant
         « toutes les colonnes », la table de sortie aurait deux colonnes du même nom
         et <code>dbt build</code> échouerait. Passez à « Choisir les colonnes » :
         l'atelier renommera les doublons.`));
    } else {
      box.appendChild(el("p", "hint",
        "Le SQL produira <code>" + aliases.map((a) => a + ".*").join(", ") +
        "</code>. Une colonne ajoutée en amont suivra toute seule."));
    }
    list.appendChild(box);
    return;
  }

  const kept = new Map((spec.select || []).map((s) => [`${s.from}.${s.column}`, s]));
  aliases.forEach((a) => {
    box.appendChild(el("div", "jsub", esc(a)));
    colsOf(a).forEach((c) => {
      const key = `${a}.${c.name}`;
      const cur = kept.get(key);
      const row = el("label", "jcol" + (cur ? " on" : ""));
      const cb = el("input");
      cb.type = "checkbox";
      cb.checked = !!cur;
      cb.addEventListener("change", () => {
        if (cb.checked) {
          spec.select.push({ from: a, column: c.name, as: c.name });
        } else {
          spec.select = spec.select.filter((s) => !(s.from === a && s.column === c.name));
        }
        edited();
      });
      row.appendChild(cb);
      const cn = el("span", "cn", esc(c.name));
      cn.title = c.name;           // le nom est tronqué si la colonne est étroite
      row.appendChild(cn);
      row.appendChild(el("span", "ct", esc(c.type || "")));
      if (cur) {
        const rn = el("input", "inp sm");
        rn.value = cur.as || c.name;
        rn.spellcheck = false;
        rn.title = "Nom dans le dataset de sortie";
        rn.addEventListener("change", () => { cur.as = rn.value.trim() || c.name; edited(); });
        rn.addEventListener("click", (e) => e.preventDefault());
        row.appendChild(rn);
      }
      box.appendChild(row);
    });
  });

  const names = (spec.select || []).map((s) => s.as || s.column);
  const clash = names.filter((n, i) => names.indexOf(n) !== i);
  if (clash.length) {
    box.appendChild(el("div", "warnbox",
      `Deux colonnes de sortie s'appellent <b>${esc([...new Set(clash)].join(", "))}</b>.
       Renommez-en une.`));
  }
  if (!names.length) {
    box.appendChild(el("div", "warnbox", "Aucune colonne retenue : cochez-en au moins une."));
  }
  list.appendChild(box);
}

/* ----------------------------------------------------------------- GROUP */


/* ----------------------------------------------------------------- GROUP */

const AGG_FNS = [
  ["count", "nombre de lignes"], ["count_distinct", "nombre de valeurs distinctes"],
  ["sum", "somme"], ["avg", "moyenne"], ["min", "minimum"],
  ["max", "maximum"], ["median", "médiane"],
];
const AGG_PREFIX = { count: "nb", count_distinct: "nb_distinct", sum: "total",
                     avg: "moyenne", min: "min", max: "max", median: "mediane" };


function aggAlias(a) {
  const col = a.column && a.column !== "*" ? a.column : "lignes";
  return `${AGG_PREFIX[a.fn] || a.fn}_${col}`.toLowerCase().replace(/[^a-z0-9_]/g, "_");
}


function renderGroupEditor(list) {
  const rec = state.rec, spec = rec.spec;
  const alias = aliasOf((spec.inputs || [])[0] || {}, 0);
  spec.group_by = spec.group_by || [];
  spec.aggregations = spec.aggregations || [];

  if (rec.colError) {
    list.appendChild(el("div", "warnbox", esc(rec.colError)));
    return;
  }

  // --- clés de regroupement
  const gbox = el("div", "jcard");
  gbox.appendChild(el("div", "jcard-h", `<b>Grouper par</b>`));
  const chips = el("div", "chips");
  spec.group_by.forEach((name, i) => {
    const chip = el("span", "chip", esc(name));
    const x = el("button", "cx", "×");
    x.type = "button";
    x.addEventListener("click", () => { spec.group_by.splice(i, 1); edited(); });
    chip.appendChild(x);
    chips.appendChild(chip);
  });
  if (!spec.group_by.length)
    chips.appendChild(el("span", "chip-empty",
      "aucune — une seule ligne en sortie, tous les totaux confondus"));
  gbox.appendChild(chips);

  const add = colSelect(alias, "", (v) => {
    if (v && !spec.group_by.includes(v)) { spec.group_by.push(v); edited(); }
  });
  gbox.appendChild(fieldRow("Ajouter une clé", add,
    "Une ligne en sortie par combinaison de ces colonnes."));
  list.appendChild(gbox);

  // --- mesures
  const abox = el("div", "jcard");
  abox.appendChild(el("div", "jcard-h", `<b>Mesures</b>`));
  if (!spec.aggregations.length)
    abox.appendChild(el("p", "hint", "Une recipe Grouper a besoin d'au moins une mesure."));

  spec.aggregations.forEach((a, i) => {
    const row = el("div", "aggrow");
    const fn = el("select", "inp");
    fn.innerHTML = AGG_FNS.map(([k, label]) =>
      `<option value="${k}"${a.fn === k ? " selected" : ""}>${esc(label)}</option>`).join("");
    fn.addEventListener("change", () => {
      a.fn = fn.value;
      if (a.fn !== "count") a.column = a.column === "*" ? "" : a.column;
      a.alias = aggAlias(a);
      edited();
    });
    row.appendChild(fn);

    row.appendChild(colSelect(alias, a.column, (v) => {
      a.column = v;
      a.alias = aggAlias(a);
      edited();
    // `count(distinct *)` n'est pas du SQL : seul « nombre de lignes » compte
    // sans colonne.
    }, a.fn === "count" ? ["*"] : []));

    const al = el("input", "inp");
    al.value = a.alias || aggAlias(a);
    al.spellcheck = false;
    al.title = "Nom de la mesure en sortie";
    al.addEventListener("change", () => { a.alias = al.value.trim(); edited(); });
    row.appendChild(al);

    row.appendChild(iconBtn("trash", "Retirer la mesure", () => {
      spec.aggregations.splice(i, 1); edited();
    }));
    abox.appendChild(row);

    const flt = el("input", "inp");
    flt.value = a.filter || "";
    flt.placeholder = "compter seulement si… (condition SQL, optionnel)";
    flt.spellcheck = false;
    flt.addEventListener("change", () => { a.filter = flt.value.trim(); edited(); });
    abox.appendChild(el("div", "aggfilter")).appendChild(flt);
  });

  const addAgg = el("button", "btn ghost sm", "+ Ajouter une mesure");
  addAgg.type = "button";
  addAgg.addEventListener("click", () => {
    const a = { fn: "count", column: "*", alias: "" };
    a.alias = aggAlias(a);
    spec.aggregations.push(a);
    edited();
  });
  abox.appendChild(addAgg);
  list.appendChild(abox);
}

/* ----------------------------------------------------------------- STACK */


/* ----------------------------------------------------------------- STACK */

function renderStackEditor(list) {
  const rec = state.rec, spec = rec.spec;
  const aliases = (spec.inputs || []).map(aliasOf);

  const box = el("div", "jcard");
  box.appendChild(el("div", "jcard-h", `<b>Empilement</b>`));
  const sel = el("select", "inp");
  sel.innerHTML = [
    ["all", "garder toutes les lignes (union all)"],
    ["distinct", "dédoublonner les lignes identiques (union)"],
  ].map(([k, label]) =>
    `<option value="${k}"${(spec.mode || "all") === k ? " selected" : ""}>${esc(label)}</option>`).join("");
  sel.addEventListener("change", () => { spec.mode = sel.value; edited(); });
  box.appendChild(fieldRow("Doublons", sel));
  list.appendChild(box);

  if (rec.colError) { list.appendChild(el("div", "warnbox", esc(rec.colError))); return; }

  // Quelle colonne vient d'où : c'est la question qu'on se pose en empilant.
  const all = [];
  aliases.forEach((a) => colsOf(a).forEach((c) => { if (!all.includes(c.name)) all.push(c.name); }));

  const grid = el("div", "jcard");
  grid.appendChild(el("div", "jcard-h", `<b>Colonnes de sortie (${all.length})</b>`));
  grid.appendChild(el("p", "hint",
    "Les colonnes sont alignées <b>par nom</b>. Une colonne absente d'un dataset y vaudra <code>null</code>."));
  const table = el("div", "stackgrid");
  table.appendChild(el("div", "sg-h", "colonne"));
  aliases.forEach((a) => table.appendChild(el("div", "sg-h", esc(a))));
  all.forEach((name) => {
    table.appendChild(el("div", "sg-c", esc(name)));
    aliases.forEach((a) => {
      const has = colsOf(a).some((c) => c.name === name);
      table.appendChild(el("div", "sg-v" + (has ? " yes" : " no"), has ? "✓" : "null"));
    });
  });
  grid.appendChild(table);
  list.appendChild(grid);
}

export {
  renderGroupEditor,
  renderJoinEditor,
  renderStackEditor,
};
