"""Present an approved DNS name over a pinned IP address.

Authorization stays on the literal IP and port. Most sites only answer under their own name
(virtual hosts, certificates issued to a name), so an operation may also carry the name the
site is known by. The connection still goes to the approved address; the name only decides
the TLS server name (SNI and the certificate check) and the ``Host`` header.
"""

from __future__ import annotations

import ipaddress
import re
from typing import TYPE_CHECKING, Any

import httpx


if TYPE_CHECKING:
    from strix.core.assessment_context import Operation


_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def validate_server_name(value: str) -> str:
    """Return ``value`` if it is a canonical lowercase DNS name, else raise ``ValueError``."""
    if not value or len(value) > 253 or value != value.lower() or value.endswith("."):
        raise ValueError("Server name must be a lowercase DNS name of at most 253 characters")
    labels = value.split(".")
    if not all(_LABEL.match(label) for label in labels) or labels[-1].isdigit():
        raise ValueError("Server name must be made of DNS labels and must not be an IP address")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError("Server name must not be an IP address")


def request_presentation(operation: Operation) -> tuple[dict[str, str], dict[str, Any]]:
    """Headers and httpx extensions that present the approved name over the pinned address."""
    name = operation.server_name
    if name is None:
        return {}, {}
    url = httpx.URL(operation.url)
    host = name if url.port is None else f"{name}:{url.port}"
    extensions: dict[str, Any] = {"sni_hostname": name} if url.scheme == "https" else {}
    return {"Host": host}, extensions
