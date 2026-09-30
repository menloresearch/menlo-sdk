"""Ask Asimov Manager for a LiveKit token to join the robot's room. Shared by this folder.

Set MANAGER_URL to the robot's Asimov Manager. The SDK credential comes from the Developer
page of Asimov Manager; the scripts read it from MENLO_CREDENTIAL so it stays out of files.
Run: python examples/livekit_raw/manager_token.py (prints the room and identity)
"""

import json
import os
import sys
import urllib.request
from typing import Any
from urllib.parse import urlsplit

MANAGER_URL = "http://192.168.22.32"
CREDENTIAL = os.environ.get("MENLO_CREDENTIAL", "")


# region main
class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: urllib would send the credential on to the new address."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def fetch_grant(manager: str = MANAGER_URL, credential: str = CREDENTIAL) -> dict[str, Any]:
    """The LiveKit URL, the robot's room and a join token, from Asimov Manager."""
    if not credential:
        sys.exit("Set MENLO_CREDENTIAL to an SDK credential from Asimov Manager.")
    request = urllib.request.Request(
        manager.rstrip("/") + "/api/livekit/token",
        data=b"{}",
        method="POST",
        headers={"Authorization": f"Bearer {credential}", "Content-Type": "application/json"},
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=5) as response:
        grant: dict[str, Any] = json.load(response)  # url, room, token, identity, role
    # The URL is the one the robot uses itself; localhost there is Asimov Manager's host.
    url = urlsplit(grant["url"])
    if url.hostname in ("localhost", "127.0.0.1"):
        host = urlsplit(manager).hostname or ""
        netloc = host if url.port is None else f"{host}:{url.port}"
        grant["url"] = url._replace(netloc=netloc).geturl()
    return grant


# endregion

if __name__ == "__main__":
    grant = fetch_grant()
    print(f"room {grant['room']} at {grant['url']}, identity {grant.get('identity')}")
