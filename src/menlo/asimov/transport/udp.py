"""The ``udp`` connection mode: bare ``asimov.io`` protobufs over UDP to Asimov Edge.

This is Asimov Edge's own external-client protocol, opt-in with ``udp-control``. Commands
enter the same arbiter and safety layer as the robot's other controllers, below Asimov
Manager's cockpit and a paired gamepad in priority. Datagrams are not authenticated: any
host on the robot's network can send them.

The wire::

    commands  ->  UDP <host>:8850    one serialized asimov.io.RobotCommand per datagram
    state     <-  UDP :8851          one serialized asimov.io.RobotState per datagram,
                                     pushed by Asimov Edge at the firmware's telemetry rate

No framing, no acks. Two facts about the connector decide the shape of this class:

* It discards the sender address and pushes state to ONE configured destination
  (``udp-state-host``). So the machine running this code has to be named when Asimov Edge
  starts, and this class binds that port and listens. One socket does both jobs.
* It answers nothing per command: ``subscribe_outcome`` is honoured and never fires, and
  every ``Sent.wait_outcome`` returns ``Unknown``.
"""

from __future__ import annotations

import contextlib
import logging
import math
import socket
import threading

from menlo.asimov._command import Command
from menlo.asimov._errors import ConnectError, LinkLostError, NotConnectedError, UnsupportedError
from menlo.asimov._media import AudioChunk
from menlo.asimov._state import TransportKind
from menlo.asimov._version import check_target
from menlo.asimov.transport._wire import _pb, encode_command, state_from_robot_state
from menlo.asimov.transport.base import (
    AudioCallback,
    ControllerCallback,
    FrameCallback,
    OutcomeCallback,
    StateCallback,
)

log = logging.getLogger("menlo.asimov.transport.udp")

#: ``--udp-control-port`` default on Asimov Edge.
COMMAND_PORT = 8850
#: ``--udp-state-port`` default on Asimov Edge.
STATE_PORT = 8851
#: Edge's local HTTP system-information endpoint.
VERSION_PORT = 3000
#: Default request/response budget for the system-information endpoint.
VERSION_TIMEOUT_S = 5.0

__all__ = [
    "COMMAND_PORT",
    "STATE_PORT",
    "VERSION_PORT",
    "VERSION_TIMEOUT_S",
    "UdpTransport",
    "state_from_robot_state",
]


class UdpTransport:
    """RobotCommand out, RobotState in. See the module docstring for the contract."""

    kind: TransportKind = "udp"
    default_outcome_timeout: float = 0.5
    silence_hint: str = (
        "Is Asimov Edge running with udp-control on, and is its udp-state-host this machine? "
        "Asimov Edge also sends no state while the robot's firmware is not reporting."
    )
    #: UDP carries commands and state only. Battery rides inside RobotState and is
    #: reported per robot (see RobotInfo.capabilities); media is not on this wire.
    capabilities: frozenset[str] = frozenset({"drive", "state"})

    def __init__(
        self,
        host: str,
        *,
        command_port: int = COMMAND_PORT,
        state_bind: tuple[str, int] = ("0.0.0.0", STATE_PORT),
        state_source: str | None = None,
        version_port: int = VERSION_PORT,
        version_timeout: float = VERSION_TIMEOUT_S,
        allow_unsupported_target: bool = False,
    ) -> None:
        if not 1 <= version_port <= 65535:
            raise ValueError("version_port must be between 1 and 65535")
        if not math.isfinite(version_timeout) or version_timeout <= 0:
            raise ValueError("version_timeout must be finite and positive")
        self._host, self._port = host, int(command_port)
        self._state_source = state_source  # optional allowlist: the one address state may come from
        self._state_source_ip: str | None = None
        self._addr: tuple[str, int] | None = None  # resolved once, in open()
        self._bind = (state_bind[0], int(state_bind[1]))
        self._version_port = int(version_port)
        self._version_timeout = float(version_timeout)
        self._allow_unsupported_target = allow_unsupported_target
        self.endpoint = f"{host}:{command_port}"
        self._sock: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._seq = 0
        self._on_state: list[StateCallback] = []
        self._on_outcome: list[OutcomeCallback] = []
        self._on_controller: list[ControllerCallback] = []

    # ── lifecycle ────────────────────────────────────────────────────────────
    def open(self) -> None:
        if self._sock is not None:
            raise ConnectError("this UdpTransport is already open")  # never leak a socket+thread
        _pb()  # fail here, with the install hint, not in the reader thread
        # The UDP transport owns its compatibility gate. That keeps Robot(config),
        # Robot(UdpTransport(...)), and HybridTransport on the same path and ensures no
        # command socket opens before Edge has identified a supported target.
        check_target(
            self._host,
            self._version_port,
            self._version_timeout,
            allow_unsupported_target=self._allow_unsupported_target,
        )
        # Resolve the robot's name ONCE. sendto() with a hostname re-resolves on every
        # datagram: ten mDNS lookups a second under the keepalive, each able to stall
        # damp()/balance() behind a slow resolver.
        resolving = self._host
        try:
            self._addr = (socket.gethostbyname(self._host), self._port)
            if self._state_source is not None:
                resolving = self._state_source
                self._state_source_ip = socket.gethostbyname(self._state_source)
        except OSError as exc:
            raise ConnectError(f"cannot resolve {resolving!r}: {exc}") from exc
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(self._bind)
        except OSError as exc:
            sock.close()
            raise ConnectError(
                f"could not bind the state port {self._bind[0]}:{self._bind[1]}: {exc}. "
                "Another SDK process on this machine is already listening, or a stale one "
                "is still running. Pass a different state_bind and start Asimov Edge with the "
                "matching --udp-state-port."
            ) from exc
        sock.settimeout(0.2)
        self._sock = sock
        # One stop flag per open, handed to its reader with its socket: close() may run on
        # the reader thread (a state callback that reconnects), and an open() that cleared a
        # shared flag would revive that reader on a closed socket beside the new one.
        stop = threading.Event()
        self._stop = stop
        self._reader = threading.Thread(
            target=self._read_states, args=(sock, stop), name="menlo-sdk-udp-state", daemon=True
        )
        self._reader.start()

    def close(self) -> None:
        self._stop.set()
        if self._reader is not None:
            if self._reader is not threading.current_thread():
                self._reader.join(timeout=2.0)
            self._reader = None
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.close()
            self._sock = None

    # ── subscriptions ────────────────────────────────────────────────────────
    def subscribe_state(self, callback: StateCallback) -> None:
        self._on_state.append(callback)

    def subscribe_outcome(self, callback: OutcomeCallback) -> None:
        # Honoured, never fired: the UdpConnector has no outcome channel.
        self._on_outcome.append(callback)

    def subscribe_controller_change(self, callback: ControllerCallback) -> None:
        self._on_controller.append(callback)

    # ── in ───────────────────────────────────────────────────────────────────
    def _read_states(self, sock: socket.socket, stop: threading.Event) -> None:
        _, _, st = _pb()
        while not stop.is_set():
            try:
                data, sender = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                if stop.is_set():
                    return
                continue
            if self._state_source_ip is not None and sender[0] != self._state_source_ip:
                log.debug("dropped a state datagram from %s (not the robot)", sender[0])
                continue
            msg = st.RobotState()
            try:
                msg.ParseFromString(data)
                state = state_from_robot_state(msg, None)  # Robot names the joints
            except Exception:  # a bad datagram (or joint table) must not kill the reader
                log.debug("dropped an undecodable datagram (%d bytes)", len(data), exc_info=True)
                continue
            for cb in tuple(self._on_state):
                try:
                    cb(state)
                except Exception:  # one bad subscriber must not stop the stream
                    log.exception("state subscriber raised")

    # ── out ──────────────────────────────────────────────────────────────────
    def send(self, command: Command) -> int:
        sock, addr = self._sock, self._addr
        if sock is None or addr is None:
            raise NotConnectedError("this UdpTransport is not open")
        with self._lock:
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            seq = self._seq
        try:
            sock.sendto(encode_command(command, seq), addr)
        except OSError as exc:
            raise LinkLostError(f"send to {self.endpoint} failed: {exc}") from exc
        return seq

    # ── media: not carried on this wire ──────────────────────────────────────
    def subscribe_frames(self, callback: FrameCallback) -> None:
        raise UnsupportedError("camera", self.kind)

    def subscribe_audio(self, callback: AudioCallback) -> None:
        raise UnsupportedError("microphone", self.kind)

    def play_audio(self, chunk: AudioChunk) -> None:
        raise UnsupportedError("speaker", self.kind)
