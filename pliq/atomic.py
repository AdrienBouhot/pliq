"""Écrire un fichier d'un seul geste, et ne pas perdre le registre.

Deux primitives, sans dépendance sur le reste du paquet : elles sont appelées
par `files`, par le stockage des recipes et par le registre des projets, qui
ne peuvent pas s'importer les uns les autres.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path


def _default_file_mode() -> int:
    """Le mode qu'aurait un fichier créé normalement, masque de session compris.

    `os.umask` ne se lit qu'en l'écrivant : la seule façon d'obtenir le masque
    courant est de le remplacer, puis de le remettre. On le fait au chargement
    du module, quand aucun autre fil n'existe encore — le refaire à chaque
    écriture ouvrirait une fenêtre, si brève soit-elle, où un fichier créé
    ailleurs dans le processus naîtrait en 0666.
    """
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


_NEW_FILE_MODE = _default_file_mode()


def write_bytes_atomically(path: Path, data: bytes) -> None:
    """Écrit `data` dans `path`, ou n'écrit rien.

    `Path.write_text` ouvre la destination, écrit, ferme : une coupure au
    milieu — SIGKILL, disque plein, arrêt machine — laisse un `.sql` tronqué à
    la place du modèle. Un contexte « tout ou rien » protège de l'*erreur*
    Python, jamais de la *panne* : son `except` ne s'exécute pas si le
    processus n'existe plus.

    Un temporaire dans le même dossier, puis `os.replace` : le remplacement est
    atomique sur un même système de fichiers, et un lecteur voit soit l'ancien
    fichier entier, soit le nouveau. Le temporaire est voisin, et pas dans
    `/tmp`, parce que `os.replace` ne l'est qu'à l'intérieur d'un même point de
    montage.

    Le `fsync` distingue « les octets sont dans le cache du noyau » de « les
    octets sont sur le disque » : sans lui, une coupure d'alimentation peut
    rendre un fichier neuf et vide.

    Le mode, enfin, est celui de la destination et non du temporaire.
    `mkstemp` crée en 0600 — c'est sa raison d'être, personne ne doit lire un
    fichier à moitié écrit — et `os.replace` emporte ce mode avec les octets :
    sans correction, un modèle en 0644 repassait en 0600 à chaque
    enregistrement, et un modèle *neuf* naissait en 0600 là où l'umask de la
    session aurait donné 0644. Un `dbt build` lancé par un autre compte, une
    CI, un conteneur d'un autre uid ne lisait plus ce que l'atelier venait
    d'écrire — et rien, pas même `git`, qui n'enregistre que le bit
    d'exécution, ne le signalait.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Le mode se lit avant l'écriture : après le `replace`, la destination est
    # le fichier neuf, et l'ancien mode n'existe plus nulle part. Seuls les
    # bits de permission sont repris : `setuid` et `setgid` ne veulent rien
    # dire sur un `.sql`, et les reconduire sur un fichier entièrement
    # réécrit serait leur donner plus de portée qu'ils n'en avaient.
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = _NEW_FILE_MODE
    tmp = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".pliq-tmp"
        )
        tmp = Path(tmp_name)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            # Sur le descripteur, et pas sur le chemin : le temporaire porte un
            # nom que personne d'autre ne connaît, mais `fchmod` ne peut pas se
            # tromper de fichier, là où `chmod` suit ce que le chemin désigne
            # au moment de l'appel. Un système de fichiers sans permissions —
            # une clé en FAT, un montage monté sans elles — refuse l'appel :
            # c'est une écriture qui garde son mode d'avant, pas un
            # enregistrement perdu.
            with contextlib.suppress(OSError):
                os.fchmod(f.fileno(), mode)
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink()


def write_text_atomically(path: Path, content: str) -> None:
    """Même geste, sur du texte UTF-8."""
    write_bytes_atomically(path, content.encode("utf-8"))


@contextlib.contextmanager
def file_lock(path: Path, timeout_s: float = 5.0):
    """Un verrou interprocessus le temps du bloc, sur un fichier voisin.

    Le registre des projets est lu, modifié en mémoire, réécrit. Deux
    écritures qui se croisent — un `scan` qui inscrit chaque projet trouvé
    pendant qu'un autre onglet en oublie un — et la dernière écrase la
    première : « mon projet a disparu de l'accueil », sans explication
    possible. Le verrou porte sur un fichier à part, et non sur le registre :
    `os.replace` remplace l'inode, ce qui relâcherait un verrou pris dessus.

    L'attente est bornée, et c'est délibéré : un `pliq` tué net, un montage
    réseau qui ne répond plus, et un verrou pris pour toujours figerait
    l'ouverture d'un projet — alors que ce qu'il protège n'est qu'une liste de
    chemins. Passé le délai, on écrit sans verrou : le pire qui puisse arriver
    est ce qui arrivait avant, une inscription perdue.

    Sur une plateforme sans `fcntl` — Windows —, le bloc s'exécute sans
    verrou plutôt que d'échouer : c'est la situation d'avant, pas une
    régression.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover — Windows
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    try:
        f = open(lock, "a+b")
    except OSError:
        yield
        return
    held = False
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
        yield
    finally:
        if held:
            with contextlib.suppress(OSError):
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()
