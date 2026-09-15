/* La question à deux réponses, posée par l'atelier et non par le navigateur.

   `confirm()` dessine la boîte du système : sa police, son fond, et deux
   boutons « OK » et « Annuler » qui ne disent pas ce qu'ils font. C'était la
   seule modale de l'application qui ne ressemblait pas à l'application — les
   dix autres passent par `.scrim` / `.modal`, avec Échap, un focus piégé et
   rendu, et des boutons nommés par leur geste.

   Le prix d'une vraie modale est qu'elle ne bloque pas le fil d'exécution :
   la réponse arrive dans une promesse, et les appelants de `quitterRecipe`
   sont devenus asynchrones pour l'attendre. */

import { $ } from "./core.js";

const BOX = "quit-scrim";

/* Une seule question à la fois, donc pas de pile : une question de sortie
   ferme l'écran qui aurait pu en poser une autre. */
let answer = null;

/** Tranche la question en cours, s'il y en a une. Appelable deux fois sans mal. */
function answerConfirm(reply) {
  const resolve = answer;
  answer = null;
  if (resolve) resolve(reply);
}

/** Pose la question et rend une promesse de « oui » ou de « non ». */
function askConfirm({ title, message, ok, cancel = "Annuler", danger = false }) {
  const box = $(BOX);
  // Sans la modale dans le document, mieux vaut ne rien bloquer que bloquer
  // pour toujours sur une promesse que personne ne tranchera.
  if (!box) return Promise.resolve(true);
  // Une question laissée ouverte tombe plutôt que de rester en l'air : sa
  // promesse doit être tenue, sinon l'appelant attend sans fin.
  answerConfirm(false);
  $("quit-title").textContent = title;
  $("quit-msg").textContent = message;
  const go = $("quit-go");
  go.textContent = ok;
  go.className = "btn " + (danger ? "danger" : "primary");
  $("quit-cancel").textContent = cancel;
  box.classList.add("on");
  return new Promise((resolve) => { answer = resolve; });
}

export function wire() {
  const box = $(BOX);
  if (!box) return;
  $("quit-go").addEventListener("click", () => {
    // Répondre avant de fermer : la fermeture passe par le nettoyage, qui
    // répond « non » à tout ce qui n'a pas encore été tranché.
    answerConfirm(true);
    box.classList.remove("on");
  });
  $("quit-cancel").addEventListener("click", () => {
    answerConfirm(false);
    box.classList.remove("on");
  });
}

export { askConfirm, answerConfirm };
