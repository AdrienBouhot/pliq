/* Documenter et tester : la fiche d'un dataset, et ce qu'un test rejette. */
import { $, el, esc } from "./core.js";
import { state } from "./state.js";
import { api, post, currentProject, toast } from "./api.js";
import { loadFlow } from "./flow.js";
import { buildGrid } from "./explore.js";
import { touch } from "./recipe.js";
import { countTests, renderTestsEditor } from "./card.js";
// Cycle assumé — `projects.js` → `recipe.js` → `tests.js` → `projects.js` :
// aucun de ces modules n'appelle quoi que ce soit pendant son évaluation, et
// `closeForm` n'est lu qu'au clic. Le mettre ailleurs le séparerait de la
// liste des formulaires, qui est justement ce qu'il parcourt.
import { closeForm } from "./projects.js";
import { hlSql } from "./runs.js";


/* --- onglet Tests de la recipe --- */

function recipeTestsModel() {
  const rec = state.rec;
  const out = (rec.spec.output = rec.spec.output || {});
  const declared = out.columns || (out.columns = []);
  const byName = {};
  declared.forEach((c) => (byName[c.name] = c));

  const columns = (rec.preview ? rec.preview.columns : []).map((c) => {
    const d = byName[c.name] || { name: c.name, description: "", tests: [] };
    byName[c.name] = d;
    d.type = c.type;
    return d;
  });
  // Colonnes déclarées mais disparues du script : on les garde pour pouvoir les retirer.
  declared.forEach((c) => {
    if (!columns.includes(c)) columns.push(Object.assign(c, { missing: true }));
  });
  out.columns = columns;
  return {
    model: rec.spec.name,
    kind: "model",
    description: out.description || "",
    tags: out.tags || [],
    columns,
  };
}


function renderRecipeTests() {
  const rec = state.rec;
  if (!rec) return;
  const model = recipeTestsModel();
  renderTestsEditor($("rtests-body"), model, () => {
    rec.spec.output.description = model.description;
    rec.spec.output.tags = model.tags || [];
    touch();
    renderRecipeTests();
  }, { tags: false,
       intro: "Description et tests partent dans le <b>schema.yml</b> à côté du modèle. Les tags se règlent dans l'onglet <b>Sortie</b>." });
  const n = countTests(model);
  $("rtests-count").textContent = n || "";
}


function testsProblem(model) {
  for (const c of model.columns || []) {
    for (const t of c.tests || []) {
      if (t.name === "accepted_values" && !(t.values || []).length)
        return `Colonne « ${c.name} » : listez les valeurs autorisées, ou retirez le test.`;
      if (t.name === "relationships" && !t.to && !t.to_expr)
        return `Colonne « ${c.name} » : la relation ne pointe vers aucun modèle.`;
    }
  }
  return "";
}

/* --- modale de tests pour un dataset existant --- */


/* --- modale de tests pour un dataset existant --- */

async function openTestsModal(uidNode) {
  const ctx = currentProject();
  try {
    const data = await api(`/api/dataset/${encodeURIComponent(uidNode)}/tests`,
                           { project: ctx.project });
    if (ctx.stale()) return;
    state.tests = { uid: uidNode, model: data, project: ctx.project };
    $("tests-path").textContent = data.schema_path;
    $("tests-status").textContent = "";
    // Une fiche que la sauvegarde refusera se lit, mais ne se remplit pas :
    // laisser le formulaire actif faisait taper une description que le serveur
    // rejetait — ou, avant qu'il la rejette, écrivait au mauvais niveau du
    // YAML. Le serveur dit pourquoi ; on le répète ici, avant la frappe.
    const frozen = data.editable === false;
    $("tests-save").hidden = frozen;
    const render = () => {
      renderTestsEditor($("tests-modal-body"), data, render, {
        intro: frozen
          ? `<b>Lecture seule.</b> ${esc(data.readonly_reason || "")}`
          : `Écrit dans <b>${esc(data.schema_path)}</b>, section <code>${esc(
            data.kind === "source" ? "sources" : data.kind + "s")}</code>. Les autres entrées du fichier ne sont pas touchées.`,
      });
      if (frozen) {
        $("tests-modal-body")
          .querySelectorAll("input, textarea, select, button")
          .forEach((n) => { n.disabled = true; });
      }
      $("tests-status").textContent = `${countTests(data)} test(s) sur ${data.columns.length} colonnes`;
    };
      $("tests-modal-body").style.padding = "16px";
    render();
    $("tests-scrim").classList.add("on");
  } catch (e) {
    toast("Tests illisibles", e.message, "error");
  }
}


/* Le numéro de la demande affichée dans la modale de résultats. Contrôle
   complet et lignes en échec partagent le même conteneur : ouvrir A, puis B,
   et recevoir A en dernier remplissait le cadre de B avec les lignes de A —
   des données lues sous le mauvais titre. `dataModal` rend donc de quoi
   vérifier, au retour de chaque `await`, que c'est toujours cette demande-là
   qui est à l'écran. */
let dataGen = 0;

const dataModal = (title, note) => {
  const gen = ++dataGen;
  const { stale } = currentProject();
  $("data-title").textContent = title;
  $("data-note").innerHTML = note || "";
  const body = $("data-body");
  body.innerHTML = `<div class="empty">lecture de la table entière…</div>`;
  $("data-scrim").classList.add("on");
  return {
    body,
    // Fermer la modale invalide ses requêtes en vol, au même titre qu'en
    // ouvrir une autre.
    stale: () => gen !== dataGen || stale()
                  || !$("data-scrim").classList.contains("on"),
  };
};

/* Fermer la modale de résultats abandonne ce qu'elle attendait. */
const closeDataModal = () => {
  dataGen += 1;
  $("data-scrim").classList.remove("on");
};


const countLabel = (n) => (n == null ? "—" : Number(n).toLocaleString("fr-FR"));


async function openFullCheck(target, title) {
  const { body, stale } = dataModal(
    title, "<b>contrôle complet</b> — la table entière, pas l'échantillon");
  let r;
  try {
    r = await post("/api/profile/full", target);
  } catch (e) {
    if (stale()) return;
    body.innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
    return;
  }
  if (stale()) return;
  body.innerHTML = "";

  (r.changes || []).forEach((c) =>
    body.appendChild(el("p", "warnbox", esc(c))));

  const resume = el("p", "hint");
  resume.innerHTML = `<b>${countLabel(r.output.total)}</b> lignes en sortie` +
    (r.input ? ` — <b>${countLabel(r.input.total)}</b> en entrée` : "");
  body.appendChild(resume);

  const before = {};
  ((r.input || {}).columns || []).forEach((c) => (before[c.name] = c));

  // Pas de colonne « type » ici : celui que l'entrepôt rend pour une requête
  // d'une ligne est deviné sur cette ligne, et se trompe dès qu'elle est vide.
  // La grille, qui en voit deux cents, le dit déjà mieux.
  const t = el("table", "dgrid check");
  t.innerHTML =
    `<thead><tr>
       <th>colonne</th><th>remplies</th><th>vides</th>
       <th>distinctes</th><th>min</th><th>max</th>
     </tr></thead>`;
  const tb = el("tbody");
  r.output.columns.forEach((c) => {
    const v = before[c.name];
    // Une colonne qui se vide entre l'entrée et la sortie est le genre de
    // dégât qu'un aperçu de 200 lignes ne montre pas.
    const worst = v && (c.empty || 0) > (v.empty || 0) && r.output.total === (r.input || {}).total;
    const tr = el("tr");
    tr.innerHTML =
      `<td>${esc(c.name)}</td>
       <td class="num">${countLabel(c.filled)}</td>
       <td class="num${worst ? " bad" : ""}">${countLabel(c.empty)}${
         worst ? ` <i>(+${countLabel(c.empty - v.empty)})</i>` : ""}</td>
       <td class="num">${countLabel(c.distinct)}${
         c.distinct === r.output.total && r.output.total ? ' <i>unique</i>' : ""}</td>
       <td class="t">${esc(c.min == null ? "" : c.min)}</td>
       <td class="t">${esc(c.max == null ? "" : c.max)}</td>`;
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  body.appendChild(t);

  const discarded = r.output.skipped || {};
  // Chaque nom est échappé *puis* enveloppé : joindre d'abord faisait échapper
  // les balises elles-mêmes, et l'écran affichait « a</code>, <code>b » en
  // clair.
  const code = (names) => names.map((n) => `<code>${esc(n)}</code>`).join(", ");
  if ((discarded.type || []).length)
    body.appendChild(el("p", "hint",
      `Non profilées, faute d'un type que l'atelier sache agréger : ${code(discarded.type)}.`));
  if ((discarded.limit || []).length)
    body.appendChild(el("p", "hint",
      `Non profilées : au-delà de ${r.output.column_limit || 60} colonnes, la requête `
      + `d'agrégation devient elle-même un problème. ${code(discarded.limit)}.`));
  // Une case vide parce que l'entrepôt ne sait pas calculer la mesure sur ce
  // type n'est pas une donnée manquante : il faut le dire, sinon les deux se
  // lisent pareil.
  const partial = Object.entries(discarded.measures || {});
  if (partial.length)
    body.appendChild(el("p", "hint",
      "Mesures indisponibles sur cet entrepôt pour ces types : "
      + partial.map(([name, what]) =>
          `<code>${esc(name)}</code> (${esc(what.join(", "))})`).join(", ") + "."));
}


// Une seule source pour la limite : la légende « les N premières » mentirait
// dès que l'une des deux bougerait sans l'autre.
const FAILURES_LIMIT = 200;


async function openTestFailures(test) {
  const { body, stale } = dataModal(
    `Lignes en échec · ${test.name}`,
    test.column ? `sur la colonne <code>${esc(test.column)}</code>` : "");
  let r;
  try {
    r = await post(`/api/test/${encodeURIComponent(test.id)}/failures`,
                   { limit: FAILURES_LIMIT });
  } catch (e) {
    if (stale()) return;
    body.innerHTML = `<div class="err-box">${esc(e.message)}</div>`;
    return;
  }
  if (stale()) return;
  body.innerHTML = "";
  if (!r.rows.length) {
    body.appendChild(el("div", "empty",
      "<b>Aucune ligne en échec</b>Le test passe sur les données actuelles."));
  } else {
    const n = r.rows.length;
    body.appendChild(el("p", "warnbox",
      `<b>${countLabel(n)} ligne${n > 1 ? "s" : ""}</b> ${n > 1 ? "font" : "fait"} ` +
      `échouer ce test`
      + (n >= FAILURES_LIMIT ? ` (les ${FAILURES_LIMIT} premières)` : "") + "."));
    body.appendChild(buildGrid(r.columns, r.rows));
  }
  const details = el("details", "seesql");
  details.innerHTML = `<summary>La requête que dbt exécute</summary><pre class="code">${hlSql(r.sql)}</pre>`;
  body.appendChild(details);
}

/* ------------------------------------------------------------ coloration */

export {
  openTestFailures,
  openTestsModal,
  recipeTestsModel,
  renderRecipeTests,
  testsProblem,
};

/* Les branchements de ce module. Appelé par `main.js` une fois
   tous les modules évalués : aucun effet de bord ne doit avoir lieu
   pendant le chargement, sinon les imports croisés se mordent la
   queue. */
export function wire() {

  $("tests-cancel").addEventListener("click", () => closeForm("tests-scrim"));
  $("tests-scrim").addEventListener("click", (e) => {
    if (e.target === $("tests-scrim")) closeForm("tests-scrim");
  });
  $("tests-save").addEventListener("click", async (ev) => {
    if (!state.tests) return;
    const card = state.tests;
    const { uid: node, model, project } = card;
    const problem = testsProblem(model);
    if (problem) return toast("Test incomplet", problem, "error");
    // Ce qui part réellement, retenu avant l'aller-retour : l'utilisateur peut
    // continuer à écrire pendant l'enregistrement, et la modale se fermait
    // inconditionnellement au retour — ce qu'il venait de taper disparaissait
    // avec elle. L'éditeur SQL et la recipe prennent déjà cette précaution.
    const sent = JSON.stringify(model);
    const button = ev.currentTarget;
    // Et pas deux fois : sans ça, un double clic envoyait deux écritures dont
    // la seconde se heurtait à l'empreinte que la première venait de changer.
    button.disabled = true;
    try {
      const r = await post(`/api/dataset/${encodeURIComponent(node)}/tests`, {
        description: model.description || "",
        tags: model.tags || [],
        freshness: model.kind === "source" ? (model.freshness || {}) : null,
        // L'empreinte de la fiche lue : la sauvegarde refuse d'écraser une
        // documentation qu'un autre écran a changée depuis.
        base: model.doc_digest || null,
        columns: (model.columns || []).map((c) => ({
          name: c.name, description: c.description || "", tests: c.tests || [],
        })),
      }, { project });
      // Un autre formulaire a pu prendre la place pendant l'aller-retour :
      // le fermer serait fermer celui de quelqu'un d'autre.
      if (state.tests !== card) {
        toast("Tests enregistrés", r.path, "ok");
        return;
      }
      // La fiche écrite devient celle dont on repart, sinon la sauvegarde
      // suivante se croirait en conflit avec celle-ci.
      if (r.base !== undefined) model.doc_digest = r.base;
      toast("Tests enregistrés", r.path, "ok");
      if (r.parse_error) toast("dbt parse échoue", r.parse_error, "error");
      // Fermer seulement si rien n'a été saisi entre-temps.
      if (JSON.stringify(model) === sent) {
        closeForm("tests-scrim");
      } else {
        $("tests-status").textContent =
          "Enregistré — des modifications faites pendant la sauvegarde restent à écrire.";
      }
      loadFlow(true);
    } catch (e) {
      toast("Enregistrement impossible", e.message, "error");
    } finally {
      button.disabled = false;
    }
  });

  /* ====================== CONTRÔLE COMPLET & LIGNES EN ÉCHEC ==============

     La grille profile un échantillon. Deux cents lignes ne disent pas combien de
     valeurs distinctes compte une table d'un million, ni combien de lignes une
     étape a retirées — et prendre l'un pour l'autre est la façon la plus simple
     de se tromper d'un facteur mille. Ces deux écrans agrègent sur *tout*, et le
     disent en toutes lettres. */


  $("data-close").addEventListener("click", closeDataModal);
  $("data-scrim").addEventListener("click", (e) => {
    if (e.target === $("data-scrim")) closeDataModal();
  });


  $("rec-check").addEventListener("click", () => {
    if (!state.rec) return;
    openFullCheck({ spec: state.rec.spec }, `Contrôle complet · ${state.rec.spec.name}`);
  });
  $("exp-check").addEventListener("click", () => {
    if (!state.explore) return;
    openFullCheck({ uid: state.explore.id }, `Contrôle complet · ${state.explore.name}`);
  });

  /* --- les lignes qui font échouer un test ---

     dbt compile chaque test en une requête qui sélectionne les échecs : la
     rejouer montre exactement les lignes fautives. Entre « le test a échoué » et
     « voilà quoi corriger », c'est la moitié du chemin. */

  // Une seule source pour la limite : la légende « les N premières » mentirait
  // dès que l'une des deux bougerait sans l'autre.
}
