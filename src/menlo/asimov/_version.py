"""Read Edge target facts and enforce the SDK's supported Robot OS lines."""

from __future__ import annotations

import http.client
import json
import re
import socket
import threading
import time
from contextlib import suppress
from http.client import HTTPConnection

from menlo.asimov._errors import ConnectError

SUPPORTED_TARGETS = frozenset({("asimov_1", 0, 2)})
_MAX_RESPONSE_BYTES = 4096
_VERSION_NUMBER = r"(?:0|[1-9][0-9]*)"
_ROBOT_OS_VERSION = re.compile(
    rf"(?P<major>{_VERSION_NUMBER})\.(?P<minor>{_VERSION_NUMBER})\."
    rf"(?P<patch>{_VERSION_NUMBER})"
    rf"(?:\.dev\.[0-9]{{12}}\.[0-9a-f]{{7,40}}(?:\.dirty\.[0-9]{{12}})?|"
    rf"(?:[-.]?(?:rc|dev|a|b|alpha|beta))\.?{_VERSION_NUMBER})?"
)


def check_target(host: str, port: int, timeout: float, *, allow_unsupported_target: bool) -> None:
    """Reject an unsupported LAN target before the command/state socket opens."""
    endpoint = f"http://{host}:{port}/api/version"
    document = _fetch_version_document(host, port, timeout, endpoint)
    check_target_document(
        document,
        endpoint,
        allow_unsupported_target=allow_unsupported_target,
    )


def check_target_document(
    document: bytes,
    endpoint: str,
    *,
    allow_unsupported_target: bool,
) -> None:
    """Apply the SDK's target policy to facts returned by any transport adapter."""
    if len(document) > _MAX_RESPONSE_BYTES:
        raise ConnectError(f"robot version at {endpoint} exceeds {_MAX_RESPONSE_BYTES} bytes")
    model, version, major, minor = _parse_target_facts(document, endpoint)

    # The override waives SDK support policy only. Fetching and parsing always fail closed.
    if allow_unsupported_target:
        return

    target = (model, major, minor)
    if target in SUPPORTED_TARGETS:
        return

    raise ConnectError(
        f"unsupported robot target {model} Robot OS {version}; supported targets: "
        f"{sorted(SUPPORTED_TARGETS)}. Update the SDK or robot, or explicitly use "
        "allow_unsupported_target=True for development at your own risk."
    )


def _fetch_version_document(host: str, port: int, timeout: float, endpoint: str) -> bytes:
    """Fetch one bounded response within one deadline, including a trickling body."""
    # HTTPConnection contacts the configured robot directly: it neither uses environment
    # proxies nor follows redirects to a different host.
    connection = HTTPConnection(host, port, timeout=timeout)
    deadline = time.monotonic() + timeout
    response: http.client.HTTPResponse | None = None
    deadline_socket: socket.socket | None = None
    expiry: threading.Timer | None = None

    try:
        connection.connect()
        assert connection.sock is not None
        read_socket = connection.sock

        # HTTP/1.0 transfers the read socket to its response object. A duplicate descriptor
        # lets the deadline interrupt that same connection regardless of current ownership.
        deadline_socket = read_socket.dup()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("robot version connection deadline expired")
        expiry = threading.Timer(
            remaining,
            _interrupt_socket,
            args=(deadline_socket,),
        )
        expiry.daemon = True
        expiry.start()

        connection.request("GET", "/api/version", headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ConnectError(f"robot version endpoint {endpoint} returned HTTP {response.status}")

        document = _read_response_body(response, read_socket, deadline, endpoint)
    except (OSError, http.client.HTTPException) as exc:
        raise ConnectError(f"could not read robot version at {endpoint}: {exc}") from exc
    finally:
        if expiry is not None:
            expiry.cancel()
            # No deadline worker may outlive the socket objects it can interrupt.
            expiry.join()
        if response is not None:
            response.close()
        connection.close()
        if deadline_socket is not None:
            deadline_socket.close()

    return document


def _interrupt_socket(sock: socket.socket) -> None:
    """Wake a blocked header or body read when the whole request deadline expires."""
    with suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)


def _read_response_body(
    response: http.client.HTTPResponse,
    read_socket: socket.socket,
    deadline: float,
    endpoint: str,
) -> bytes:
    """Read at most one document while keeping every chunk inside the original deadline."""
    document = bytearray()
    while len(document) <= _MAX_RESPONSE_BYTES:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("robot version response deadline expired")

        read_socket.settimeout(remaining)
        chunk = response.read1(_MAX_RESPONSE_BYTES + 1 - len(document))
        if not chunk:
            if response.length not in (None, 0):
                raise ConnectError(f"truncated robot version response at {endpoint}")
            break

        document.extend(chunk)
        # A complete Content-Length body needs no second read on an HTTP/1.0 socket.
        if response.length == 0:
            break

    return bytes(document)


def _parse_target_facts(document: bytes, endpoint: str) -> tuple[str, str, int, int]:
    """Validate the untrusted Edge document and return its canonical target facts."""
    try:
        payload = json.loads(document, object_pairs_hook=_unique_facts)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ConnectError(f"malformed robot version JSON at {endpoint}") from exc

    if not isinstance(payload, dict):
        raise ConnectError(f"robot version at {endpoint} must be a JSON object")

    model = payload.get("robot_model")
    if not isinstance(model, str):
        raise ConnectError(f"invalid robot_model at {endpoint}")
    if not model or model.strip() != model:
        raise ConnectError(f"invalid robot_model at {endpoint}")

    version = payload.get("robot_os_version")
    if not isinstance(version, str):
        raise ConnectError(f"missing or invalid robot_os_version at {endpoint}")

    match = _ROBOT_OS_VERSION.fullmatch(version)
    if match is None:
        raise ConnectError(f"invalid robot_os_version {version!r} at {endpoint}")

    major = int(match["major"])
    minor = int(match["minor"])
    return model, version, major, minor


def _unique_facts(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate JSON keys instead of letting the last identity silently win."""
    facts: dict[str, object] = {}
    for key, value in pairs:
        if key in facts:
            raise ValueError(f"duplicate target fact {key!r}")
        facts[key] = value
    return facts
