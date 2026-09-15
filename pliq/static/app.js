/* Le point d'entrée : on importe, on branche, on démarre.
 *
 * `app.js` faisait 5 100 lignes dans une seule portée, avec 88
 * `addEventListener` semés entre les fonctions qu'ils appellent et un
 * `boot()` au milieu. Le tout marchait parce qu'un script classique hisse
 * ses déclarations de fonction — mais rien n'était nommable de l'extérieur,
 * et rien ne disait qui dépendait de quoi.
 *
 * Les modules ne font plus que déclarer. Les branchements attendent ici,
 * dans l'ordre du fichier d'origine, et `boot()` part en dernier : aucun
 * module n'appelle quoi que ce soit pendant que les autres se chargent, ce
 * qui rend les imports croisés inoffensifs.
 */

import { wire as wire_flow } from "./js/flow.js";
import { wire as wire_explore } from "./js/explore.js";
import { wire as wire_recipe } from "./js/recipe.js";
import { wire as wire_tests } from "./js/tests.js";
import { wire as wire_runs } from "./js/runs.js";
import { wire as wire_boot } from "./js/boot.js";
import { wire as wire_projects } from "./js/projects.js";
import { wire as wire_canvas } from "./js/canvas.js";
import { wire as wire_confirm } from "./js/confirm.js";
import { installModals } from "./js/modals.js";
import { boot } from "./js/boot.js";

wire_flow();
wire_explore();
wire_recipe();
wire_tests();
wire_runs();
wire_boot();
wire_projects();
wire_canvas();
wire_confirm();
// En dernier des branchements : le gestionnaire de modales observe les onze
// écrans que `FORMULAIRES` nomme, et il doit les trouver déjà en place.
installModals();

boot();
