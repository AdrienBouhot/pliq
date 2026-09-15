"""Sous quel nom l'atelier accepte d'être joint, et par quelle page.

Ces deux contrôles visent la même attaque : une page web qui essaierait de
parler à un atelier *local* depuis le navigateur de qui l'a ouvert. Ils ne
protègent de rien d'autre — voir `__main__.trusted_host`, qui refuse
d'écouter ailleurs que sur la boucle locale.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

# Les noms sous lesquels on s'attend à joindre un atelier local. Les adresses
# littérales passent de toute façon par le contrôle d'IP ; ce qu'il faut nommer
# ici, c'est ce qui n'en est pas une. Voir `_host_allowed`.
LOCAL_HOSTS = frozenset({"localhost", "localhost.localdomain"})


def _host_and_port(value: str) -> str:
    """`host:port` en minuscules, sans le schéma ni le chemin."""
    return (value or "").strip().lower()


def _hostname(host_and_port: str) -> str:
    """Le nom seul, port retiré, crochets d'IPv6 compris.

    Une IPv6 porte ses propres deux-points : `[::1]:8765` ne se coupe pas au
    dernier `:` comme `localhost:8765`, c'est le crochet fermant qui borne le
    nom. Sans ça, `::1` se retrouvait traité comme un nom de machine ordinaire.
    """
    host = host_and_port.strip()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end > 0 else host[1:]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _host_allowed(host_header: str, expected: str) -> bool:
    """L'atelier accepte-t-il d'être joint sous ce nom d'hôte ?

    C'est la parade au réattachement DNS : un nom que l'attaquant contrôle,
    qu'il fait pointer sur 127.0.0.1, devient « même origine » que l'atelier
    pour le navigateur de la victime — et le contrôle d'origine ci-dessous
    tombe avec, puisque l'origine correspond alors vraiment à l'hôte.

    Ce que cette attaque exige, c'est un nom que le DNS public sait résoudre :
    l'attaquant doit pouvoir en changer la réponse. C'est donc là que passe la
    frontière, et pas ailleurs — refuser tout ce qui n'est pas `localhost`
    interdirait de joindre l'atelier par le nom de sa propre machine, ce qui
    est un usage ordinaire et non une attaque.

    Sont admis : les adresses IP littérales, qu'aucun DNS ne détourne ; les
    noms de bouclage ; les noms d'une seule étiquette (`macbook`) et les `.local`
    (mDNS), qui ne se résolvent pas sur le DNS public ; et le nom que `--host` a
    explicitement demandé, pour qui sert l'atelier derrière un nom choisi. Un
    `Host` absent passe : quelques outils n'en envoient pas, un navigateur en
    envoie toujours un.
    """
    host = _host_and_port(host_header)
    if not host:
        return True
    name = _hostname(host)
    if not name or name in LOCAL_HOSTS or name == _hostname(_host_and_port(expected)):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    labels = name.rstrip(".").split(".")
    return len(labels) == 1 or labels[-1] == "local"


def _same_origin(origin: str, host_header: str) -> bool:
    """L'origine annoncée est-elle la page que l'atelier a lui-même servie ?

    Les requêtes WebSocket échappent à CORS par construction : le navigateur
    ouvre la connexion et la laisse parler, quelle que soit la page qui la
    demande. Une page quelconque pouvait donc, depuis le navigateur de qui a
    l'atelier ouvert, écouter l'état des runs et les journaux dbt — qui portent
    des noms de ressources et des messages d'erreur du projet.

    Une origine absente passe : un navigateur en envoie toujours une sur un
    WebSocket, donc son absence désigne un client qui n'est pas un navigateur —
    et celui-là forgerait l'en-tête aussi bien. La refuser ne protégerait de
    personne et casserait les scripts légitimes.
    """
    if not origin:
        return True
    try:
        u = urlsplit(origin)
    except ValueError:
        return False
    if u.scheme not in ("http", "https"):
        return False
    return bool(u.netloc) and u.netloc.lower() == _host_and_port(host_header)
