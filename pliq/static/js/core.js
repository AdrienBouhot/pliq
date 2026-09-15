/* Le petit outillage : créer un nœud, échapper du texte, dessiner une icône. */
import { state } from "./state.js";


const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const esc = (s) =>
  String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
const uid = () => "s" + Math.random().toString(36).slice(2, 9);

/* ------------------------------------------------------------------ icônes */


/* ------------------------------------------------------------------ icônes */

const ICONS = {
  model: '<path d="M3 4h18v16H3V4zm2 4v3h5V8H5zm7 0v3h7V8h-7zM5 13v3h5v-3H5zm7 0v3h7v-3h-7z"/>',
  seed: '<path d="M12 2l5 5h-3.5v6h-3V7H7l5-5zM3 16h18v5H3v-5z"/>',
  source: '<path d="M12 2c4.4 0 8 1.4 8 3.1v13.8c0 1.7-3.6 3.1-8 3.1s-8-1.4-8-3.1V5.1C4 3.4 7.6 2 12 2zm0 1.8c-3.6 0-6 1-6 1.3s2.4 1.3 6 1.3 6-1 6-1.3-2.4-1.3-6-1.3z"/>',
  snapshot: '<path d="M12 4a8 8 0 108 8h-2a6 6 0 11-6-6V4z"/><path d="M12 1l4 3-4 3V1z"/>',
  prepare: '<path d="M16.5 2.1l5.4 5.4-2.3 2.3-1.2-1.2-5.6 5.6 1.2 1.2-2.3 2.3-5.4-5.4 2.3-2.3 1.2 1.2 5.6-5.6-1.2-1.2 2.3-2.3z"/><path d="M7.4 14.2l2.4 2.4L5 21.9 2.1 19l5.3-4.8z"/>',
  join: '<circle cx="9.5" cy="12" r="5.4" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="14.5" cy="12" r="5.4" fill="none" stroke="currentColor" stroke-width="2"/>',
  group: '<path d="M3 4h18v2.4H3V4zm2.6 5.3h12.8v2.4H5.6V9.3zM8.2 14.6h7.6V17H8.2v-2.4zM10.6 19.9h2.8v2.1h-2.8v-2.1z"/>',
  stack: '<path d="M12 2l9 4.6-9 4.6-9-4.6L12 2zm0 8.2l9 4.6-9 4.6-9-4.6 2.6-1.3L12 17l6.4-3.3 2.6 1.3-9-4.8z"/>',
  distinct: '<path d="M4 8.5h11.2l-2-2 1.6-1.6L19.6 9.5l-4.8 4.6-1.6-1.6 2-2H4v-2zM20 15.5v2H8.8l2 2-1.6 1.6L4.4 16.5l4.8-4.6 1.6 1.6-2 2H20z"/>',
  topn: '<path d="M3 4h5v2.5H3V4zm7 0h11v2.5H10V4zM3 10.7h5v2.5H3v-2.5zm7 0h11v2.5H10v-2.5zM3 17.5h5V20H3v-2.5zm7 0h11V20H10v-2.5z"/>',
  sql: '<path d="M9.4 5.6L3 12l6.4 6.4 1.7-1.7L6.4 12l4.7-4.7-1.7-1.7zm5.2 0l-1.7 1.7L17.6 12l-4.7 4.7 1.7 1.7L21 12l-6.4-6.4z"/>',
  filter: '<path d="M3 4h18l-7 8.3V21l-4-2.3v-6.4L3 4z"/>',
  eye: '<path d="M12 5C6.5 5 2.7 9.3 1.5 12 2.7 14.7 6.5 19 12 19s9.3-4.3 10.5-7C21.3 9.3 17.5 5 12 5zm0 11a4 4 0 110-8 4 4 0 010 8z"/>',
  power: '<path d="M11 3h2v9h-2V3zm-3.2 2.3l1.4 1.5a6 6 0 106.7 0l1.4-1.5a8 8 0 11-9.5 0z"/>',
  trash: '<path d="M9 3h6l1 1.5h4V7H4V4.5h4L9 3zM5.5 8.5h13L17.3 21H6.7L5.5 8.5z"/>',
  up: '<path d="M12 6l7 8h-4.5v5h-5v-5H5l7-8z"/>',
  down: '<path d="M12 18l-7-8h4.5V5h5v5H19l-7 8z"/>',
  copy: '<path d="M8 2h10a2 2 0 012 2v12h-2.5V4.5H8V2zM4 6h10a2 2 0 012 2v12a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2z"/>',
};


const svg = (name, size) =>
  `<svg viewBox="0 0 24 24" ${size ? `width="${size}" height="${size}"` : ""}>${ICONS[name] || ICONS.model}</svg>`;


const RECIPE_ICON = {
  prepare: "prepare", join: "join", group: "group", stack: "stack",
  distinct: "distinct", topn: "topn", sql: "sql", filter: "filter",
};

/* --------------------------------------------------------------- champs */

/* Ces listes sont remplies au démarrage par `/api/processors`, et pas écrites
   ici.

   Elles l'étaient, en double : mêmes clés, mêmes libellés français, et un test
   par liste pour vérifier que les deux copies restaient d'accord. Un test qui
   surveille une duplication ne la remplace pas — il en fait une dette qu'on
   entretient, et il ne dit rien du jour où le serveur gagne un opérateur que
   l'écran n'offre pas.

   Un repli ne manque à personne : sans `/api/processors`, il n'y a ni
   bibliothèque de processeurs ni type de recipe, et l'atelier n'a rien à
   montrer. Mieux vaut un écran manifestement vide qu'un menu qui a l'air
   complet et ne l'est plus.

   Les types de « Changer le type » nomment ceux de l'entrepôt ouvert :
   `varchar` n'existe pas sur BigQuery, `double` ni sur PostgreSQL ni sur
   Redshift. Ils n'ont jamais pu être écrits ici. */
const OPS = [];
const TYPES = [];
const WINDOW_FNS = [];
const PIVOT_AGGS = [];


const winFn = (p) => p.fn || "row_number";


const FIELDS = {
  rename: [{ k: "renames", t: "renames", label: "Renommages" }],
  keep_delete: [
    { k: "action", t: "select", label: "Action", opts: [["delete", "Supprimer"], ["keep", "Conserver uniquement"]] },
    { k: "columns", t: "cols", label: "Colonnes" }],
  concat_columns: [
    { k: "columns", t: "cols", label: "Colonnes à assembler" },
    { k: "separator", t: "text", label: "Séparateur" },
    { k: "into", t: "text", label: "Colonne de sortie" }],
  split_column: [
    { k: "column", t: "col", label: "Colonne" },
    { k: "separator", t: "text", label: "Séparateur" },
    { k: "count", t: "number", label: "Nombre de morceaux" }],
  formula: [
    { k: "into", t: "text", label: "Colonne de sortie" },
    { k: "expression", t: "text", label: "Expression SQL" }],
  if_then_else: [
    { k: "into", t: "text", label: "Colonne de sortie" },
    { k: "condition", t: "text", label: "Si (condition SQL)" },
    { k: "then", t: "text", label: "Alors" },
    { k: "otherwise", t: "text", label: "Sinon" }],
  filter_value: [
    { k: "column", t: "col", label: "Colonne" },
    { k: "operator", t: "select", label: "Opérateur", opts: OPS },
    { k: "values", t: "values", label: "Valeurs" },
    { k: "action", t: "select", label: "Action", opts: [["remove", "Retirer les lignes"], ["keep", "Garder les lignes"], ["clear", "Vider la cellule"]] }],
  filter_formula: [
    { k: "condition", t: "text", label: "Condition SQL" },
    { k: "action", t: "select", label: "Action", opts: [["keep", "Garder les lignes"], ["remove", "Retirer les lignes"]] }],
  remove_empty: [{ k: "column", t: "col", label: "Colonne" }],
  fill_empty: [
    { k: "column", t: "col", label: "Colonne" },
    { k: "value", t: "text", label: "Valeur de remplacement" }],
  find_replace: [
    { k: "column", t: "col", label: "Colonne" },
    { k: "find", t: "text", label: "Rechercher" },
    { k: "replace", t: "text", label: "Remplacer par" }],
  text_transform: [
    { k: "column", t: "col", label: "Colonne" },
    { k: "mode", t: "select", label: "Transformation", opts: [["lower", "minuscules"], ["upper", "MAJUSCULES"], ["trim", "sans espaces autour"], ["capitalize", "Capitalisé (1re lettre)"], ["trim_lower", "minuscules sans espaces"]] }],
  change_type: [
    { k: "column", t: "col", label: "Colonne" },
    { k: "to", t: "select", label: "Nouveau type", opts: TYPES }],
  round: [
    { k: "column", t: "col", label: "Colonne" },
    { k: "decimals", t: "number", label: "Décimales" }],
  parse_date: [
    { k: "column", t: "col", label: "Colonne" },
    { k: "format", t: "text", label: "Format strptime (vide = auto)" },
    { k: "into", t: "text", label: "Colonne de sortie" }],
  extract_date_parts: [
    { k: "column", t: "col", label: "Colonne" },
    { k: "parts", t: "chips", label: "Composants", opts: [["year", "année"], ["month", "mois"], ["day", "jour"], ["quarter", "trimestre"], ["week", "semaine"], ["dow", "jour sem."]] }],
  distinct_rows: [],
  sort: [
    { k: "column", t: "col", label: "Colonne" },
    { k: "descending", t: "bool", label: "Décroissant" }],
  dedup_key: [
    { k: "keys", t: "cols", label: "Colonnes de la clé" },
    { k: "order_by", t: "order", label: "Garder la ligne qui vient en premier selon" },
    { k: "ties", t: "select", label: "S'il reste des ex æquo",
      opts: [["one", "n'en garder qu'une"], ["all", "les garder toutes"]] }],
  window_function: [
    { k: "fn", t: "select", label: "Fonction", opts: WINDOW_FNS },
    { k: "into", t: "text", label: "Colonne de sortie" },
    { k: "column", t: "col", label: "Colonne mesurée",
      when: (p) => state.caps.window_needs_column.includes(winFn(p)) },
    { k: "partition_by", t: "cols", label: "Fenêtre — une par combinaison de" },
    { k: "order_by", t: "order", label: "Ordre dans la fenêtre",
      when: (p) => state.caps.window_needs_order.includes(winFn(p)) },
    { k: "offset", t: "number", label: "De combien de lignes",
      when: (p) => ["lag", "lead"].includes(winFn(p)) },
    { k: "window_rows", t: "number", label: "Sur combien de lignes",
      when: (p) => winFn(p) === "moving_avg" }],
  unpivot: [
    { k: "columns", t: "cols", label: "Colonnes à replier" },
    { k: "name_into", t: "text", label: "Colonne qui recevra les noms" },
    { k: "value_into", t: "text", label: "Colonne qui recevra les valeurs" }],
  pivot: [
    { k: "key_columns", t: "cols", label: "Une ligne par combinaison de" },
    { k: "name_column", t: "col", label: "Colonne qui porte les noms" },
    { k: "value_column", t: "col", label: "Colonne qui porte les valeurs" },
    { k: "values", t: "values", label: "Valeurs à déplier" },
    { k: "aggregate", t: "select", label: "Si plusieurs lignes tombent dans la même case",
      opts: PIVOT_AGGS },
    { k: "prefix", t: "text", label: "Préfixe des colonnes créées" }],
};

/* ------------------------------------------------------------------ état */

export {
  $,
  FIELDS,
  OPS,
  PIVOT_AGGS,
  RECIPE_ICON,
  TYPES,
  WINDOW_FNS,
  el,
  esc,
  svg,
  uid,
};
