"""The direct lane: bare ``asimov.io`` protobufs over UDP to the edge's ``UdpConnector``.

This is the edge's own external-client protocol (asimov-edge ``connectors/udp_connector.py``,
opt-in with ``--udp-control``). Speaking it makes the SDK the fifth connector beside BLE,
cloud, RF and the manager: the same ``RobotCommand``, the same arbiter, the same safety
layer, the same RSL gate when the robot has one.

The wire::

    commands  ->  UDP <host>:8850    one serialized asimov.io.RobotCommand per datagram
    state     <-  UDP :8851          one serialized asimov.io.RobotState per datagram,
                                     pushed by the edge at the firmware's telemetry rate

No framing, no acks. Two facts about the connector decide the shape of this class:

* It discards the sender address (``data, _ = recvfrom``) and pushes state to ONE
  configured destination (``--udp-state-host``). So the machine running this code has to
  be named when the edge starts, and this class binds that port and listens. One socket
  does both jobs, the way asimov-manager's own edge bridge does it.
* It answers nothing per command. Verdicts are an edge addition still to come; until
  then ``subscribe_outcome`` is honoured and never fires, and every ``Sent.wait_outcome``
  on this lane returns ``Unknown``.
"""

from __future__ import annotations

import contextlib
import logging
import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from asimov_sdk import _proto, robots
from asimov_sdk._command import Command, ModeCommand, Trajectory, Velocity
from asimov_sdk._errors import ConnectFailed, LinkLost, Unsupported
from asimov_sdk._media import AudioChunk
from asimov_sdk._outcome import Applied, Refused
from asimov_sdk._state import Alert, Battery, Joint, Mode, State
from asimov_sdk._state import Transport as TransportKind
from asimov_sdk.transport.base import (
    AudioCallback,
    ControllerCallback,
    FrameCallback,
    OutcomeCallback,
    StateCallback,
)

log = logging.getLogger("asimov_sdk.transport.udp")

#: ``--udp-control-port`` default on the edge.
COMMAND_PORT = 8850
#: ``--udp-state-port`` default on the edge.
STATE_PORT = 8851


def _pb() -> tuple[Any, Any, Any]:
    """The generated bindings, imported lazily so importing the SDK never needs protobuf
    until a transport is actually opened (and so the error names the fix)."""
    try:
        b = _proto.load()
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise ConnectFailed(
            "protobuf is not installed; it is the SDK's only runtime dependency "
            "(`pip install protobuf>=5.29.3`)."
        ) from exc
    return b.command, b.common, b.state


def state_from_robot_state(msg: Any, joint_names: tuple[str, ...] | None) -> State:
    """``asimov.io.RobotState`` -> :class:`State`. Pure; shared with tests."""
    n = len(msg.joint_pos)
    names = joint_names if joint_names and len(joint_names) == n else None
    vel, cur, temp = msg.joint_vel, msg.joint_current, msg.joint_temp
    joints = tuple(
        Joint(
            name=names[i] if names else "",
            pos=float(msg.joint_pos[i]),
            vel=float(vel[i]) if i < len(vel) else None,
            current=float(cur[i]) if i < len(cur) else None,
            temp=float(temp[i]) if i < len(temp) else None,
        )
        for i in range(n)
    )
    g = tuple(float(x) for x in msg.projected_gravity)
    w = tuple(float(x) for x in msg.base_ang_vel)
    q = tuple(float(x) for x in msg.base_quat)
    return State(
        mode=Mode.from_wire(msg.current_mode),
        joints=joints,
        gravity=(g[0], g[1], g[2]) if len(g) == 3 else None,
        gyro=(w[0], w[1], w[2]) if len(w) == 3 else None,
        quat=(q[0], q[1], q[2], q[3]) if len(q) == 4 else None,
        error_flags=int(msg.error_flags),
        alerts=tuple(
            Alert(
                id=int(a.id),
                severity=int(a.severity),
                value=float(a.value),
                threshold=float(a.threshold),
                source_id=int(a.source_id),
            )
            for a in msg.active_alerts
        ),
        battery=_battery_from(msg),
        sequence=int(msg.sequence),
        fw_timestamp_us=int(msg.timestamp_us),
        protocol_version=int(msg.protocol_version),
    )


def _battery_from(msg: Any) -> Battery | None:
    """``RobotState.battery`` (field 28). The firmware leaves it absent or all-zero when no
    BMS is fitted; both mean "not reported" here, never a 0 V pack."""
    if not msg.HasField("battery"):
        return None
    b = msg.battery
    if not (b.voltage_v or b.current_a or b.soc_percent or b.max_cell_temp_c or b.protection_flags):
        return None
    return Battery(
        voltage_v=float(b.voltage_v),
        current_a=float(b.current_a),
        soc_percent=float(b.soc_percent),
        max_cell_temp_c=float(b.max_cell_temp_c),
        protection_flags=int(b.protection_flags),
    )


class UdpTransport:
    """RobotCommand out, RobotState in. See the module docstring for the contract."""

    kind: TransportKind = "direct"
    default_outcome_timeout: float = 0.5
    #: The UDP lane carries commands and state only. Battery rides inside RobotState and is
    #: reported per robot (see RobotInfo.capabilities); media is not on this wire.
    capabilities: frozenset[str] = frozenset({"drive", "state"})

    def __init__(
        self,
        host: str,
        *,
        command_port: int = COMMAND_PORT,
        state_bind: tuple[str, int] = ("0.0.0.0", STATE_PORT),
        joint_names: Callable[[int], tuple[str, ...] | None] = robots.joint_names_for,
        state_source: str | None = None,
    ) -> None:
        self._host, self._port = host, int(command_port)
        self._state_source = state_source  # optional allowlist: the one address state may come from
        self._state_source_ip: str | None = None
        self._addr: tuple[str, int] | None = None  # resolved once, in open()
        self._bind = (state_bind[0], int(state_bind[1]))
        self._joint_names = joint_names
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
            raise ConnectFailed("this UdpTransport is already open")  # never leak a socket+thread
        _pb()  # fail here, with the install hint, not in the reader thread
        # Resolve the robot's name ONCE. sendto() with a hostname re-resolves on every
        # datagram — ten mDNS lookups a second under the keepalive, each able to stall
        # damp()/stop() behind a slow resolver.
        resolving = self._host
        try:
            self._addr = (socket.gethostbyname(self._host), self._port)
            if self._state_source is not None:
                resolving = self._state_source
                self._state_source_ip = socket.gethostbyname(self._state_source)
        except OSError as exc:
            raise ConnectFailed(f"cannot resolve {resolving!r}: {exc}") from exc
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(self._bind)
        except OSError as exc:
            sock.close()
            raise ConnectFailed(
                f"could not bind the state port {self._bind[0]}:{self._bind[1]}: {exc}. "
                "Another SDK process on this machine is already listening, or a stale one "
                "is still running. Pass a different state_bind and start the edge with the "
                "matching --udp-state-port."
            ) from exc
        sock.settimeout(0.2)
        self._sock = sock
        self._stop.clear()
        self._reader = threading.Thread(
            target=self._read_states, name="asimov-sdk-udp-state", daemon=True
        )
        self._reader.start()

    def close(self) -> None:
        self._stop.set()
        if self._reader is not None:
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
        # Honoured, never fired: the UdpConnector has no outcome channel yet.
        self._on_outcome.append(callback)

    def subscribe_controller_change(self, callback: ControllerCallback) -> None:
        self._on_controller.append(callback)

    # ── in ───────────────────────────────────────────────────────────────────
    def _read_states(self) -> None:
        _, _, st = _pb()
        sock = self._sock
        while sock is not None and not self._stop.is_set():
            try:
                data, sender = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            if self._state_source_ip is not None and sender[0] != self._state_source_ip:
                log.debug("dropped a state datagram from %s (not the robot)", sender[0])
                continue
            msg = st.RobotState()
            try:
                msg.ParseFromString(data)
                state = state_from_robot_state(msg, self._joint_names(len(msg.joint_pos)))
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
            raise LinkLost("transport is closed")
        cmd, common, _ = _pb()
        msg = cmd.RobotCommand(protocol_version=robots.PROTOCOL_VERSION)
        if isinstance(command, Velocity):
            # mode=MOVE + policy: the shape the edge's own BLE connector and asimov-manager
            # send. The arbiter routes on HasField("policy").
            msg.mode = common.CONTROL_MODE_MOVE
            msg.command_control = common.COMMAND_CONTROL_POLICY
            msg.policy.vx = command.vx
            msg.policy.vy = command.vy
            msg.policy.vyaw = command.vyaw
        elif isinstance(command, ModeCommand):
            msg.mode = (
                common.CONTROL_MODE_STAND if command.mode == "stand" else common.CONTROL_MODE_DAMP
            )
        elif isinstance(command, Trajectory):
            msg.command_control = common.COMMAND_CONTROL_TRAJECTORY
            msg.all_trajectory.positions.extend(command.positions)
            if command.kp is not None:
                msg.all_trajectory.kp.extend(command.kp)
            if command.kd is not None:
                msg.all_trajectory.kd.extend(command.kd)
        else:  # pragma: no cover - the Command union is closed
            raise TypeError(f"unsupported command {command!r}")
        with self._lock:
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            seq = self._seq
        msg.sequence = seq
        # Load-bearing when the robot's RSL gate is on (5 s freshness window); harmless off.
        msg.timestamp_us = int(time.time() * 1_000_000)
        try:
            sock.sendto(msg.SerializeToString(), addr)
        except OSError as exc:
            raise LinkLost(f"send to {self.endpoint} failed: {exc}") from exc
        return seq

    # ── media: not carried on this wire ──────────────────────────────────────
    def subscribe_frames(self, callback: FrameCallback) -> None:
        raise Unsupported("camera", self.kind)

    def subscribe_audio(self, callback: AudioCallback) -> None:
        raise Unsupported("microphone", self.kind)

    def play_audio(self, chunk: AudioChunk) -> None:
        raise Unsupported("speaker", self.kind)

    # Test seam: a fake edge delivers verdicts here.
    def _deliver_outcome(self, outcome: Applied | Refused) -> None:
        for cb in tuple(self._on_outcome):
            cb(outcome)
