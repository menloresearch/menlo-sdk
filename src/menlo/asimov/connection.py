"""Where a robot is: one typed config per connection, and the connection mode is chosen at
``connect()``.

    cfg = ConnectionConfig(
        udp=UdpConfig(host="10.0.0.5"),
        livekit=ManagerConfig(url="http://10.0.0.5:8080", credential=CREDENTIAL),
    )
    robot = Robot(cfg)          # bound, nothing on the network
    robot.connect("hybrid")     # attach now; close() and connect("udp") later on the same Robot

Nothing here opens a socket. A ``ConnectionConfig`` can be built from a config file, kept by a
long-running session and reused for as many connects as needed. Each connection's fields live
on their own class so an editor suggests exactly what it needs: ``UdpConfig`` has no
token, ``LiveKitConfig`` has no host, ``ManagerConfig`` is a manager URL and a credential.

The ``livekit`` slot takes either the LiveKit details themselves or a ``ManagerConfig``. With
the manager form the SDK asks the robot's manager for the URL, the room and a fresh join
token on every ``connect()``, so the caller never holds a LiveKit token and the LiveKit
secret never leaves the manager.
"""

from __future__ import annotations

import json
import re
import secrets
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, get_args
from urllib.parse import urlsplit, urlunsplit

from menlo.asimov._command import Limits
from menlo.asimov._errors import ConnectError
from menlo.asimov.transport._livekit_client import TokenProvider
from menlo.asimov.transport.base import Transport
from menlo.asimov.transport.livekit import MEDIA_TIMEOUT_S, HybridTransport, LiveKitTransport
from menlo.asimov.transport.udp import COMMAND_PORT, STATE_PORT, UdpTransport

#: The connection modes a ``Robot`` can connect on: ``udp`` (control and state over UDP, no
#: media), ``hybrid`` (control and state over UDP, camera and audio over LiveKit) and
#: ``livekit`` (everything through a LiveKit room). ``menlo.asimov.Mode`` is the robot mode
#: (DAMP/STAND/MOVE), a different thing.
ConnectMode = Literal["udp", "hybrid", "livekit"]
MODES: tuple[ConnectMode, ...] = get_args(ConnectMode)

#: A manager answer larger than this is not a token reply.
_MAX_REPLY_BYTES = 64 * 1024

#: Hosts a manager may name in the LiveKit URL it mints that mean "this machine" to the
#: ROBOT (the manager configures Asimov Edge, which runs beside the SFU) and nowhere else.
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})

#: The characters the manager keeps in a label (its ``sanitize()``: anything else becomes
#: a dash, the ends are stripped, the result lowercased). Applied here too, so the SDK's
#: default label arrives as it was made.
_LABEL_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")

#: Which ``ConnectionConfig`` slots each mode needs.
_SLOTS: dict[str, tuple[str, ...]] = {
    "udp": ("udp",),
    "hybrid": ("udp", "livekit"),
    "livekit": ("livekit",),
}


@dataclass(frozen=True)
class UdpConfig:
    """UDP on the robot's network: ``RobotCommand`` datagrams to ``host:command_port``,
    ``RobotState`` datagrams back to ``state_bind``. Asimov Edge must run with ``udp-control``
    on and send state to this machine (``udp-state-host``)."""

    host: str
    command_port: int = COMMAND_PORT
    state_bind: tuple[str, int] = ("0.0.0.0", STATE_PORT)
    #: Only accept state from this address; ``None`` accepts any (a robot behind NAT or in a
    #: container can send state from a different address than it listens on).
    state_source: str | None = None


@dataclass(frozen=True)
class LiveKitConfig:
    """LiveKit details you already hold: the SFU URL, the robot's room, and a join token (or
    a callable that mints one per join). For people running their own LiveKit server or
    writing tests; with a robot's Asimov Manager use :class:`ManagerConfig` instead."""

    url: str
    room: str
    token: TokenProvider = field(repr=False)  # a join grant; keep it out of logs and tracebacks

    def resolve(self) -> LiveKitConfig:
        return self


@dataclass(frozen=True)
class ManagerConfig:
    """Let the robot's Asimov Manager supply the LiveKit details. ``credential`` is an SDK
    credential from the Developer page of Asimov Manager; its role decides whether the
    session may drive and talk in the room (``control``) or only watch (``observe``). The role
    applies to the LiveKit room: on ``hybrid`` commands travel over UDP, which has no sign-in.

    ``url`` is the manager's address as a browser would type it: ``http://asimov.local``
    (port 80), ``http://10.0.0.5:8080``, or just ``10.0.0.5`` (``http://`` is assumed).
    """

    url: str
    credential: str = field(repr=False)  # a bearer secret; keep it out of logs and tracebacks
    #: Suffix for this session's room identity (``sdk-<credential id>-<label>``). LiveKit
    #: keys participants by identity and a second join with the same identity EVICTS the
    #: first, so two sessions on one credential must not share one. Left ``None``, every
    #: ``resolve()`` makes its own: ``<this host>-<6 random hex>``.
    label: str | None = None
    #: HTTP timeout for each request to the manager.
    timeout: float = 5.0

    def __post_init__(self) -> None:
        if not self.timeout > 0:
            raise ValueError(f"ManagerConfig.timeout must be positive, not {self.timeout!r}")
        url = self.url.strip()
        if not url:
            raise ValueError("ManagerConfig.url is empty")
        if "://" not in url:
            url = "http://" + url
        object.__setattr__(self, "url", url.rstrip("/"))

    @property
    def host(self) -> str:
        """The manager's hostname or address, without scheme or port."""
        return urlsplit(self.url).hostname or self.url

    def resolve(self) -> LiveKitConfig:
        """Ask the manager once for url + room + a token, and hand the transport a token
        callable: the first join uses that token, every later join mints a fresh one. Nothing
        minted is thrown away: each token is a live grant on the room.

        The identity is fixed for the session here: one label, whether the caller's or a
        fresh default, is sent with every mint, so a rejoin comes back as the same
        participant instead of evicting a second one."""
        label = self.label if self.label is not None else default_label()
        first = self._mint(label)
        unused = [str(first["token"])]

        def mint() -> str:
            if unused:
                return unused.pop()
            return str(self._mint(label)["token"])

        return LiveKitConfig(
            url=self._reachable(str(first["url"])), room=str(first["room"]), token=mint
        )

    def check(self) -> ManagerGrant:
        """Ask Asimov Manager for one token and report what it granted: whether the
        credential is accepted (a :class:`ConnectError` if not), its role and the robot's
        room. The token itself is not kept."""
        answer = self._mint(self.label if self.label is not None else default_label())
        role = answer.get("role")
        identity = answer.get("identity")
        return ManagerGrant(
            url=self._reachable(str(answer["url"])),
            room=str(answer["room"]),
            identity=str(identity) if identity else None,
            role=str(role) if role else None,
        )

    def _reachable(self, livekit_url: str) -> str:
        """The LiveKit URL as THIS machine can reach it. The manager reports the URL it
        gave Asimov Edge, and on a robot whose SFU runs beside Asimov Edge that is
        ``ws://localhost:7880``: correct on the robot, "connection refused" anywhere else.
        A loopback host is replaced by the manager's own host; scheme and port stay."""
        parts = urlsplit(livekit_url)
        if parts.hostname not in _LOOPBACK_HOSTS:
            return livekit_url
        host = self.host
        if ":" in host:  # a bare IPv6 address needs its brackets back in a URL
            host = f"[{host}]"
        netloc = f"{host}:{parts.port}" if parts.port is not None else host
        return urlunsplit(parts._replace(netloc=netloc))

    def _mint(self, label: str) -> dict[str, Any]:
        endpoint = self.url + "/api/livekit/token"
        body: dict[str, Any] = {"label": label} if label else {}
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.credential}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with _opener.open(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_REPLY_BYTES + 1)
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                # Never follow: urllib would carry the bearer credential to the new host.
                raise ConnectError(
                    f"the manager at {self.url} redirected to {exc.headers.get('Location')!r}; "
                    "use that address as ManagerConfig.url (the credential is only ever sent "
                    "to the URL you configured)"
                ) from exc
            detail = _error_detail(exc)
            raise ConnectError(
                f"the manager at {self.url} refused to mint a LiveKit token "
                f"(HTTP {exc.code}): {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectError(
                f"could not reach Asimov Manager at {self.url} ({exc}). Is it running there, "
                "and can this machine reach the robot's network?"
            ) from exc
        if len(raw) > _MAX_REPLY_BYTES:
            raise ConnectError(
                f"the manager at {self.url} answered with more than {_MAX_REPLY_BYTES} bytes; "
                "that is not a token reply. Is this Asimov Manager's URL?"
            )
        try:
            payload = json.loads(raw.decode() or "{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ConnectError(
                f"the manager at {self.url} did not answer with JSON "
                f"(Content-Type {content_type!r}); a login page or a proxy may be in the way. "
                "Is this Asimov Manager's URL?"
            ) from exc
        if not isinstance(payload, dict):
            raise ConnectError(
                f"the manager at {self.url} answered with a JSON {type(payload).__name__}, "
                "not an object; is this the manager's URL?"
            )
        missing = [k for k in ("url", "room", "token") if not payload.get(k)]
        if missing:
            raise ConnectError(
                f"the manager at {self.url} answered without {', '.join(missing)}; "
                "it may be too old to mint SDK tokens (it needs the Developer page)."
            )
        return dict(payload)


@dataclass(frozen=True)
class ManagerGrant:
    """What Asimov Manager answered for a credential (:meth:`ManagerConfig.check`)."""

    #: The LiveKit URL, as this machine reaches it.
    url: str
    #: The robot's room, ``robot-<serial>``.
    room: str
    #: The identity the SDK joins as; ``None`` when the manager does not say.
    identity: str | None
    #: ``"control"`` (may drive) or ``"observe"`` (may watch); ``None`` when the manager
    #: does not say.
    role: str | None


def default_label() -> str:
    """The label a session gets when its caller set none: ``<host>-<6 hex>``, sanitized
    the manager's way. Random per call, so two connects on one credential (two scripts,
    or one script restarted while the previous participant is still timing out) never
    present the same identity and never evict each other. Short enough to fit the
    manager's 64-character identity limit behind ``sdk-<credential id>-``."""
    host = _LABEL_UNSAFE.sub("-", socket.gethostname()).strip("-").lower()[:16].strip("-")
    suffix = secrets.token_hex(3)
    return f"{host}-{suffix}" if host else suffix


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: urllib re-sends the ``Authorization`` header to the new URL."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(_MAX_REPLY_BYTES).decode()
        data = json.loads(raw)
        if isinstance(data, dict):
            return str(data.get("error") or data.get("detail") or raw)
        return raw
    except Exception:
        return exc.reason if isinstance(exc.reason, str) else str(exc.reason)


LiveKitSource = LiveKitConfig | ManagerConfig


@dataclass(frozen=True)
class ConnectionConfig:
    """Everything known about how to reach one robot. Leave a slot ``None`` when the robot
    (or this machine) has no such connection; :meth:`available_modes` says what is left."""

    udp: UdpConfig | None = None
    livekit: LiveKitSource | None = None
    #: The velocity clamp a ``Robot`` bound to this config uses when it is given none
    #: (a saved robot's ``[robots.NAME.limits]``). ``None`` leaves the default.
    limits: Limits | None = None
    #: The connection mode ``connect()`` uses when called without one. ``None``: the only
    #: mode the slots allow.
    mode: ConnectMode | None = None
    #: The saved robot this config was read from, if any.
    name: str | None = None
    #: What to tell the user when ``connect(mode)`` needs a slot this config lacks, keyed
    #: by slot (``"udp"``, ``"livekit"``): the command or variable that fills it.
    hints: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.mode is not None and self.mode not in MODES:
            raise ValueError(f"unknown connection mode {self.mode!r}; one of {', '.join(MODES)}")

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None, *, robot: str | None = None
    ) -> ConnectionConfig:
        """The config ``Robot()`` uses when handed none: the ``MENLO_*`` connection variables,
        else the saved robot (``robot``, else ``MENLO_ROBOT``, else the default). A
        :class:`ConnectError` names every way to configure one when nothing does. See
        :mod:`menlo.asimov.store`."""
        from menlo.asimov.store import resolve_connection

        return resolve_connection(environ, robot=robot)

    def available_modes(self) -> tuple[ConnectMode, ...]:
        """The modes this config can connect on, given which slots are set."""
        return tuple(
            mode for mode in MODES if all(getattr(self, s) is not None for s in _SLOTS[mode])
        )

    def default_mode(self) -> ConnectMode:
        """What ``connect()`` uses when given no mode: :attr:`mode`, else :meth:`only_mode`."""
        return self.mode if self.mode is not None else self.only_mode()

    def only_mode(self) -> ConnectMode:
        """The one mode this config can connect on. ``ValueError`` when the choice is not
        the config's to make."""
        modes = self.available_modes()
        if len(modes) == 1:
            return modes[0]
        if not modes:
            raise ConnectError("this ConnectionConfig has no connection set; nothing to connect on")
        raise ValueError(
            f"this ConnectionConfig can connect on {', '.join(modes)}; pass connect(mode)"
            " or set its mode"
        )

    def transport_for(
        self,
        mode: ConnectMode,
        *,
        media_timeout: float = MEDIA_TIMEOUT_S,
        connect_timeout: float = 10.0,
    ) -> Transport:
        """Build the transport for ``mode``. Nothing is opened; with a :class:`ManagerConfig`
        the manager IS asked here, because the transport needs the room name up front."""
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}; one of {', '.join(MODES)}")
        missing = [slot for slot in _SLOTS[mode] if getattr(self, slot) is None]
        if missing:
            fixes = [self.hints[slot] for slot in missing if slot in self.hints]
            if fixes:
                raise ConnectError(f"connect({mode!r}): " + "; ".join(fixes))
            have = ", ".join(self.available_modes()) or "nothing"
            raise ConnectError(
                f"connect({mode!r}) needs the {' and '.join(missing)} slot"
                f"{'s' if len(missing) > 1 else ''} of the ConnectionConfig; "
                f"this one can connect on: {have}"
            )
        if mode == "udp":
            assert self.udp is not None
            return UdpTransport(
                self.udp.host,
                command_port=self.udp.command_port,
                state_bind=self.udp.state_bind,
                state_source=self.udp.state_source,
            )
        assert self.livekit is not None
        lk = self.livekit.resolve()
        if mode == "livekit":
            return LiveKitTransport(
                lk.url,
                lk.room,
                token=lk.token,
                media_timeout=media_timeout,
                connect_timeout=connect_timeout,
            )
        assert self.udp is not None
        return HybridTransport(
            self.udp.host,
            livekit_url=lk.url,
            room=lk.room,
            token=lk.token,
            command_port=self.udp.command_port,
            state_bind=self.udp.state_bind,
            state_source=self.udp.state_source,
            media_timeout=media_timeout,
            connect_timeout=connect_timeout,
        )


__all__ = [
    "MODES",
    "ConnectMode",
    "ConnectionConfig",
    "LiveKitConfig",
    "LiveKitSource",
    "ManagerConfig",
    "ManagerGrant",
    "UdpConfig",
    "default_label",
]
