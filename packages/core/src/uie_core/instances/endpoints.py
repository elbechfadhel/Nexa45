"""Garde des adresses saisies par un fournisseur (V2b.1, décision E3).

Quand un fournisseur connecte son système, le serveur UIE se connecte à l'URL qu'il a saisie : sans garde, un
tenant pourrait sonder le réseau interne de la plateforme (SSRF). Règle : http / https uniquement, et aucune
adresse interne (loopback, réseaux privés, link-local, réservées) sauf `UIE_ALLOW_PRIVATE_ENDPOINTS=true`
(développement : customer17 sur localhost:8027). Les adresses saisies par l'Admin ne passent pas par ici.
Limite connue : la résolution DNS est vérifiée au moment de la saisie (pas de protection contre un DNS qui
changerait ensuite de réponse).
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

from uie_core.registry.service import RegistryError

ALLOW_PRIVATE_ENV = "UIE_ALLOW_PRIVATE_ENDPOINTS"


class EndpointNotAllowed(RegistryError):
    """Adresse refusée pour un fournisseur (→ HTTP 400, message lisible)."""


def private_endpoints_allowed() -> bool:
    return os.environ.get(ALLOW_PRIVATE_ENV, "").strip().lower() in {"1", "true", "yes"}


def check_endpoint(url: str, *, allow_private: bool) -> None:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise EndpointNotAllowed("Enter the full address of your server, starting with https:// or http://")
    if allow_private:
        return
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return  # nom inconnu : le test de connexion dira que le serveur est injoignable
    for info in infos:
        ip = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise EndpointNotAllowed(
                "This address is not reachable from GO45: use the public address of your server"
            )
