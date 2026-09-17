"""Drive an Asimov from Python.

::

    from asimov_sdk import ConnectionConfig, Mode, Robot, UdpConfig

    cfg = ConnectionConfig(udp=UdpConfig("asimov.local"))
    with Robot(cfg).connect("udp") as robot:
        if robot.state.mode is Mode.DAMP:          # STAND is the wake-up verb only
            robot.stand()
            robot.wait_for(Mode.STAND, timeout=8.0)
        robot.set_velocity(vx=0.25, duration=4.0)   # held for 4 s, then zero
        robot.wait_for(Mode.MOVE)
        # Leaving the block zeroes the velocity, as the hold ending would. Either way the
        # robot stays in MOVE at zero velocity, where the walking policy keeps balancing
        # it — that IS how a free-standing biped stands still. Do not ask for STAND here:
        # STAND stiffens to a fixed pose with no balance loop, and a robot nothing is
        # holding tips over.

The verbs are the wire's verbs — ``set_velocity``, ``stand``, ``damp``, ``stop``,
``trajectory`` — and every one returns immediately with a :class:`~asimov_sdk.Sent`.
Two questions are kept apart on purpose:

* **Was it admitted?** ``sent.wait_outcome()`` — the arbiter's verdict, when the edge
  reports one (today: ``Unknown``; see :mod:`asimov_sdk._outcome`).
* **Did it take effect?** ``robot.wait_for(Mode.STAND)`` / ``robot.wait_until(pred)`` —
  read from the robot's own state stream, never inferred from what we sent.

Behaviours worth knowing before the first script:

* **A velocity is held.** ``set_velocity`` latches and a background thread re-sends it
  at 10 Hz, so you can spend most of a second on vision between calls and the robot keeps
  walking. That thread feeds nothing on the robot; it holds off the edge's own two-second
  velocity watchdog, which is the real safety net. Pass ``duration=`` to bound the hold.
* **Mode commands are one-shot.** STAND and DAMP are events the firmware latches itself;
  repeating them would let a script out-shout an operator's DAMP.
* **Zero velocity is not STAND, and it is usually what you want.** After ``stop()`` (or a
  ``duration`` ending) the firmware stays in MOVE with zero velocity — the walking policy,
  balancing in place. STAND is a different thing: it stiffens every joint to a fixed
  pose and runs **no balance loop**, so a free-standing biped asked to stiffen after
  walking tips over. ``stand()`` is for waking a robot up (DAMP -> STAND -> MOVE), or for
  one that is held, craned or on its stand — never for finishing a walk.
* **Your own setpoint loop owns the robot; ``goto()`` does not.** The firmware obeys
  whichever command arrived last. A ``goto()`` is fenced: any verb from any thread —
  ``damp()``, ``stand()``, a velocity — bumps the generation and the goto thread stops
  before its next setpoint leaves. A loop you clock yourself with ``trajectory()`` is
  not fenced: a mode verb sent from another thread is overwritten by your next setpoint.
  Measured: a ``damp()`` fired into a hand-rolled 50 Hz trajectory loop left the robot in
  MOVE and upright, exactly as if it had never been sent. Stop your loop first, then
  send the verb. For an emergency, kill the process: the edge DAMPs by itself about two
  seconds after the last setpoint, and that path does not depend on your loop still
  working.
* **``close()`` zeroes velocity first**, then drops the link, so leaving the ``with``
  block — including by exception — leaves the robot standing still, not walking.
* **``damp()`` is the emergency verb.** A standing biped folds. It raises on a dead link
  like every other verb; ``close()`` is the one that swallows.
"""

from __future__ import annotations

import collections
import dataclasses
import logging
import math
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING, Literal, Self

from asimov_sdk import robots
from asimov_sdk._command import Command, Limits, ModeCommand, Trajectory, Velocity
from asimov_sdk._errors import (
    AsimovError,
    CommandRefusedError,
    ConnectError,
    LinkLostError,
    NotConnectedError,
    ProtocolMismatchError,
    RobotFaultedError,
    StateStaleError,
    UnsupportedError,
    WaitTimeoutError,
)
from asimov_sdk._media import Camera, Microphone, Speaker
from asimov_sdk._outcome import Applied, Refused, Sent
from asimov_sdk._state import Alert, Capability, Mode, RobotInfo, State
from asimov_sdk.connection import ConnectionConfig, ConnectMode
from asimov_sdk.recording import Recording
from asimov_sdk.transport.base import Transport

# Importing the LiveKit transports does NOT import livekit: every `livekit` import in the
# SDK is lazy, inside `transport/_livekit_client.py`. `connect("udp")` never touches it.
from asimov_sdk.transport.livekit import MEDIA_TIMEOUT_S

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


def _positive_finite(name: str, value: float) -> float:
    if not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a positive finite number of seconds, got {value!r}")
    return value


def _nonnegative_finite(name: str, value: float) -> float:
    if not (math.isfinite(value) and value >= 0):
        raise ValueError(f"{name} must be a finite number of seconds >= 0, got {value!r}")
    return value


#: How often a held velocity is re-sent. The edge zeroes velocity 2 s after the last one it
#: received (the robot then holds MOVE at rest), so 10 Hz leaves a 20-packet margin.
#: controllers send.
KEEPALIVE_HZ = 10.0
#: Past this much silence the state stream stops counting as an observation.
DEFAULT_LINK_TIMEOUT_S = 2.0
#: How long a firmware alert block is carried forward over frames that omit it (the firmware
#: sends it every 20th frame, 100 ms apart at 200 Hz).
ALERT_HOLD_S = 0.3
#: A goto() refuses to plan from a reported pose older than this.
GOTO_MAX_POSE_AGE_S = 0.5


class Robot:
    """One robot over one transport. Thread-safe: the verbs may be called from any thread."""

    def __init__(
        self,
        source: ConnectionConfig | Transport,
        *,
        limits: Limits | None = None,
        link_timeout: float = DEFAULT_LINK_TIMEOUT_S,
    ) -> None:
        """Bind a robot to a :class:`ConnectionConfig` (then :meth:`connect` picks the lane),
        or to an already-constructed transport (then :meth:`open` attaches it — the seam a
        new transport plugs into). Nothing touches the network here."""
        self._config: ConnectionConfig | None
        self._transport: Transport | None
        if isinstance(source, ConnectionConfig):
            self._config, self._transport = source, None
        else:
            self._config, self._transport = None, source
        self.limits = limits if limits is not None else Limits()
        self.link_timeout = _positive_finite("link_timeout", link_timeout)
        self._lock = threading.RLock()
        # `_state` and `_info` are single reference assignments read from several threads
        # without the lock. That is safe because reading/replacing one attribute is atomic
        # in CPython (GIL or free-threaded); anything compound goes under `_lock`.
        self._state: State | None = None
        self._last_alerts: tuple[Alert, ...] = ()
        self._last_alerts_at = 0.0
        self._state_seen = threading.Event()
        self._latched: Velocity | None = None
        self._latch_deadline: float | None = None
        self._generation = 0
        self._stop = threading.Event()
        self._keepalive: threading.Thread | None = None
        self._pending: collections.OrderedDict[int, Sent] = collections.OrderedDict()
        self._last_mode: Sent | None = None  # the stand/damp/trajectory a wait may be waiting on
        self._subscribed = False
        self._refusals: collections.deque[Refused] = collections.deque(maxlen=256)
        self._closed = True
        self._link_lost: LinkLostError | None = None
        self._info: RobotInfo | None = None
        self._derived_caps: frozenset[str] = frozenset()
        tx = self._current_transport
        self._microphone = Microphone("microphone", tx)
        self._camera = Camera("camera", tx, self._microphone)  # capture_clip records both
        self._speaker = Speaker(tx)
        self.on_refused: Callable[[Refused], None] | None = None
        #: Every accepted state sample, on the transport's reader thread. Keep it short.
        self.on_state: Callable[[State], None] | None = None
        #: An alert appearing ("raised") or disappearing ("cleared") between samples.
        self.on_alert: Callable[[Alert, Literal["raised", "cleared"]], None] | None = None
        #: The reported mode changed: (previous, current).
        self.on_mode_change: Callable[[Mode, Mode], None] | None = None
        self._on_sent: Callable[[Sent], None] | None = None  # recording hook
        self.on_controller_change: Callable[[str | None, str | None, str], None] | None = None
        self.on_link_lost: Callable[[LinkLostError], None] | None = None

    # ── the transport, and choosing it ───────────────────────────────────────
    @property
    def _tx(self) -> Transport:
        tx = self._transport
        if tx is None:
            raise NotConnectedError(
                "this Robot is not connected: call connect(mode) with one of "
                + ", ".join(repr(m) for m in self.config.available_modes())
            )
        return tx

    def _current_transport(self) -> Transport:
        return self._tx

    @property
    def config(self) -> ConnectionConfig:
        """The :class:`ConnectionConfig` this Robot was bound to."""
        if self._config is None:
            raise RuntimeError("this Robot was built over a transport, not a ConnectionConfig")
        return self._config

    def connect(
        self,
        mode: ConnectMode,
        *,
        timeout: float = 5.0,
        media_timeout: float = MEDIA_TIMEOUT_S,
        connect_timeout: float = 10.0,
        allow_version_skew: bool = False,
    ) -> Self:
        """Attach on ``mode`` — ``"udp"``, ``"hybrid"`` (UDP control + LiveKit media) or
        ``"livekit"`` — using the lanes described by this Robot's :class:`ConnectionConfig`.

        Returns the robot once the first state sample has arrived and its protocol version
        matches, so ``with robot.connect("hybrid"):`` works. Raises :class:`ConnectError`
        before touching the network when the config lacks a slot the mode needs, and after
        ``timeout`` seconds of silence otherwise. ``close()`` and call again with another mode
        to switch lanes on the same Robot: limits, joint table and hooks are kept.

        ``media_timeout`` is how long a LiveKit lane waits for the robot's tracks before
        deciding what it carries; ``connect_timeout`` bounds the room join itself.
        """
        config = self.config  # a transport-bound Robot has none: that error comes first
        with self._lock:
            if not self._closed:
                raise RuntimeError("this Robot is already connected; close() it first")
        tx = config.transport_for(
            mode, media_timeout=media_timeout, connect_timeout=connect_timeout
        )
        with self._lock:  # a second connect() racing this one must not swap the lane twice
            if not self._closed:
                raise RuntimeError("this Robot is already connected; close() it first")
            self._transport = tx
            self._subscribed = False  # the new transport has not heard our callbacks yet
            self._derived_caps = frozenset()  # the previous robot's battery is not this one's
        self._camera._rebind()
        self._microphone._rebind()
        self.open(timeout=timeout, allow_version_skew=allow_version_skew)
        return self

    def open(self, *, timeout: float = 5.0, allow_version_skew: bool = False) -> None:
        if not self._closed:
            raise RuntimeError("this Robot is already open")
        if self._transport is None:
            raise NotConnectedError(
                "this Robot is bound to a ConnectionConfig: call connect(mode) instead of open()"
            )
        if not self._subscribed:  # a failed open() followed by a retry must not double-subscribe
            tx = self._tx
            tx.subscribe_state(self._only_from(tx, self._on_state))
            tx.subscribe_outcome(self._only_from(tx, self._on_outcome))
            tx.subscribe_controller_change(self._only_from(tx, self._on_controller))
            self._subscribed = True
        # Every open() waits for a FRESH sample: a Robot reopened after close() must not
        # pass the protocol check on what the previous session left behind.
        self._forget_state()
        with self._lock:  # nothing from the previous session may leak into this one
            self._info = None  # or _on_state would filter the NEW robot's samples as foreign
            self._link_lost = None
            self._latched, self._latch_deadline = None, None  # a drive never survives a session
            self._last_alerts, self._last_alerts_at = (), 0.0  # nor do the last session's alerts
            self._generation += 1
            self._last_mode = None
            self._pending.clear()
            self._refusals.clear()
        self._tx.open()
        # Verbs stay refused (NotConnectedError) until the handshake below has passed.
        if not self._state_seen.wait(timeout):
            self._tx.close()
            # A transport MAY carry a ``silence_hint``: what to check when a connect hears no
            # state on its wire. It knows the setup that feeds it; this class does not.
            raise ConnectError(
                f"no state from the robot at {self._tx.endpoint} within {timeout:.1f}s. "
                f"{getattr(self._tx, 'silence_hint', '')}"
            )
        first = self._state
        assert first is not None
        if first.protocol_version != robots.PROTOCOL_VERSION and not allow_version_skew:
            self._tx.close()
            raise ProtocolMismatchError(
                f"the robot reports asimov.io protocol v{first.protocol_version}; this SDK "
                f"was built against v{robots.PROTOCOL_VERSION}. Commands would be misread. "
                "Update the SDK (or the robot), or pass allow_version_skew=True to proceed "
                "at your own risk.",
                expected=robots.PROTOCOL_VERSION,
                observed=first.protocol_version,
            )
        dof = len(first.joints)
        capabilities = set(self._tx.capabilities)
        if first.battery is not None:
            capabilities.add("battery")
        # What the robot told us about itself, as opposed to what the wire carries. The
        # transport can lose a track mid-session and `has()` follows it down; these don't
        # come from the transport, so they can't be taken away by one.
        self._derived_caps = frozenset(capabilities) - frozenset(self._tx.capabilities)
        self._info = RobotInfo(
            transport=self._tx.kind,
            endpoint=self._tx.endpoint,
            dof=dof,
            joint_names=robots.joint_names_for(dof),
            protocol_version=first.protocol_version,
            limits=self.limits,
            capabilities=frozenset(capabilities),
        )
        self._closed = False
        # One stop flag per session. close() may run ON the keepalive thread (an on_link_lost
        # handler that reconnects); a shared flag that open() cleared would un-signal that
        # thread's exit before it woke, leaving two keepalives re-sending into the edge.
        stop = threading.Event()
        self._stop = stop
        self._keepalive = threading.Thread(
            target=self._keepalive_loop, args=(stop,), name="asimov-sdk-keepalive", daemon=True
        )
        self._keepalive.start()

    def _only_from[**P](self, tx: Transport, handler: Callable[P, None]) -> Callable[P, None]:
        """Wrap a transport callback so only the transport this Robot is CURRENTLY on may call
        it. A closed transport keeps its subscriber list and its reader thread is joined
        best-effort, so a late sample from the previous lane must not become this session's
        state, frame or handshake."""

        def guarded(*args: P.args, **kwargs: P.kwargs) -> None:
            if self._transport is tx:
                handler(*args, **kwargs)

        return guarded

    def close(self) -> None:
        """Zero velocity if one is held, stop re-sending a held trajectory, then drop the link.
        Idempotent; never raises. A trajectory that is no longer re-sent is DAMPed by the edge
        two seconds later: there is no neutral setpoint the SDK could send instead."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            tx = self._transport  # pinned here: a connect() racing us may swap the lane
            had_velocity = self._latched is not None
            self._latched = None
            self._generation += 1
        self._stop.set()
        if self._keepalive is not None:
            # close() may run on the keepalive thread itself (an on_link_lost handler that
            # closes the robot); a thread cannot join itself, and it is exiting anyway.
            if self._keepalive is not threading.current_thread():
                self._keepalive.join(timeout=2.0)
            self._keepalive = None
        if tx is None:
            return
        if had_velocity:
            self._send_safety_zero("close", tx)
        tx.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── identity & state ─────────────────────────────────────────────────────
    @property
    def info(self) -> RobotInfo:
        if self._info is None:
            raise NotConnectedError("not connected")
        return self._info

    @property
    def connected(self) -> bool:
        return not self._closed and self._link_lost is None

    def has(self, capability: Capability | str) -> bool:
        """Does this robot, over this transport, provide ``capability`` **right now**?

        Reads the transport's **live** set rather than ``info.capabilities``, which is a
        snapshot taken at connect (``RobotInfo`` is frozen). On a room lane a camera goes
        away mid-session when its track unsubscribes, and a script gating on ``has()`` —
        the documented safe pattern — should then skip cleanly instead of taking the
        ``UnsupportedError`` it was trying to avoid.

        Capabilities the robot reported about *itself* (``battery``) are not the wire's to
        lose, so they stay until the session ends.
        """
        if self._closed:
            raise NotConnectedError("not connected: has() describes a live session")
        cap = str(capability)
        return cap in self._tx.capabilities or cap in self._derived_caps

    def require(self, *capabilities: Capability | str) -> None:
        """Raise :class:`UnsupportedError` for the first capability this robot lacks. For the
        top of a script that cannot degrade."""
        for c in capabilities:
            if not self.has(c):
                raise UnsupportedError(str(c), self._tx.kind)

    def record(self, path: str | os.PathLike[str], **kw: bool) -> Recording:
        """``with robot.record("run.jsonl"): ...`` writes every state sample and every
        command sent to a JSON-lines file. See :mod:`asimov_sdk.recording`."""
        return Recording(self, path, **kw)

    @property
    def camera(self) -> Camera:
        """Frames from the robot's camera. Raises ``UnsupportedError`` on first use when this
        transport does not carry them."""
        return self._camera

    @property
    def microphone(self) -> Microphone:
        """Audio from the robot's microphone. Same contract as ``camera``."""
        return self._microphone

    @property
    def speaker(self) -> Speaker:
        """Audio to the robot's speaker. ``play`` raises ``UnsupportedError`` when this transport
        cannot carry it."""
        return self._speaker

    @property
    def state(self) -> State:
        """The latest sample the robot pushed. Check ``state.age_s`` before trusting it."""
        s = self._state
        if s is None:
            raise NotConnectedError("no state received yet")
        return s

    # ── verbs: each returns a Sent immediately ───────────────────────────────
    def set_velocity(
        self,
        vx: float = 0.0,
        vy: float = 0.0,
        vyaw: float = 0.0,
        *,
        duration: float | None = None,
    ) -> Sent:
        """Walk. Held and re-sent at 10 Hz until superseded, ``stop()``, or ``duration``
        seconds pass (then zero velocity is sent). Clamped to ``limits``; the returned
        ``Sent.command`` is what actually went out and ``Sent.clamped`` says whether it
        differs from what you asked."""
        return self._drive("set_velocity", Velocity(vx, vy, vyaw), duration)

    def _drive(self, name: str, asked: Velocity, duration: float | None) -> Sent:
        v = asked.clamped(self.limits)
        if duration is not None:
            _positive_finite("duration", duration)
        # Bump and send under ONE lock hold: a verb issued by the caller must never be
        # dropped as "superseded" by a concurrent verb. The generation fence exists for the
        # keepalive's re-sends, which are the only sends that may legitimately go stale.
        with self._lock:
            self._generation += 1
            gen = self._generation
            self._latched = None if v.is_zero else v
            self._latch_deadline = (
                time.monotonic() + duration if (duration is not None and not v.is_zero) else None
            )
            self._last_mode = None  # a new drive supersedes whatever posture change preceded it
            return self._send(name, v, gen, clamped=(v != asked))

    def stop(self) -> Sent:
        """Zero velocity. The firmware stays in MOVE at zero speed, balancing in place
        under the walking policy — for a free-standing robot that IS how it stands still,
        so this is usually where a walk should end. ``stand()`` returns it to the STAND
        posture, but see that verb's warning first. Not an emergency stop — see ``damp``."""
        return self._drive("stop", Velocity(), None)

    def stand(self) -> Sent:
        """Stiffen to the standing pose. One-shot; follow with ``wait_for(Mode.STAND)``.

        STAND blends every joint to a fixed pose and holds it there with position gains.
        There is **no balance loop** — the robot does not catch itself. It is the wake-up
        verb (DAMP -> STAND -> MOVE) and is safe on a robot that is held, craned or on its
        stand. Asking a free-standing biped to stiffen after walking tips it over, and a
        fall latches a fault-DAMP that lasts until the firmware restarts. To stand still
        after walking, stay in MOVE at zero velocity (``stop()``), where the policy keeps
        balancing."""
        return self._once("stand", ModeCommand("stand"))

    def damp(self) -> Sent:
        """Motors go compliant NOW. A standing or walking biped collapses — this is the
        emergency stop, and the verb you type in full when you mean it."""
        return self._once("damp", ModeCommand("damp"))

    def trajectory(
        self,
        positions: Iterable[float],
        *,
        kp: tuple[float, ...] | list[float] | None = None,
        kd: tuple[float, ...] | list[float] | None = None,
    ) -> Sent:
        """One set of joint targets for every motor (radians, firmware order). Every joint goes
        under position control and the walking policy is off, so a standing biped will not
        balance itself — see :meth:`goto`. The edge drives a trajectory for two seconds after
        the last setpoint and then DAMPs, so clock these yourself or use :meth:`goto`.
        A ``kp``/``kd`` entry of zero or less means the firmware substitutes its own DAMP
        gains for that joint (limp). ``ValueError`` unless ``len(positions) == info.dof``, or
        when only one of ``kp``/``kd`` is given (the edge ignores a lone gain)."""
        pos = tuple(float(p) for p in positions)
        if self._info is not None and len(pos) != self._info.dof:
            raise ValueError(
                f"trajectory has {len(pos)} positions; this robot has {self._info.dof} motors"
            )
        t = Trajectory(
            pos,
            tuple(float(x) for x in kp) if kp is not None else None,
            tuple(float(x) for x in kd) if kd is not None else None,
        )
        return self._once("trajectory", t)

    def goto(
        self,
        positions: Iterable[float],
        *,
        duration: float = 2.0,
        hz: float = 50.0,
        kp: Iterable[float] | None = None,
        kd: Iterable[float] | None = None,
        wait: bool = True,
        tolerance: float = 0.05,
        timeout: float | None = None,
    ) -> Sent:
        """Move every joint from where it IS to ``positions`` over ``duration`` seconds.

        A trajectory puts EVERY joint under position control with the walking policy off:
        the robot does not balance itself while one is in force. On a standing biped, use
        this only with the robot supported, or with gains (``kp``/``kd``) known to hold the
        legs; a fall latches a fault-DAMP that lasts until the firmware restarts.

        Interpolates (minimum-jerk) from the current reported joint positions and clocks
        ``trajectory()`` setpoints at ``hz`` from a background thread, then HOLDS the target
        by re-sending it at the keepalive rate until another verb takes over — the edge
        DAMPs a trajectory two seconds after the last setpoint, so a reached pose is kept
        alive the way a velocity is. ``stand()`` (or any other verb) ends the hold. With
        ``wait``, blocks until every joint is within ``tolerance`` radians of the target, or
        raises :class:`WaitTimeoutError` after ``timeout`` (default ``duration + 2``); the
        target is still held after that timeout until another verb. Every argument is
        validated before the first setpoint leaves. Returns the ``Sent`` of the first setpoint.
        """
        target = tuple(float(p) for p in positions)
        _positive_finite("duration", duration)
        _positive_finite("hz", hz)
        _nonnegative_finite("tolerance", tolerance)
        if timeout is not None:
            _nonnegative_finite("timeout", timeout)
        current = self.state
        if current.age_s > GOTO_MAX_POSE_AGE_S:
            raise StateStaleError(
                f"the last reported pose is {current.age_s:.2f}s old; a motion must start from "
                f"a fresh one (limit {GOTO_MAX_POSE_AGE_S:.1f}s)",
                last=current,
            )
        start = current.joint_pos
        if len(target) != len(start):
            raise ValueError(f"goto has {len(target)} positions; this robot reports {len(start)}")
        kp_t = tuple(float(x) for x in kp) if kp is not None else None
        kd_t = tuple(float(x) for x in kd) if kd is not None else None
        steps = max(1, round(duration * hz))
        period = 1.0 / hz

        def blend(i: int) -> tuple[float, ...]:
            t = i / steps
            a = 10 * t**3 - 15 * t**4 + 6 * t**5  # minimum jerk, 0→1
            return tuple(s0 + (s1 - s0) * a for s0, s1 in zip(start, target, strict=True))

        with self._lock:
            # Bump, send the first setpoint and record the generation in ONE lock hold: a
            # verb landing between them would otherwise leave this motion running under the
            # newer generation and let stale setpoints follow the takeover command.
            self._generation += 1
            gen = self._generation
            self._latched, self._latch_deadline = None, None
            first = self._send("trajectory", Trajectory(blend(1), kp_t, kd_t), gen)
            self._last_mode = first

        def run() -> None:
            i = 2
            while True:
                # Clock the motion at `hz`; once the target is reached keep re-sending it at the
                # keepalive rate — the edge DAMPs a trajectory two seconds after the last setpoint,
                # so a reached pose must be held until another verb takes over.
                time.sleep(period if i <= steps else 1.0 / KEEPALIVE_HZ)
                with self._lock:
                    if self._generation != gen or self._closed:
                        return  # superseded by another verb, or closed
                try:
                    self._send("trajectory", Trajectory(blend(min(i, steps)), kp_t, kd_t), gen)
                except AsimovError:
                    return
                i += 1

        threading.Thread(target=run, name="asimov-sdk-goto", daemon=True).start()
        if wait:
            limit = duration + 2.0 if timeout is None else timeout
            self.wait_until(
                lambda st: all(
                    abs(p - q) <= tolerance for p, q in zip(st.joint_pos, target, strict=False)
                ),
                timeout=limit,
            )
        return first

    # ── waits: the robot's own report ────────────────────────────────────────
    def wait_until(
        self,
        predicate: Callable[[State], bool],
        *,
        timeout: float = 10.0,
        stale_after: float | None = None,
        poll: float = 0.05,
    ) -> State:
        """Block until the robot's OWN state satisfies ``predicate``.

        Ends early, with a typed error, when the answer can no longer come: the stream
        went quiet (:class:`StateStaleError`); the firmware fault-DAMPed
        (:class:`RobotFaultedError`, checked before the predicate, so a fault is never read
        as success); or the ``stand``/``damp``/``trajectory`` still in force — the last verb
        sent, when it was one of those — was refused (:class:`CommandRefusedError`).
        """
        _nonnegative_finite("timeout", timeout)
        _nonnegative_finite("poll", poll)
        stale = (
            self.link_timeout
            if stale_after is None
            else _positive_finite("stale_after", stale_after)
        )
        deadline = time.monotonic() + timeout
        last: State | None = None
        while True:
            if self._closed:  # a cached sample from a closed session must not satisfy a wait
                raise NotConnectedError("this Robot is closed")
            if self._link_lost is not None:
                raise self._link_lost
            s = self._state
            if s is not None:
                if s.age_s > stale:
                    raise StateStaleError(
                        f"the robot has not reported for {s.age_s:.2f}s (limit {stale:.2f}s); "
                        "treating it as absent, not slow",
                        last=s,
                    )
                if s.faulted and s.mode in (Mode.DAMP, Mode.UNKNOWN):
                    raise RobotFaultedError(
                        "the firmware fault-DAMPed; the wait cannot succeed "
                        f"(error_flags={s.error_flags}, critical alerts="
                        f"{[a.id for a in s.alerts if a.critical]})",
                        state=s,
                    )
                if predicate(s):
                    return s
                last = s
            pending_mode = self._last_mode  # only the command still in force can refuse a wait
            if pending_mode is not None and isinstance(pending_mode.outcome, Refused):
                raise CommandRefusedError(pending_mode.outcome)
            if time.monotonic() >= deadline:
                raise WaitTimeoutError(
                    f"condition not met after {timeout:.1f}s"
                    + (f" (mode={last.mode.name})" if last else ""),
                    last=last,
                )
            time.sleep(poll)

    def wait_for(
        self, mode: Mode, *, timeout: float = 10.0, stale_after: float | None = None
    ) -> State:
        """``wait_until(lambda s: s.mode is mode)`` with a readable name. A quiet stream still
        raises :class:`StateStaleError`, not a plain timeout."""
        try:
            return self.wait_until(
                lambda s: s.mode is mode, timeout=timeout, stale_after=stale_after
            )
        except StateStaleError:
            raise
        except WaitTimeoutError as exc:
            raise WaitTimeoutError(
                f"the robot did not reach {mode.name} within {timeout:.1f}s"
                + (f" (still {exc.last.mode.name})" if exc.last else ""),
                last=exc.last,
            ) from None

    def outcomes(self) -> Iterator[Refused]:
        """Drain refusals received so far, oldest first. The drain happens NOW, under the
        lock, at call time (not on first iteration); each refusal is handed to exactly one
        caller."""
        with self._lock:
            drained = tuple(self._refusals)
            self._refusals.clear()
        return iter(drained)

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _once(self, name: str, command: Command) -> Sent:
        with self._lock:  # bump + send atomically; see set_velocity
            self._generation += 1
            gen = self._generation
            self._latched = None  # a posture change ends whatever drive was in force
            self._latch_deadline = None
            sent = self._send(name, command, gen)
            self._last_mode = sent
            return sent

    def _send(self, name: str, command: Command, gen: int, *, clamped: bool = False) -> Sent:
        lost: LinkLostError | None = None
        with self._lock:
            # Checked under the lock so a close() racing on another thread cannot slip a
            # command onto a transport that is being torn down.
            if self._closed:
                raise NotConnectedError("this Robot is closed")
            if self._link_lost is not None:
                raise self._link_lost
            if gen != self._generation:
                # Superseded before it left. Report it as sent-then-superseded rather than
                # send a stale command after its replacement.
                return Sent(
                    name=name,
                    sequence=-1,
                    command=command,
                    clamped=clamped,
                    default_timeout=self._tx.default_outcome_timeout,
                )
            try:
                seq = self._tx.send(command)
            except LinkLostError as exc:
                lost = exc
            if lost is None:
                sent = Sent(
                    name=name,
                    sequence=seq,
                    command=command,
                    clamped=clamped,
                    default_timeout=self._tx.default_outcome_timeout,
                )
                self._pending[seq] = sent
                while len(self._pending) > 512:
                    self._pending.popitem(last=False)
                hook = self._on_sent
        if lost is not None:
            # Outside the lock: on_link_lost may block on things that need a verb.
            self._mark_link_lost(lost)
            raise lost
        if hook is not None:
            self._call(hook, sent)
        return sent

    def _keepalive_loop(self, stop: threading.Event) -> None:
        interval = 1.0 / KEEPALIVE_HZ
        last_seen = time.monotonic()
        while not stop.wait(interval):
            s = self._state
            if s is not None:
                last_seen = s.received_at
            if time.monotonic() - last_seen > self.link_timeout and self._link_lost is None:
                self._mark_link_lost(
                    LinkLostError(
                        f"no state from {self._tx.endpoint} for {self.link_timeout:.1f}s; "
                        "a zero velocity was sent and any held trajectory stopped; "
                        "close() and connect() again to reconnect"
                    )
                )
            with self._lock:
                v, gen, deadline = self._latched, self._generation, self._latch_deadline
                if v is not None and deadline is not None and time.monotonic() >= deadline:
                    # The bounded hold expired: send zero once and stop holding.
                    self._latched, self._latch_deadline = None, None
                    self._generation += 1
                    v, gen = Velocity(), self._generation
            if v is None:
                continue
            try:
                self._send("set_velocity", v, gen)
            except (LinkLostError, NotConnectedError):
                return
            except Exception as exc:  # a transport bug must not silently end the hold
                log.exception("keepalive send failed; declaring the link lost")
                self._mark_link_lost(LinkLostError(f"keepalive send failed: {exc!r}"))
                return

    def _mark_link_lost(self, exc: LinkLostError) -> None:
        with self._lock:
            if self._link_lost is not None:
                return
            self._link_lost = exc
            had_velocity = self._latched is not None
            self._latched, self._latch_deadline = None, None
            self._generation += 1
        if had_velocity:
            # The STATE stream died; the COMMAND path may well still reach the edge. Do not
            # leave a nonzero velocity as the last word — send the zero, best effort.
            self._send_safety_zero("link lost")
        if self.on_link_lost is not None:
            try:
                self.on_link_lost(exc)
            except Exception:
                log.exception("on_link_lost raised")

    def _send_safety_zero(self, why: str, tx: Transport | None = None) -> None:
        """Best-effort zero velocity on close() and link loss. Bypasses the closed/lost
        checks in ``_send`` on purpose, but still reaches the recording hook: a log whose
        last word is a nonzero setpoint would misreport what actually went out."""
        zero = Velocity()
        try:
            seq = (tx if tx is not None else self._tx).send(zero)
        except Exception:
            log.debug("%s: the zero-velocity frame did not go out", why, exc_info=True)
            return
        hook = self._on_sent
        if hook is not None:
            self._call(
                hook,
                Sent(
                    name="set_velocity",
                    sequence=seq,
                    command=zero,
                    clamped=False,
                    default_timeout=self._tx.default_outcome_timeout,
                ),
            )

    def _forget_state(self) -> None:
        self._state = None
        self._state_seen.clear()

    # transport callbacks (any thread) ──────────────────────────────────────
    def _on_state(self, state: State) -> None:
        # The lane is plain UDP: anyone on the LAN can hit the state port, and protobuf
        # decodes an empty or foreign datagram as a default RobotState. A sample that does
        # not look like THIS robot (no joints; or, once connected, a different protocol
        # version or joint count) must not refresh liveness or become `robot.state`.
        if not state.joints:
            log.debug("dropped a state sample with no joints")
            return
        info = self._info
        if info is not None and (
            state.protocol_version != info.protocol_version or len(state.joints) != info.dof
        ):
            log.debug(
                "dropped a state sample that does not match this robot (proto v%d, %d joints)",
                state.protocol_version,
                len(state.joints),
            )
            return
        # UDP reorders and duplicates. An OLDER sample must never overwrite a newer one,
        # or the caller reads the robot going backwards in time (seen at 20% reorder:
        # 196 backwards steps in one demo). Half-range compare: a counter that wrapped
        # to 0 is NEWER (difference > 2**31); an unstamped stream (all zeros) differs by 0.
        prev = self._state
        if prev is not None and 0 < (prev.sequence - state.sequence) % 2**32 < 2**31:
            return
        if state.joints and not state.joints[0].name:
            names = robots.joint_names_for(len(state.joints))
            if names is not None:
                state = dataclasses.replace(
                    state,
                    joints=tuple(
                        dataclasses.replace(j, name=n)
                        for j, n in zip(state.joints, names, strict=True)
                    ),
                )
        state = self._carry_alerts(state)
        prev = self._state
        self._state = state
        self._state_seen.set()
        self._fire_state_callbacks(prev, state)

    def _carry_alerts(self, state: State) -> State:
        """The firmware puts its alert block in every 20th frame (10 Hz at 200 Hz) and an
        absent block decodes the same as "no alerts". Carry the last block forward for
        ``ALERT_HOLD_S`` so ``state.alerts``, ``faulted`` and ``on_alert`` are stable
        per sample; a cleared alert lingers at most that long."""
        now = time.monotonic()
        if state.alerts:
            self._last_alerts, self._last_alerts_at = state.alerts, now
            return state
        if self._last_alerts and now - self._last_alerts_at < ALERT_HOLD_S:
            return dataclasses.replace(state, alerts=self._last_alerts)
        self._last_alerts = ()
        return state

    def _fire_state_callbacks(self, prev: State | None, state: State) -> None:
        if self.on_state is not None:
            self._call(self.on_state, state)
        if self.on_mode_change is not None and prev is not None and prev.mode is not state.mode:
            self._call(self.on_mode_change, prev.mode, state.mode)
        if self.on_alert is not None:
            before = {a.id: a for a in prev.alerts} if prev is not None else {}
            after = {a.id: a for a in state.alerts}
            for aid, alert in after.items():
                if aid not in before:
                    self._call(self.on_alert, alert, "raised")
            for aid, alert in before.items():
                if aid not in after:
                    self._call(self.on_alert, alert, "cleared")

    @staticmethod
    def _call(cb: Callable[..., None], *args: object) -> None:
        try:
            cb(*args)
        except Exception:
            log.exception("%s raised", getattr(cb, "__name__", "callback"))

    def _on_outcome(self, outcome: Applied | Refused) -> None:
        with self._lock:
            sent = self._pending.pop(outcome.sequence, None)  # resolved: no reason to keep it
        if sent is None:
            # Nothing of ours is waiting on this sequence: a verdict for a previous session
            # (or a duplicate). It must not surface as a fresh refusal of this session.
            log.debug("dropped an outcome for unknown sequence %d", outcome.sequence)
            return
        sent._resolve(outcome)  # stamps the verb onto a Refused
        resolved = sent.outcome
        if isinstance(resolved, Refused):
            outcome = resolved
        if isinstance(outcome, Refused):
            with self._lock:  # outcomes() snapshots+clears under the same lock
                self._refusals.append(outcome)
            if self.on_refused is not None:
                try:
                    self.on_refused(outcome)
                except Exception:
                    log.exception("on_refused raised")

    def _on_controller(self, previous: str | None, current: str | None, reason: str) -> None:
        if self.on_controller_change is not None:
            try:
                self.on_controller_change(previous, current, reason)
            except Exception:
                log.exception("on_controller_change raised")
