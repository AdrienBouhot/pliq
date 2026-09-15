/* La documentation de Pliq : un sommaire, des pages Markdown, une recherche.

   Le même parti pris que l'atelier — aucune construction, aucune dépendance,
   rien qui sorte de la machine. Les pages sont des `.md` du dépôt, servies
   telles quelles et rendues ici ; le sommaire est un JSON qui dit l'ordre des
   chapitres, et lui seul. Ajouter une page, c'est écrire un fichier et une
   ligne.

   L'adresse d'une page est `/doc?p=<slug>#<ancre>` : une vraie adresse, qu'on
   met en signet et qu'on copie dans un ticket. Le `?` du bandeau de l'atelier
   l'ouvre dans un onglet à part — on ne quitte pas ce qu'on fait pour aller
   lire comment le faire. */

import { esc, render } from "./markdown.js";

const PAGES = "/static/doc/pages";
const HOME = "index";

const $ = (id) => document.getElementById(id);

/** Ce qui a déjà été chargé : une page n'est lue qu'une fois par onglet. */
const cache = new Map();
let summary = [];
/** Les pages dans l'ordre du sommaire — c'est lui qui fait précédent/suivant. */
let flat = [];
/** L'index de recherche, construit à la première frappe et pas avant. */
let index = null;

/* ------------------------------------------------------------- chargement */

async function textOf(slug) {
  if (cache.has(slug)) return cache.get(slug);
  const r = await fetch(`${PAGES}/${encodeURIComponent(slug)}.md`);
  if (!r.ok) throw new Error(`Page introuvable : ${slug}`);
  const text = await r.text();
  cache.set(slug, text);
  return text;
}

function entryOf(slug) {
  return flat.find((p) => p.slug === slug);
}

/* ---------------------------------------------------------------- sommaire */

function paintSummary(current) {
  const nav = $("nav");
  nav.innerHTML = "";
  summary.forEach((section) => {
    const open = section.pages.some((p) => p.slug === current);
    const navSection = document.createElement("section");
    navSection.className = "nav-sec" + (open ? " on" : "");

    const title = document.createElement("button");
    title.type = "button";
    title.className = "nav-sec-h";
    title.setAttribute("aria-expanded", String(open));
    title.innerHTML = `<span>${esc(section.title)}</span>`;
    title.addEventListener("click", () => {
      const state = navSection.classList.toggle("on");
      title.setAttribute("aria-expanded", String(state));
    });
    navSection.appendChild(title);

    const list = document.createElement("ul");
    section.pages.forEach((page) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = `?p=${encodeURIComponent(page.slug)}`;
      a.textContent = page.title;
      if (page.slug === current) {
        a.className = "on";
        a.setAttribute("aria-current", "page");
      }
      li.appendChild(a);
      list.appendChild(li);
    });
    navSection.appendChild(list);
    nav.appendChild(navSection);
  });
}

/* ------------------------------------------------------------------ rendu */

/* Les liens entre pages s'écrivent `[texte](flow.md#ancre)` : c'est un lien
   qui marche aussi quand on lit le `.md` dans un éditeur ou sur une forge.
   Ici, il devient l'adresse de la page. */
function rewriteLinks(zone) {
  zone.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href");
    if (!href.includes(".md")) return;
    const [target, anchor] = href.split("#");
    const slug = target.replace(/\.md$/, "");
    a.setAttribute("href", `?p=${encodeURIComponent(slug)}${anchor ? `#${anchor}` : ""}`);
  });
}

function paintPageSummary(headings) {
  const box = $("toc");
  const useful = headings.filter((t) => t.level === 2 || t.level === 3);
  box.hidden = useful.length < 2;
  if (box.hidden) return;
  box.innerHTML = "<b>Sur cette page</b>";
  const list = document.createElement("ul");
  useful.forEach((t) => {
    const li = document.createElement("li");
    li.className = `n${t.level}`;
    const a = document.createElement("a");
    a.href = `#${t.id}`;
    a.textContent = t.text;
    li.appendChild(a);
    list.appendChild(li);
  });
  box.appendChild(list);
}

function paintBreadcrumb(entry) {
  const thread = $("fil");
  const chunks = [`<a href="?p=${HOME}">Documentation</a>`];
  if (entry && entry.section) chunks.push(`<span>${esc(entry.section)}</span>`);
  if (entry && entry.slug !== HOME) chunks.push(`<b>${esc(entry.title)}</b>`);
  thread.innerHTML = chunks.join('<i aria-hidden="true">›</i>');
}

function paintNeighbours(slug) {
  const n = flat.findIndex((p) => p.slug === slug);
  const foot = $("voisins");
  foot.innerHTML = "";
  const link = (page, direction) => {
    if (!page) return;
    const a = document.createElement("a");
    a.className = `voisin ${direction}`;
    a.href = `?p=${encodeURIComponent(page.slug)}`;
    a.innerHTML =
      `<span>${direction === "prec" ? "Précédent" : "Suivant"}</span>` +
      `<b>${esc(page.title)}</b>`;
    foot.appendChild(a);
  };
  link(flat[n - 1], "prec");
  link(flat[n + 1], "suiv");
}

async function display(slug, anchor) {
  const zone = $("page");
  let source;
  try {
    source = await textOf(slug);
  } catch {
    zone.innerHTML =
      `<h1>Page introuvable</h1><p>La page <code>${esc(slug)}</code> n'existe pas ` +
      `(ou plus). <a href="?p=${HOME}">Revenir au sommaire</a>.</p>`;
    paintBreadcrumb(null);
    $("toc").hidden = true;
    $("voisins").innerHTML = "";
    return;
  }

  const { html, headings } = render(source);
  zone.innerHTML = html;
  rewriteLinks(zone);

  const entry = entryOf(slug);
  const title = (headings.find((t) => t.level === 1) || {}).text || (entry || {}).title;
  document.title = title ? `${title} — Documentation Pliq` : "Documentation Pliq";

  paintSummary(slug);
  paintBreadcrumb(entry || { slug, title });
  paintPageSummary(headings);
  paintNeighbours(slug);
  closeDrawer();

  // L'ancre après le rendu, et jamais avant : la cible n'existe pas tant que
  // la page n'est pas écrite.
  if (anchor && document.getElementById(anchor)) {
    document.getElementById(anchor).scrollIntoView();
  } else {
    $("lecture").scrollTop = 0;
  }
}

/* ---------------------------------------------------------------- routage */

function currentSlug() {
  return new URLSearchParams(location.search).get("p") || HOME;
}

function goTo(slug, anchor, replace) {
  const url = `?p=${encodeURIComponent(slug)}${anchor ? `#${anchor}` : ""}`;
  if (replace) history.replaceState({}, "", url);
  else history.pushState({}, "", url);
  display(slug, anchor);
}

function wireLinks() {
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a || a.target === "_blank") return;
    const href = a.getAttribute("href");
    if (!href) return;

    // Une ancre seule reste une ancre : le navigateur la suit mieux que nous.
    if (href.startsWith("#")) return;
    if (!href.startsWith("?p=")) return;

    e.preventDefault();
    const [target, anchor] = href.slice(3).split("#");
    goTo(decodeURIComponent(target), anchor, false);
  });

  window.addEventListener("popstate", () => {
    display(currentSlug(), location.hash.slice(1) || "");
  });
}

/* -------------------------------------------------------------- recherche */

/* L'index est construit au premier usage : la documentation tient en quelques
   dizaines de fichiers servis par le même processus, et les charger d'avance
   ne ferait que retarder l'affichage de la page qu'on est venu lire. */
async function buildIndex() {
  if (index) return index;
  const pages = await Promise.all(
    flat.map(async (p) => {
      const text = await textOf(p.slug).catch(() => "");
      return { ...p, text };
    })
  );
  index = pages;
  return index;
}

function withoutAccents(text) {
  return String(text)
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();
}

function excerpt(text, words) {
  // Le Markdown se lit mal en extrait : un lien y apparaissait avec sa cible
  // — « [onglet Sortie](incremental.md#la-fenetre-de-reprise) » — au milieu
  // d'une phrase. On ne garde que ce qui se lit.
  const flat = text
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/\[![a-z]+\]/gi, " ")
    .replace(/[#`*>|]/g, " ")
    .replace(/\s+/g, " ");
  const bare = withoutAccents(flat);
  const n = bare.indexOf(words[0]);
  if (n < 0) return flat.slice(0, 120) + "…";
  const start = Math.max(0, n - 40);
  return (start ? "…" : "") + flat.slice(start, start + 150).trim() + "…";
}

function search(pages, question) {
  const words = withoutAccents(question).split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return pages
    .map((p) => {
      const title = withoutAccents(p.title);
      const body = withoutAccents(p.text);
      let score = 0;
      for (const word of words) {
        if (!body.includes(word) && !title.includes(word)) return null;
        if (title.includes(word)) score += 10;
        // Un titre de section pèse plus qu'une mention dans un paragraphe.
        const pattern = word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
        score += (body.match(new RegExp(`^#+ .*${pattern}`, "gm")) || []).length * 5;
        score += (body.split(word).length - 1);
      }
      return { page: p, score };
    })
    .filter(Boolean)
    .sort((a, b) => b.score - a.score)
    .slice(0, 12)
    .map(({ page }) => ({ page, excerpt: excerpt(page.text, words) }));
}

function paintResults(results, question) {
  const box = $("resultats");
  box.hidden = false;
  box.innerHTML = "";
  if (!results.length) {
    // La question est tapée par qui lit : elle s'échappe, comme tout le reste.
    box.innerHTML = `<p class="vide">Rien pour « ${esc(question)} ».</p>`;
    return;
  }
  results.forEach(({ page, excerpt: text }) => {
    const a = document.createElement("a");
    a.href = `?p=${encodeURIComponent(page.slug)}`;
    a.innerHTML =
      `<b>${esc(page.title)}</b><span>${esc(page.section)}</span>` +
      `<p>${esc(text)}</p>`;
    box.appendChild(a);
  });
}

function wireSearch() {
  const field = $("q");
  let pending;

  const launch = async () => {
    const question = field.value.trim();
    if (question.length < 2) {
      $("resultats").hidden = true;
      return;
    }
    paintResults(search(await buildIndex(), question), question);
  };

  field.addEventListener("input", () => {
    clearTimeout(pending);
    pending = setTimeout(launch, 120);
  });
  field.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      field.value = "";
      $("resultats").hidden = true;
      field.blur();
    }
    if (e.key === "Enter") {
      const first = $("resultats").querySelector("a");
      if (first) first.click();
    }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".recherche")) $("resultats").hidden = true;
  });
  // « / » ouvre la recherche, comme partout ailleurs.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) return;
    e.preventDefault();
    field.focus();
    field.select();
  });
}

/* ------------------------------------------------- le tiroir des écrans étroits */

function closeDrawer() {
  document.body.classList.remove("tiroir");
}

function wireDrawer() {
  $("burger").addEventListener("click", () => {
    document.body.classList.toggle("tiroir");
  });
  $("voile").addEventListener("click", closeDrawer);
}

/* ------------------------------------------------------------------- boot */

async function start() {
  wireLinks();
  wireSearch();
  wireDrawer();

  try {
    summary = await (await fetch("/static/doc/sommaire.json")).json();
  } catch {
    $("page").innerHTML =
      "<h1>Sommaire illisible</h1><p>Le fichier <code>sommaire.json</code> " +
      "n'a pas pu être lu. La documentation est servie par l'atelier : " +
      "s'il tourne, rechargez la page.</p>";
    return;
  }
  flat = summary.flatMap((s) => s.pages.map((p) => ({ ...p, section: s.title })));

  await display(currentSlug(), location.hash.slice(1) || "");
}

start();
