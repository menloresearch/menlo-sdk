"""Drive an Asimov from Python.

::

    from asimov_sdk import Robot, Mode

    with Robot.connect_direct("asimov.local") as robot:
        robot.stand()
        robot.wait_for(Mode.STAND, timeout=8.0)
        robot.set_velocity(vx=0.25, duration=4.0)   # held for 4 s, then zero
        robot.wait_for(Mode.MOVE)
        robot.stand()                               # MOVE at zero velocity is not STAND
        robot.wait_for(Mode.STAND)

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
* **Zero velocity is not STAND.** After ``stop()`` (or a ``duration`` ending) the firmware
  stays in MOVE mode with zero velocity — the walking policy, standing in place. ``stand()``
  is what returns it to the STAND posture. Observed on the robot; do not assume otherwise.
* **``close()`` zeroes velocity first**, then drops the link, so leaving the ``with``
  block — including by exception — leaves the robot standing still, not walking.
* **``damp()`` is the emergency verb.** A standing biped folds. It raises on a dead link
  like every other verb; ``close()`` is the one that swallows.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from collections.abc import Callable, Iterator
from typing import Self

from asimov_sdk import robots
from asimov_sdk._command import Command, Limits, ModeCommand, Trajectory, Velocity
from asimov_sdk._errors import (
    AsimovError,
    CommandRefusedError,
    ConnectFailed,
    LinkLost,
    NotConnected,
    ProtocolMismatch,
    RobotFaulted,
    StateStale,
    WaitTimedOut,
)
from asimov_sdk._outcome import Applied, Refused, Sent
from asimov_sdk._state import Mode, RobotInfo, State
from asimov_sdk.transport.base import Transport
from asimov_sdk.transport.udp import UdpTransport

log = logging.getLogger("asimov_sdk.robot")

#: How often a held velocity is re-sent. The edge zero-and-STANDs ~2 s after the last
#: velocity it saw; 10 Hz leaves 20 misses of margin and is what the edge's other
#: controllers send.
KEEPALIVE_HZ = 10.0
#: Past this much silence the state stream stops counting as an observation.
DEFAULT_LINK_TIMEOUT_S = 2.0


class Robot:
    """One robot over one transport. Thread-safe: the verbs may be called from any thread."""

    def __init__(
        self,
        transport: Transport,
        *,
        limits: Limits | None = None,
        link_timeout: float = DEFAULT_LINK_TIMEOUT_S,
    ) -> None:
        """Build a robot over an already-constructed transport. Prefer the ``connect_*``
        classmethods; this is the seam a new transport plugs into."""
        self._tx = transport
        self.limits = limits if limits is not None else Limits()
        self.link_timeout = link_timeout
        self._lock = threading.RLock()
        self._state: State | None = None
        self._state_seen = threading.Event()
        self._latched: Velocity | None = None
        self._latch_deadline: float | None = None
        self._generation = 0
        self._stop = threading.Event()
        self._keepalive: threading.Thread | None = None
        self._pending: collections.OrderedDict[int, Sent] = collections.OrderedDict()
        self._refusals: collections.deque[Refused] = collections.deque(maxlen=256)
        self._closed = True
        self._link_lost: LinkLost | None = None
        self._info: RobotInfo | None = None
        self.on_refused: Callable[[Refused], None] | None = None
        self.on_controller_change: Callable[[str | None, str | None, str], None] | None = None
        self.on_link_lost: Callable[[LinkLost], None] | None = None

    # ── constructors ─────────────────────────────────────────────────────────
    @classmethod
    def connect_direct(
        cls,
        host: str,
        *,
        command_port: int = 8850,
        state_bind: tuple[str, int] = ("0.0.0.0", 8851),
        timeout: float = 5.0,
        allow_version_skew: bool = False,
        limits: Limits | None = None,
    ) -> Robot:
        """Attach to a robot over its LAN UDP lane.

        The edge must be running with ``--udp-control`` and pushing state to this machine
        (``--udp-state-host <this ip>``). Returns once the first state sample has arrived
        and its protocol version matches; raises :class:`ConnectFailed` otherwise, because
        a UDP socket that hears nothing is talking to nobody.
        """
        tx = UdpTransport(host, command_port=command_port, state_bind=state_bind)
        robot = cls(tx, limits=limits)
        robot.open(timeout=timeout, allow_version_skew=allow_version_skew)
        return robot

    def open(self, *, timeout: float = 5.0, allow_version_skew: bool = False) -> None:
        if not self._closed:
            raise AsimovError("this Robot is already open")
        self._tx.subscribe_state(self._on_state)
        self._tx.subscribe_outcome(self._on_outcome)
        self._tx.subscribe_controller_change(self._on_controller)
        self._tx.open()
        self._closed = False
        self._stop.clear()
        if not self._state_seen.wait(timeout):
            self._tx.close()
            self._closed = True
            raise ConnectFailed(
                f"no state from the robot at {self._tx.endpoint} within {timeout:.1f}s. "
                "Is the edge running with --udp-control, and is its --udp-state-host "
                "pointing at this machine?"
            )
        first = self._state
        assert first is not None
        if first.protocol_version != robots.PROTOCOL_VERSION and not allow_version_skew:
            self._tx.close()
            self._closed = True
            raise ProtocolMismatch(
                f"the robot reports asimov.io protocol v{first.protocol_version}; this SDK "
                f"was built against v{robots.PROTOCOL_VERSION}. Commands would be misread. "
                "Update the SDK (or the robot), or pass allow_version_skew=True to proceed "
                "at your own risk.",
                expected=robots.PROTOCOL_VERSION,
                observed=first.protocol_version,
            )
        dof = len(first.joints)
        self._info = RobotInfo(
            transport=self._tx.kind,
            endpoint=self._tx.endpoint,
            dof=dof,
            joint_names=robots.joint_names_for(dof),
            protocol_version=first.protocol_version,
            limits=self.limits,
        )
        self._keepalive = threading.Thread(
            target=self._keepalive_loop, name="asimov-sdk-keepalive", daemon=True
        )
        self._keepalive.start()

    def close(self) -> None:
        """Zero velocity if one is held, then drop the link. Idempotent; never raises."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            had_velocity = self._latched is not None
            self._latched = None
            self._generation += 1
        self._stop.set()
        if self._keepalive is not None:
            self._keepalive.join(timeout=2.0)
            self._keepalive = None
        if had_velocity:
            try:
                self._tx.send(Velocity())
            except Exception:
                log.debug("close: the zero-velocity frame did not go out", exc_info=True)
        self._tx.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── identity & state ─────────────────────────────────────────────────────
    @property
    def info(self) -> RobotInfo:
        if self._info is None:
            raise NotConnected("not connected")
        return self._info

    @property
    def connected(self) -> bool:
        return not self._closed and self._link_lost is None

    @property
    def state(self) -> State:
        """The latest sample the robot pushed. Check ``state.age_s`` before trusting it."""
        s = self._state
        if s is None:
            raise NotConnected("no state received yet")
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
        asked = Velocity(vx, vy, vyaw)
        v = asked.clamped(self.limits)
        if duration is not None and duration <= 0:
            raise ValueError("duration must be positive")
        with self._lock:
            self._generation += 1
            gen = self._generation
            self._latched = None if v.is_zero else v
            self._latch_deadline = (
                time.monotonic() + duration if (duration is not None and not v.is_zero) else None
            )
        return self._send("set_velocity", v, gen, clamped=(v != asked))

    def stop(self) -> Sent:
        """Zero velocity. The firmware stays in MOVE at zero speed (standing in place under
        the walking policy); call ``stand()`` to return to the STAND posture. Not an
        emergency stop — see ``damp``."""
        return self.set_velocity()

    def stand(self) -> Sent:
        """Ask the firmware to stand. One-shot. Follow with ``wait_for(Mode.STAND)``."""
        return self._once("stand", ModeCommand("stand"))

    def damp(self) -> Sent:
        """Motors go compliant NOW. A standing or walking biped collapses — this is the
        emergency stop, and the verb you type in full when you mean it."""
        return self._once("damp", ModeCommand("damp"))

    def trajectory(
        self,
        positions: Iterator[float] | tuple[float, ...] | list[float],
        *,
        kp: tuple[float, ...] | list[float] | None = None,
        kd: tuple[float, ...] | list[float] | None = None,
    ) -> Sent:
        """Direct joint targets for every motor (radians, firmware order). A setpoint the
        caller clocks; ``ValueError`` unless ``len(positions) == info.dof``."""
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
        went quiet (:class:`StateStale`), the firmware fault-DAMPed (:class:`RobotFaulted`),
        or the most recent ``stand``/``damp`` was refused (:class:`CommandRefusedError`).
        """
        stale = self.link_timeout if stale_after is None else stale_after
        deadline = time.monotonic() + timeout
        last: State | None = None
        while True:
            if self._link_lost is not None:
                raise self._link_lost
            s = self._state
            if s is not None:
                if s.age_s > stale:
                    raise StateStale(
                        f"the robot has not reported for {s.age_s:.2f}s (limit {stale:.2f}s); "
                        "treating it as absent, not slow",
                        last=s,
                    )
                if predicate(s):
                    return s
                if s.faulted and s.mode is Mode.DAMP:
                    raise RobotFaulted(
                        "the firmware fault-DAMPed; the wait cannot succeed "
                        f"(error_flags={s.error_flags}, critical alerts="
                        f"{[a.id for a in s.alerts if a.critical]})",
                        state=s,
                    )
                last = s
            refused = self._last_mode_refusal()
            if refused is not None:
                raise CommandRefusedError(refused)
            if time.monotonic() >= deadline:
                raise WaitTimedOut(
                    f"condition not met after {timeout:.1f}s"
                    + (f" (mode={last.mode.name})" if last else ""),
                    last=last,
                )
            time.sleep(poll)

    def wait_for(self, mode: Mode, *, timeout: float = 10.0) -> State:
        """``wait_until(lambda s: s.mode is mode)`` with a readable name."""
        try:
            return self.wait_until(lambda s: s.mode is mode, timeout=timeout)
        except WaitTimedOut as exc:
            raise WaitTimedOut(
                f"the robot did not reach {mode.name} within {timeout:.1f}s"
                + (f" (still {exc.last.mode.name})" if exc.last else ""),
                last=exc.last,
            ) from None

    def outcomes(self) -> Iterator[Refused]:
        """Drain refusals received so far, oldest first."""
        while self._refusals:
            yield self._refusals.popleft()

    # ── plumbing ─────────────────────────────────────────────────────────────
    def _once(self, name: str, command: Command) -> Sent:
        with self._lock:
            self._generation += 1
            gen = self._generation
            self._latched = None  # a posture change ends whatever drive was in force
            self._latch_deadline = None
        return self._send(name, command, gen)

    def _send(self, name: str, command: Command, gen: int, *, clamped: bool = False) -> Sent:
        if self._closed:
            raise NotConnected("this Robot is closed")
        if self._link_lost is not None:
            raise self._link_lost
        with self._lock:
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
            except LinkLost as exc:
                self._mark_link_lost(exc)
                raise
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
            return sent

    def _keepalive_loop(self) -> None:
        interval = 1.0 / KEEPALIVE_HZ
        last_seen = time.monotonic()
        while not self._stop.wait(interval):
            s = self._state
            if s is not None:
                last_seen = s.received_at
            if time.monotonic() - last_seen > self.link_timeout and self._link_lost is None:
                self._mark_link_lost(
                    LinkLost(
                        f"no state from {self._tx.endpoint} for {self.link_timeout:.1f}s; "
                        "the edge's own watchdog has already stopped the robot"
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
            except (LinkLost, NotConnected):
                return

    def _mark_link_lost(self, exc: LinkLost) -> None:
        self._link_lost = exc
        with self._lock:
            self._latched = None
        if self.on_link_lost is not None:
            try:
                self.on_link_lost(exc)
            except Exception:
                log.exception("on_link_lost raised")

    def _last_mode_refusal(self) -> Refused | None:
        """A refusal for the most recent stand/damp, if one has landed."""
        for sent in reversed(self._pending.values()):
            if sent.name in ("stand", "damp"):
                o = sent.outcome
                return o if isinstance(o, Refused) else None
        return None

    # transport callbacks (any thread) ──────────────────────────────────────
    def _on_state(self, state: State) -> None:
        self._state = state
        self._state_seen.set()

    def _on_outcome(self, outcome: Applied | Refused) -> None:
        sent = self._pending.get(outcome.sequence)
        if sent is not None:
            sent._resolve(outcome)
        if isinstance(outcome, Refused):
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
