"""Point d'entrée : python -m pliq"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import threading
import webbrowser

import uvicorn

from . import projects
from .config import load_settings
from .server import create_app

# Ce que l'atelier peut faire, et qu'il faut avoir en tête avant de l'ouvrir
# au réseau. Ce n'est pas une liste de failles : c'est la liste de ses
# fonctions, et c'est bien le problème — elles sont toutes offertes sans
# authentification, parce qu'un outil de poste de travail n'en a pas besoin.
EXPOSURE = """\
  • exécuter du SQL et du Jinja dbt quelconques sur votre entrepôt
    (c'est ce que fait une recipe « SQL », et son aperçu) ;
  • écrire, renommer et supprimer les fichiers de vos projets dbt ;
  • supprimer un dossier de projet entier ;
  • lire vos profils dbt tels que dbt les résout.

  L'atelier n'a aucune authentification à opposer : il ne compte que sur
  le fait d'être joignable depuis votre seule machine."""


def trusted_host(host: str) -> bool:
    """Cette adresse d'écoute reste-t-elle sur la machine ?

    C'est toute la sécurité de l'atelier. Les parades qu'il porte — le contrôle
    du nom d'hôte, celui de l'origine sur la socket, le refus d'un corps JSON
    sans `Content-Type` — visent une seule attaque : une page web qui essaie de
    parler à un atelier *local* depuis le navigateur de qui l'a ouvert. Aucune
    ne protège de quelqu'un qui atteint le port directement.

    Écouter ailleurs que sur la boucle locale rend donc tout ce qui précède
    inopérant, et il n'y a rien derrière. `--host 0.0.0.0` était accepté sans
    un mot.
    """
    name = (host or "").strip().lower().strip("[]")
    if name in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        # Un nom que le DNS résoudra : on ne sait pas où il mène, et il ne mène
        # presque jamais à la boucle locale.
        return False


def _check_listening(host: str, expose: bool) -> None:
    if trusted_host(host) or expose:
        return
    raise SystemExit(
        f"\n  Refus d'écouter sur « {host} ».\n\n"
        f"  Cette adresse rend l'atelier joignable depuis le réseau, et\n"
        f"  quiconque l'atteint peut, sans mot de passe :\n\n"
        f"{EXPOSURE}\n\n"
        f"  Si c'est bien ce que vous voulez — une machine de développement\n"
        f"  sur un réseau que vous maîtrisez, un tunnel SSH en face —\n"
        f"  ajoutez --exposer pour le dire explicitement.\n\n"
        f"  Sinon, laissez l'atelier sur 127.0.0.1 et atteignez-le par un\n"
        f"  tunnel : ssh -L 8765:127.0.0.1:8765 la-machine\n"
    )


def _configure_logging(verbose: bool) -> None:
    """Le journal de l'atelier, réglable — ce que `print` ne savait pas faire.

    Seul `pliq` est touché : régler la racine mettrait aussi dbt et uvicorn au
    même niveau, et `dbt parse` en debug noie tout le reste.
    """
    logger = logging.getLogger("pliq")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    if not logger.handlers:
        output = logging.StreamHandler()
        output.setFormatter(logging.Formatter("  [pliq] %(message)s"))
        logger.addHandler(output)
    logger.propagate = False


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pliq",
        description="Atelier visuel pour un projet dbt Core.",
    )
    parser.add_argument("--project-dir", help="Dossier contenant dbt_project.yml")
    parser.add_argument("--profiles-dir", help="Dossier contenant profiles.yml")
    parser.add_argument("--target", help="Cible dbt à utiliser (dev, prod…)")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Adresse d'écoute. Hors boucle locale, exige --exposer.",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Ne pas ouvrir le navigateur au démarrage",
    )
    parser.add_argument(
        "--exposer",
        action="store_true",
        help="Assumer une écoute hors de la machine (voir --host)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Journal détaillé de l'atelier",
    )
    args = parser.parse_args()

    # Avant tout le reste : rien ne doit démarrer si l'écoute est refusée.
    _check_listening(args.host, args.exposer)
    _configure_logging(args.verbose)

    def _load(project_dir: str | None):
        return load_settings(
            project_dir=project_dir,
            profiles_dir=args.profiles_dir,
            target=args.target,
            host=args.host,
            port=args.port,
        )

    try:
        settings = _load(args.project_dir)
    except SystemExit:
        # Lancé hors d'un projet : on rouvre le dernier utilisé, l'écran
        # d'accueil permettra d'en choisir un autre.
        last = projects.last_opened() if not args.project_dir else None
        if last is None:
            raise
        settings = _load(str(last))

    # dbt-duckdb résout un `path:` relatif depuis le répertoire courant du
    # process, pas depuis --project-dir. Sans ce chdir, l'atelier écrirait dans
    # une base et lirait dans une autre.
    os.chdir(settings.project_dir)

    url = f"http://{settings.host}:{settings.port}"
    # La bannière reste un `print` : ce n'est pas une trace, c'est la réponse à
    # « qu'est-ce que je viens de lancer ». Elle ne se tait pas et ne se règle
    # pas — le journal, lui, est juste en dessous.
    print(f"\n  Pliq — projet « {settings.project_name} »")
    print(f"  projet   : {settings.project_dir}")
    print(f"  profils  : {settings.profiles_dir}")
    print(f"  cible    : {settings.active_target()}")
    print(f"  atelier  : {url}")
    if not trusted_host(settings.host):
        print(f"\n  ⚠ ouvert sur le réseau ({settings.host}), sans authentification.")
    print()

    if not args.no_browser:
        threading.Timer(1.4, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="warning",
        # Un `dbt build` lancé dans un thread ne s'interrompt pas : sans borne,
        # Ctrl-C fermerait le port puis attendrait la fin du run, atelier muet.
        timeout_graceful_shutdown=8,
    )


if __name__ == "__main__":
    main()
