/* Ce que l'écran sait à cet instant, et les textes d'aide qui vont avec. */


/* ------------------------------------------------------------------ état */

const state = {
  project: null,
  flow: null,
  selected: null,
  running: false,
  processors: [],
  categories: [],
  recipeTypes: {},  // clé → { label, icon, inputs } ; l'arité vient du serveur
  refs: [],
  view: "flow",
  fold: [],         // zones repliées, mémorisées par projet
  lineage: true,    // n'éclairer que l'amont/aval du nœud sélectionné
  find: "",
  explore: null,
  rec: null,      // { spec, preview, previewStep, openStep, dirty, isNew }
  lib: { cat: null, search: "", pick: null },
  /* Les listes fermées de dbt, servies par `/api/processors` au démarrage.
     Vides ici pour la même raison qu'au-dessus : une copie qui dort dans le
     navigateur finit par ne plus dire la même chose que le serveur, et
     personne ne s'en aperçoit. */
  caps: {
    materializations: [],
    incremental_strategies: [],
    strategies_needing_key: [],
    on_schema_change: [],
    batch_sizes: [],
    lookback_units: [],
    window_needs_column: [],
    window_needs_order: [],
  },
  tests: null,   // éditeur de tests ouvert en modale
};


const MAT_HELP = {
  view: "Recalculé à chaque requête, rien n'est stocké. Le défaut pour le staging.",
  table: "Table reconstruite entièrement à chaque exécution. Simple et prévisible.",
  incremental: "Seules les nouvelles lignes sont traitées à chaque exécution. Plus rapide sur gros volumes, mais il faut dire à dbt comment reconnaître ce qui est nouveau.",
  ephemeral: "Aucun objet créé dans l'entrepôt : le SQL est injecté dans les modèles qui s'en servent.",
};


const STRAT_HELP = {
  append: "Ajoute les nouvelles lignes sans rien remplacer. Le plus rapide — mais crée des doublons si une ligne est retraitée.",
  "delete+insert": "Supprime les lignes dont la clé réapparaît, puis insère. Sûr : c'est le choix par défaut.",
  merge: "Met à jour les lignes existantes et insère les nouvelles, en une seule opération.",
  microbatch: "Traite par tranches de temps successives. Réservé aux très gros volumes.",
};


const BATCH_HELP = {
  hour: "Une tranche par heure. Pour des flux très denses.",
  day: "Une tranche par jour. Le choix courant.",
  month: "Une tranche par mois.",
  year: "Une tranche par an.",
};


const OSC_HELP = {
  append_new_columns: "Une nouvelle colonne en amont est ajoutée à la table.",
  sync_all_columns: "Colonnes ajoutées ET supprimées sont répercutées.",
  ignore: "Les changements de schéma sont ignorés (défaut dbt).",
  fail: "Toute différence de schéma fait échouer l'exécution.",
};


const SEVERITIES = [["error", "bloquant"], ["warn", "avertissement"]];

/* ------------------------------------------------------------------- API */

export {
  BATCH_HELP,
  MAT_HELP,
  OSC_HELP,
  SEVERITIES,
  STRAT_HELP,
  state,
};
