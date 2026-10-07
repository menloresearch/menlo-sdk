"""Drive an Asimov from Python.

::

    from menlo.asimov import Robot

    with Robot().connect() as robot:               # the saved default robot
        s = robot.get_state()                      # your guard reads the facts
        if s.faulted or (s.battery and s.battery.soc_percent < 20):
            raise SystemExit(f"not driving: robot mode {s.mode.name}")
        robot.stand()                              # DAMP -> STAND; returns once armed
        robot.balance()                            # STAND -> MOVE: balancing in place
        robot.set_velocity(vx=0.25, duration=4.0)  # walk, then zero velocity
        robot.balance()                            # stay in MOVE, balancing in place
        # The robot stays in MOVE at zero velocity, where the walking policy keeps
        # balancing it: that is how a free-standing biped stands still. Do not ask for
        # STAND here: STAND stiffens to a fixed pose with no balance loop, and a robot
        # nothing is holding tips over.

The verbs are the wire's verbs (``stand``, ``balance``, ``set_velocity``, ``damp``,
``trajectory``) and each returns a :class:`~menlo.asimov.Sent`. Three questions are kept
apart on purpose:

* **Should the robot do it now?** That is your call. Safety is the firmware's job, command
  handling is Asimov Edge's, and a guard (a joint too hot, a battery too low, a fault you
  will not drive through) is yours: read ``robot.get_state()`` and ``robot.armed``, or
  ``robot.preflight(action)`` for the same facts as a list. The SDK does not refuse a
  command because of what the robot reports. It refuses only without live state:
  ``stand()``, ``set_velocity()``, ``trajectory()``, ``set_joints()`` and ``balance()``
  from outside MOVE wait up to their ``timeout`` for a fresh sample and raise
  :class:`NotReadyError` with nothing sent when none comes. A closed Robot raises
  :class:`NotConnectedError`, a lost link :class:`LinkLostError` and a robot on another
  protocol version :class:`ProtocolMismatchError`, at once. ``balance()`` in MOVE and
  ``damp()`` are sent at once.
* **Did it take effect?** ``stand()``, ``balance()`` and ``damp()`` wait for the robot to
  report the robot mode they ask for, and ``set_velocity()`` for its ``duration``
  (``wait=False`` returns at once); ``robot.wait_until(pred)`` waits for anything else.
  They read the robot's own state stream, never infer from what was sent, and raise
  :class:`WaitTimeoutError` when it does not come.
* **Was it admitted?** ``sent.wait_outcome()``. Asimov Edge reports no per-command verdict
  on any connection mode, so this is ``Unknown`` (see :mod:`menlo.asimov._outcome`).

Behaviours worth knowing before the first script:

* **Armed.** The firmware reports STAND at once, but enters MOVE only once STAND has been
  held upright for 0.5 s (armed). ``stand()`` returns once the robot is armed. A velocity
  (``balance()`` or ``set_velocity()``) sent in STAND puts an armed robot in MOVE; one
  that arrives before the robot arms leaves it in STAND. In DAMP the firmware does not enter MOVE:
  ``balance()`` says so (``stand()`` first).
* **A velocity is held.** ``set_velocity`` latches and a background thread re-sends it
  at 10 Hz. On ``udp`` and ``hybrid``, Asimov Edge zeroes a velocity 2 s after the last one
  it received; on ``livekit`` it stops a held velocity when the SDK sends zero or leaves
  the room. ``duration=`` bounds the hold, and by default ``set_velocity`` blocks until it
  has ended and its zero has gone out. ``wait=False`` returns once sent and keeps the
  velocity until the next command: that is for a control loop, which sends a short
  ``duration`` on every tick.
* **Mode commands are one-shot.** STAND, DAMP and ``balance()`` are sent once; repeating
  them would let a script out-shout an operator's DAMP. Only a velocity and a trajectory
  are re-sent.
* **MOVE at zero velocity is not STAND, and it is usually what you want.** After
  ``balance()`` (or a ``duration`` ending) the firmware stays in MOVE with zero velocity:
  the walking policy, balancing in place. STAND is a different thing: it stiffens every
  joint to a fixed pose and runs **no balance loop**, so a free-standing biped asked to
  stiffen after walking tips over. ``stand()`` is for bringing a robot out of DAMP, or for
  one hanging from its gantry hook or seated on a stool or bench, never for finishing a
  walk. From MOVE it sends STAND as asked: support the robot first.
* **Your own setpoint loop owns the robot; ``set_joints()`` does not.** The firmware obeys
  whichever command arrived last. A ``set_joints()`` is fenced: any verb from any thread
  (``damp()``, ``stand()``, a velocity) bumps the generation and the set_joints thread stops
  before its next setpoint leaves. A loop you clock yourself with ``trajectory()`` is
  not fenced: a verb sent from another thread can be overwritten by your next setpoint.
  Stop your loop first, then send the verb. Asimov Edge DAMPs a trajectory 2 s after the
  last setpoint, whether or not your loop is still running.
* **``close()`` zeroes velocity first**, then drops the link, so leaving the ``with``
  block, including by exception, leaves the robot balancing in place, not walking. A
  script that ends without ``close()`` gets the same at interpreter exit.
* **Nothing here is an emergency stop.** ``damp()`` makes every actuator compliant and a
  standing biped folds; it raises on a dead link like every other verb, and ``close()`` is
  the one that swallows. Use the E-Stop in Asimov Manager in an emergency.
"""

from __future__ import annotations

import atexit
import collections
import dataclasses
import logging
import math
import os
import threading
import time
import weakref
from collections.abc import Callable, Iterable, Iterator
from typing import TYPE_CHECKING, Literal, Self

from menlo.asimov import robots, store
from menlo.asimov._command import Command, Limits, ModeCommand, Trajectory, Velocity
from menlo.asimov._errors import (
    CommandRefusedError,
    ConnectError,
    LinkLostError,
    MenloError,
    NotConnectedError,
    NotReadyError,
    ProtocolMismatchError,
    RobotFaultedError,
    StateStaleError,
    UnsupportedError,
    WaitTimeoutError,
)
from menlo.asimov._media import Camera, Microphone, Speaker
from menlo.asimov._outcome import Applied, Refused, Sent
from menlo.asimov._preflight import (
    ARM_GRAVITY_Z,
    ARM_HOLD_S,
    ARM_MAX_GAP_S,
    TRANSIENT,
    Action,
    Preflight,
    Problem,
    fault_names,
)
from menlo.asimov._preflight import evaluate as _evaluate_preflight
from menlo.asimov._state import Alert, Capability, Mode, RobotInfo, State
from menlo.asimov.connection import ConnectionConfig, ConnectMode, ManagerConfig
from menlo.asimov.recording import Recording
from menlo.asimov.transport.base import Transport

# Importing the LiveKit transports does NOT import livekit: every `livekit` import in the
# SDK is lazy, inside `transport/_livekit_client.py`. `connect("udp")` never touches it.
from menlo.asimov.transport.livekit import MEDIA_TIMEOUT_S

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


#: ``"vx,vy,vyaw"``: the velocity clamp for a Robot constructed without ``limits=``.
ENV_LIMITS = "MENLO_LIMITS"

#: How often a held velocity is re-sent. Asimov Edge zeroes velocity 2 s after the last one
#: it received (the robot then holds MOVE at rest), so 10 Hz leaves a 20-packet margin.
KEEPALIVE_HZ = 10.0
#: Past this much silence the state stream stops counting as an observation.
DEFAULT_LINK_TIMEOUT_S = 2.0
#: How long a firmware alert block is carried forward over frames that omit it (the firmware
#: sends it every 20th frame, 100 ms apart at 200 Hz).
ALERT_HOLD_S = 0.3
#: How long set_velocity() and set_joints() wait, by default, for live state (``TRANSIENT``)
#: before they raise NotReadyError.
READY_TIMEOUT_S = 5.0
#: How long stand() may take by default, from the call to an armed STAND: the firmware
#: reports STAND at once and arms after 0.5 s upright.
STAND_TIMEOUT_S = 10.0
#: How long damp() waits by default for DAMP to be reported.
DAMP_TIMEOUT_S = 5.0
#: How long balance() may take by default from STAND, from the call to MOVE reported.
BALANCE_TIMEOUT_S = 5.0
#: A STAND sent this recently may still be on its way back (one livekit sample is 0.1 s), so a
#: command that finds the robot still in DAMP waits for the report before it sends.
STAND_REPORT_S = 1.0
#: How often a command re-reads the state while it waits for live state.
READY_POLL_S = 0.02
#: Robot modes in which a latched fault means the robot is down and a wait cannot succeed.
_DOWN = (Mode.DAMP, Mode.FAULT_DAMP, Mode.UNKNOWN)
# Set while an on_state / on_alert / on_mode_change callback runs. Those run on the thread
# that delivers state, so a wait for state made from one could never see a new sample.
_CALLBACK = threading.local()


def _in_callback() -> bool:
    return getattr(_CALLBACK, "active", False)


def _no_wait_in_callback(verb: str, wait: bool) -> None:
    if wait and _in_callback():
        raise RuntimeError(
            f"{verb}() waits for the robot's state, and a state callback runs on the thread "
            "that delivers it; pass wait=False, or call it from your own thread"
        )


#: A state sample whose sequence is behind the last accepted one by at most this many is
#: a reordered datagram and is dropped; behind by more, the counter has restarted (the
#: firmware rebooted under a live link) and the sample is the new stream.
STATE_REORDER_WINDOW = 1000
#: A firmware clock that went back by more than this, sequence aside, is also a restart.
STATE_CLOCK_RESET_S = 1.0


class Robot:
    """One robot over one transport. Thread-safe: the verbs may be called from any thread."""

    def __init__(
        self,
        source: ConnectionConfig | Transport | None = None,
        *,
        limits: Limits | None = None,
        link_timeout: float = DEFAULT_LINK_TIMEOUT_S,
    ) -> None:
        """Bind a robot to a :class:`ConnectionConfig` (then :meth:`connect` picks the
        connection mode),
        or to an already-constructed transport (then :meth:`open` attaches it: the seam a
        new transport plugs into). With no ``source`` the config comes from the environment
        or the credential store (:meth:`ConnectionConfig.from_environment`), so
        ``Robot().connect()`` is a whole script's setup. Nothing touches the network here.

        ``limits`` is the velocity clamp; left ``None`` it is ``MENLO_LIMITS``, else the
        config's (a saved robot's ``[robots.NAME.limits]``), else the firmware caps
        (:class:`Limits`)."""
        self._config: ConnectionConfig | None
        self._transport: Transport | None
        if source is None:
            source = ConnectionConfig.from_environment()
        if isinstance(source, ConnectionConfig):
            self._config, self._transport = source, None
        else:
            self._config, self._transport = None, source
        self.limits = self._choose_limits(limits)
        self.link_timeout = _positive_finite("link_timeout", link_timeout)
        self._lock = threading.RLock()
        # `_state` and `_info` are single reference assignments read from several threads
        # without the lock. That is safe because reading/replacing one attribute is atomic
        # in CPython (GIL or free-threaded); anything compound goes under `_lock`.
        self._state: State | None = None
        self._last_alerts: tuple[Alert, ...] = ()
        self._last_alerts_at = 0.0
        self._state_seen = threading.Event()
        # Arming, as observed: whether the firmware accepts MOVE (see `armed`). Written on
        # the reader thread only.
        self._armed = False
        self._upright_since: float | None = None
        self._latched: Velocity | None = None
        self._latch_deadline: float | None = None
        self._hold_done: threading.Event | None = None  # set when a bounded hold has ended
        # Each bounded hold a fault-DAMP ended, by its event, with the sample that showed
        # it: the caller blocked in set_velocity(wait=True) on that hold raises instead of
        # returning. One entry per hold, so a later hold never erases an earlier one's
        # reason; an entry goes when its waiter takes it or nobody holds its event.
        self._faulted_holds: weakref.WeakKeyDictionary[threading.Event, State] = (
            weakref.WeakKeyDictionary()
        )
        # The last velocity went out once (hold=False) and was not zero: close() zeroes it.
        self._streamed = False
        self._generation = 0
        self._stop = threading.Event()
        self._keepalive: threading.Thread | None = None
        self._pending: collections.OrderedDict[int, Sent] = collections.OrderedDict()
        self._last_mode: Sent | None = None  # the stand/damp/trajectory a wait may be waiting on
        self._subscribed = False
        self._refusals: collections.deque[Refused] = collections.deque(maxlen=256)
        self._closed = True
        self._opening = False  # a connect() or open() is running (see _reserve_open)
        # open() sets these before the transport starts delivering: whether samples that
        # arrive before open() returns may complete the handshake themselves (a session
        # that did not wait for the robot), and whether this Robot is taking samples at all.
        self._late = False
        self._accepting = False
        self._link_lost: LinkLostError | None = None
        self._info: RobotInfo | None = None
        self._derived_caps: frozenset[str] = frozenset()
        self._allow_version_skew = False
        self._handshake_error: ProtocolMismatchError | None = None
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

    def _choose_limits(self, limits: Limits | None) -> Limits:
        """The argument, else ``MENLO_LIMITS``, else the config's (a saved robot's), else
        the firmware caps."""
        if limits is not None:
            return limits
        env = os.environ.get(ENV_LIMITS, "").strip()
        if env:
            try:
                return Limits.parse(env)
            except ValueError as exc:
                raise ValueError(f"{ENV_LIMITS}: {exc}") from None
        if self._config is not None and self._config.limits is not None:
            return self._config.limits
        return Limits()

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
        mode: ConnectMode | None = None,
        *,
        timeout: float = 5.0,
        media_timeout: float = MEDIA_TIMEOUT_S,
        connect_timeout: float = 10.0,
        allow_version_skew: bool = False,
        require_state: bool = True,
        persist: bool | None = None,
    ) -> Self:
        """Attach on connection mode ``mode``: ``"udp"``, ``"hybrid"`` (control and state over
        UDP, camera and audio over LiveKit) or ``"livekit"``, using the connections described
        by this Robot's :class:`ConnectionConfig`. Left out, it is the config's ``mode`` (a
        saved robot's, or ``MENLO_MODE``), else the mode its connections imply: ``udp``
        alone is udp, ``livekit`` alone is livekit, both together is hybrid.

        Returns the robot once the first state sample has arrived and its protocol version
        matches, so ``with robot.connect("hybrid"):`` works. Raises :class:`ConnectError`
        before touching the network when the config lacks what the mode needs (naming the
        field and the command that sets it), and after ``timeout`` seconds of silence
        otherwise. ``close()`` and call again with another mode to switch connection modes on
        the same Robot: limits, joint table and hooks are kept.

        ``require_state=False`` returns as soon as the connection is open, without waiting for the
        robot to report: the camera, microphone and speaker work whether or not the
        firmware is running, so a media-only script does not need it to be. Until the first
        state sample arrives ``robot.get_state()`` and ``robot.info`` raise
        :class:`NotConnectedError` and every motion verb refuses with the same error; a
        wait times out. When state does arrive the handshake completes on its own and the
        session becomes an ordinary one, including :class:`LinkLostError` should that
        stream then go quiet. Nothing is sent to the robot before then.

        ``persist=True`` (or ``MENLO_PERSIST=1`` in the environment) saves the connection in
        ``~/.menlo/robots.toml`` once the connect has SUCCEEDED: the Asimov Manager URL, the
        credential, the room, the UDP host and the mode, keyed by the saved robot's name or
        the robot's serial (from its room), and made the default when the store had none,
        so the next script can be ``Robot().connect()``. ``ValueError`` before any I/O when
        the config has no :class:`ManagerConfig`. See :mod:`menlo.asimov.store`.

        ``media_timeout`` is how long a LiveKit connection waits for the robot's tracks before
        deciding what it carries; ``connect_timeout`` bounds the room join itself.
        """
        config = self.config  # a transport-bound Robot has none: that error comes first
        if mode is None:
            mode = config.default_mode()
        if persist is None:
            persist = store.env_flag(store.ENV_PERSIST)
        if persist and not isinstance(config.livekit, ManagerConfig):
            raise ValueError(
                "connect(persist=True) needs a ManagerConfig in the livekit slot: the store "
                "keeps a manager URL and a credential, and this config has no manager"
            )
        self._reserve_open("connected")
        try:
            tx = config.transport_for(
                mode, media_timeout=media_timeout, connect_timeout=connect_timeout
            )
            with self._lock:
                self._transport = tx
                self._subscribed = False  # the new transport has not heard our callbacks
                self._derived_caps = frozenset()  # the previous robot's battery is not this one's
            self._camera._rebind()
            self._microphone._rebind()
            self._open(
                timeout=timeout, allow_version_skew=allow_version_skew, require_state=require_state
            )
        finally:
            self._opening = False
        if persist:
            # The session is open by now. A store that cannot be written (read-only or full
            # $MENLO_HOME, a name that belongs to another manager) must not leave it open
            # behind an exception the caller's `with` never gets to close.
            try:
                saved = store.persist(config, getattr(tx, "room", None), mode=mode)
            except Exception:
                self.close()
                raise
            log.info("saved %s (%s) to %s", saved.name, saved.manager_url, store.store_path())
        return self

    def open(
        self,
        *,
        timeout: float = 5.0,
        allow_version_skew: bool = False,
        require_state: bool = True,
    ) -> None:
        """Attach the transport this Robot was built over. See :meth:`connect` for
        ``timeout``, ``allow_version_skew`` and ``require_state``."""
        self._reserve_open("open")
        try:
            self._open(
                timeout=timeout, allow_version_skew=allow_version_skew, require_state=require_state
            )
        finally:
            self._opening = False

    def _reserve_open(self, state: str) -> None:
        """Claim the one session open that may run at a time: a second connect() or open()
        while one is running, or on an open session, is a ``RuntimeError``, so two openers
        never share (and leak) a transport and a keepalive."""
        with self._lock:
            if self._opening:
                raise RuntimeError(
                    "another connect() or open() on this Robot is still running; wait for it"
                )
            if not self._closed:
                raise RuntimeError(f"this Robot is already {state}; close() it first")
            self._opening = True

    def _open(self, *, timeout: float, allow_version_skew: bool, require_state: bool) -> None:
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
            self._handshake_error = None
            self._allow_version_skew = allow_version_skew
            self._link_lost = None
            self._end_hold()  # a drive never survives a session
            self._last_alerts, self._last_alerts_at = (), 0.0  # nor do the last session's alerts
            self._generation += 1
            self._last_mode = None
            self._pending.clear()
            self._refusals.clear()
        self._camera._reset()  # nor its frames and audio, on the same transport or a new one
        self._microphone._reset()
        # Armed BEFORE the transport opens: a LiveKit open() waits for media for seconds,
        # and state that arrives meanwhile must be handshaken (or dropped), not stored raw.
        self._late = not require_state
        self._accepting = True
        self._tx.open()
        # Verbs stay refused (NotConnectedError) until the handshake has passed: here, or
        # in _on_state when the session did not wait for the robot.
        if require_state:
            if not self._state_seen.wait(timeout):
                self._accepting = False
                self._tx.close()
                # A transport MAY carry a ``silence_hint``: what to check when a connect
                # hears no state on its wire. It knows the setup that feeds it; this class
                # does not.
                raise ConnectError(
                    f"no state from the robot at {self._tx.endpoint} within {timeout:.1f}s. "
                    f"{getattr(self._tx, 'silence_hint', '')}"
                )
            first = self._state
            assert first is not None
            try:
                self._handshake(first)
            except ProtocolMismatchError:
                self._accepting = False
                self._tx.close()
                raise
        self._closed = False
        _open_robots.add(self)
        # One stop flag per session. close() may run ON the keepalive thread (an on_link_lost
        # handler that reconnects); a shared flag that open() cleared would un-signal that
        # thread's exit before it woke, leaving two keepalives re-sending into Asimov Edge.
        stop = threading.Event()
        self._stop = stop
        self._keepalive = threading.Thread(
            target=self._keepalive_loop, args=(stop,), name="menlo-sdk-keepalive", daemon=True
        )
        self._keepalive.start()

    def _handshake(self, first: State) -> None:
        """What the first state sample settles: the protocol version this SDK can speak,
        the body's size and the capabilities the robot reports about itself."""
        if first.protocol_version != robots.PROTOCOL_VERSION and not self._allow_version_skew:
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

    def _late_handshake(self, first: State) -> bool:
        """The handshake for a session opened with ``require_state=False``, run on the
        reader thread when the robot first reports. A protocol mismatch cannot be raised
        to anyone here, so it is kept and raised by ``get_state()``, the verbs and the waits."""
        with self._lock:
            if self._info is not None:
                return True
            if not self._accepting:
                return False
            try:
                self._handshake(first)
            except ProtocolMismatchError as exc:
                if self._handshake_error is None:
                    self._handshake_error = exc
                    log.error("%s", exc)
                return False
            return True

    def _no_state(self) -> MenloError:
        """Why there is no state to read: the session never heard the robot, or heard one
        this SDK cannot talk to."""
        if self._handshake_error is not None:
            return self._handshake_error
        if self._closed:
            return NotConnectedError("not connected: no state received")
        return NotConnectedError(
            "the robot has not reported state (this session did not wait for it: "
            "require_state=False). Media works now; robot.get_state(), robot.info and damp() "
            "need the firmware's state stream"
        )

    def _only_from[**P](self, tx: Transport, handler: Callable[P, None]) -> Callable[P, None]:
        """Wrap a transport callback so only the transport this Robot is CURRENTLY on may call
        it. A closed transport keeps its subscriber list and its reader thread is joined
        best-effort, so a late sample from the previous transport must not become this session's
        state, frame or handshake."""

        def guarded(*args: P.args, **kwargs: P.kwargs) -> None:
            if self._transport is tx:
                handler(*args, **kwargs)

        return guarded

    def close(self) -> None:
        """Zero velocity if one is held, stop re-sending a held trajectory, then drop the link.
        Idempotent; never raises. A trajectory that is no longer re-sent is DAMPed by Asimov Edge
        two seconds later: there is no neutral setpoint the SDK could send instead.

        A ``duration`` hold that has not expired is cut short here, zero and all: leaving a
        ``with`` block ends the walk. A script that wants the whole hold waits for it, with
        ``set_velocity(..., wait=True)`` or a ``time.sleep(duration)``, before it leaves."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._accepting = False
            _open_robots.discard(self)
            tx = self._transport  # pinned here: a connect() racing us may swap the transport
            had_velocity = self._latched is not None or self._streamed
            self._end_hold()
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
            raise self._no_state()
        return self._info

    @property
    def connected(self) -> bool:
        return not self._closed and self._link_lost is None

    def has(self, capability: Capability | str) -> bool:
        """Does this robot, over this transport, provide ``capability`` **right now**?

        Reads the transport's **live** set rather than ``info.capabilities``, which is a
        snapshot taken at connect (``RobotInfo`` is frozen). Through a LiveKit room a camera goes
        away mid-session when its track unsubscribes, and a script gating on ``has()``
        (the documented safe pattern) should then skip cleanly instead of taking the
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
        command sent to a JSON-lines file. See :mod:`menlo.asimov.recording`."""
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

    def get_state(self) -> State:
        """Return the latest state sample the robot sent: an immutable snapshot. Read it
        once per step and use the fields from that one object. No network call; check
        ``age_s`` before trusting it. Raises :class:`NotConnectedError` when the robot has
        not reported state, and :class:`ProtocolMismatchError` for a robot this SDK cannot
        talk to."""
        s = self._state
        if s is None or self._handshake_error is not None:
            # A robot this SDK cannot talk to is an error, not a sample, even if something
            # was cached before the mismatch was known.
            raise self._no_state()
        return s

    @property
    def armed(self) -> bool | None:
        """Whether the firmware accepts MOVE now, as far as the state stream shows.

        ``True`` in MOVE, and in STAND once the robot has been seen upright (tilt under
        30 degrees) for 0.5 s since STAND began; ``False`` in DAMP and before that; ``None``
        with no state, in an unknown robot mode, or in STAND when the robot reports no
        gravity vector or the Robot is closed. A velocity sent before the robot is armed is
        neither refused nor reported: the robot stays in STAND. A held velocity is re-sent,
        so the robot enters MOVE once it arms; a one-shot ``balance()`` is not."""
        s = self._state
        if s is None or self._closed or self._handshake_error is not None:
            return None
        if s.mode is Mode.MOVE:
            return True
        if s.mode is Mode.STAND:
            if self._armed:
                return True
            return None if s.gravity is None else False
        return False if s.mode in (Mode.DAMP, Mode.FAULT_DAMP) else None

    def preflight(self, action: Action = "move") -> Preflight:
        """Report what the robot's latest state says about ``action`` (``"stand"``,
        ``"move"`` for ``balance``/``set_velocity``, or ``"trajectory"`` for
        ``trajectory``/``set_joints``). Sends nothing, never waits and never raises for a
        robot condition. Information: blocking problems are the ones a command refuses on
        (``no_state`` and ``stale_state`` after its ``timeout``, with :class:`NotReadyError`;
        for ``not_connected`` a command raises :class:`NotConnectedError`,
        :class:`LinkLostError` or :class:`ProtocolMismatchError` at once); the others
        (``faulted``, ``alerts``, ``not_armed``) are facts, and nothing in the SDK acts on
        them. See :mod:`menlo.asimov._preflight` for the codes."""
        no_state: Problem | None = None
        if self._closed:
            no_state = Problem("not_connected", "this Robot is not connected", True)
        elif self._link_lost is not None:
            no_state = Problem("not_connected", f"the link was lost: {self._link_lost}", True)
        elif self._handshake_error is not None:
            no_state = Problem("not_connected", str(self._handshake_error), True)
        s = self._state if no_state is None else None
        return _evaluate_preflight(action, s, armed=self.armed, no_state=no_state)

    def _usable(self) -> None:
        """Raise what a command raises, at once, on a session that cannot send:
        :class:`NotConnectedError` for a closed Robot, the :class:`LinkLostError` for a lost
        link, the :class:`ProtocolMismatchError` for a robot this SDK cannot talk to. A
        session that has not heard the robot is none of these: the command waits for its
        first sample."""
        if self._closed:
            raise NotConnectedError("this Robot is closed")
        if self._link_lost is not None:
            raise self._link_lost
        if self._handshake_error is not None:
            raise self._handshake_error

    def _ready(self, action: Action, timeout: float) -> Preflight:
        """The one check a command runs before it sends: is there live state to send
        against? Returns at once when there is; waits up to ``timeout`` for a sample (and,
        for ``move`` and ``trajectory``, for the report of a STAND sent moments ago); raises
        :class:`NotReadyError` past it, and then nothing was sent. A closed Robot, a lost
        link or a protocol mismatch raises its own error at once. What the robot reports
        (a fault, an alert, a hot joint, the robot mode) never refuses here. From a state
        callback it checks once: waiting there would hold up the state it waits for."""
        deadline = time.monotonic() + (0.0 if _in_callback() else timeout)
        while True:
            self._usable()
            check = self.preflight(action)
            pending = action != "stand" and self._stand_pending()
            if check.ok and not pending:
                return check
            if not all(p.code in TRANSIENT for p in check.blocking):
                raise NotReadyError(check.explain(), action=action, preflight=check)
            if time.monotonic() >= deadline:
                if check.ok:
                    return check  # the STAND report did not come: send anyway
                raise NotReadyError(
                    f"{check.explain()} (waited {timeout:.1f} s)", action=action, preflight=check
                )
            time.sleep(READY_POLL_S)

    def _stand_pending(self) -> bool:
        """A STAND went out moments ago and the robot still reports DAMP: the report is on
        its way, so a command waits for it before it sends."""
        last, s = self._last_mode, self._state
        return (
            last is not None
            and last.name == "stand"
            and time.monotonic() - last.sent_at < STAND_REPORT_S
            and s is not None
            and s.mode is Mode.DAMP
        )

    # ── verbs ────────────────────────────────────────────────────────────────
    def set_velocity(
        self,
        vx: float = 0.0,
        vy: float = 0.0,
        vyaw: float = 0.0,
        *,
        duration: float | None = None,
        wait: bool | None = None,
        hold: bool = True,
        timeout: float | None = None,
    ) -> Sent:
        """Walk: ``vx`` forward, ``vy`` left (m/s), ``vyaw`` counter-clockwise
        (rad/s). Held and re-sent at 10 Hz until another verb, or until ``duration``
        seconds pass (then zero velocity is sent and the robot balances in place). Clamped
        to ``limits``; the returned ``Sent.command`` is what actually went out and
        ``Sent.clamped`` says whether it differs from what you asked.

        Sent in any robot mode; the firmware decides. In MOVE the robot walks. In STAND an
        armed robot enters MOVE and walks (before it arms the robot stays in STAND; the hold
        re-sends the velocity, so it enters MOVE once armed). In DAMP the firmware does not
        walk. The SDK refuses only without live state: it waits up to ``timeout`` seconds
        (default 5 s) for a fresh sample and raises :class:`NotReadyError` with nothing sent
        when none comes. A guard on the robot's report (mode, faults, temperatures, battery)
        is the caller's: see ``robot.get_state()``.

        ``wait`` defaults to ``True``: block until the hold has ended and its zero has gone
        out, or until another verb superseded it. That needs a ``duration``; without one
        this raises ``ValueError`` with nothing sent. A fault-DAMP, reported before the
        velocity went out or during the hold, ends the hold with nothing more sent, and the
        wait raises :class:`RobotFaultedError`.
        ``wait=False`` returns once sent and keeps the velocity until the next command, or
        until ``duration`` if given: a
        control loop sends a short ``duration`` on every tick, so the robot stops when the
        loop does. The next verb (or ``close()``, so the end of a ``with`` block) cuts a
        hold short. A zero velocity with ``wait=True`` blocks for ``duration``, and ends
        early as a hold's wait does: another verb returns it, a fault-DAMP raises
        :class:`RobotFaultedError`, a lost link or ``close()`` raises their errors.

        ``hold=False`` is for a loop that clocks its own velocity commands: it sends exactly
        one velocity command now and nothing in the background, and ends any hold that was
        running so an older velocity is never re-sent. The live-state check is made once,
        as in :meth:`trajectory`: it adds no delay on a live stream and raises at once on a
        stale one (pass ``timeout`` to wait that long). There is nothing to wait for
        and nothing to bound, so ``duration`` or ``wait=True`` with ``hold=False`` is a
        ``ValueError``. If your loop stops, nothing re-sends its last velocity: on ``udp``
        and ``hybrid``, Asimov Edge zeroes velocity 2 s after the last packet and the robot
        keeps balancing in MOVE; on ``livekit``, Asimov Edge stops it when the SDK sends
        zero or leaves the room. ``balance()``, ``close()`` and the exit hook still send
        zero after a nonzero packet. On ``livekit``, when the robot reads the SDK's command
        track, a non-zero velocity sent this way is one lossy frame: a lost one is replaced
        by your next, so send at a steady rate rather than only on a change, and end with
        zero, which always goes as a reliable packet."""
        if not hold:
            if duration is not None:
                raise ValueError(
                    "set_velocity(hold=False) sends one velocity command and holds nothing, so "
                    "it takes no duration=; call it again from your loop, or use hold=True"
                )
            if wait:
                raise ValueError(
                    "set_velocity(hold=False) sends one velocity command and has nothing to wait "
                    "for; leave wait= out"
                )
            asked = Velocity(vx, vy, vyaw)
            self._ready("move", _nonnegative_finite("timeout", timeout or 0.0))
            return self._drive("set_velocity", asked, None, hold=False)[0]
        wait = True if wait is None else wait
        _no_wait_in_callback("set_velocity", wait)
        if wait and duration is None:
            raise ValueError(
                "set_velocity() needs a duration= to wait for, or wait=False to keep the "
                "velocity until the next command"
            )
        if duration is not None:
            _positive_finite("duration", duration)
        asked = Velocity(vx, vy, vyaw)  # a non-finite speed is a ValueError before the check
        limit = READY_TIMEOUT_S if timeout is None else _nonnegative_finite("timeout", timeout)
        self._ready("move", limit)
        sent, done = self._drive("set_velocity", asked, duration)
        if wait:
            assert duration is not None
            self._await_hold(done, duration, sent)
        return sent

    def _drive(
        self, name: str, asked: Velocity, duration: float | None, *, hold: bool = True
    ) -> tuple[Sent, threading.Event | None]:
        v = asked.clamped(self.limits)
        # Bump and send under ONE lock hold: a verb issued by the caller must never be
        # dropped as "superseded" by a concurrent verb. The generation fence exists for the
        # keepalive's re-sends, which are the only sends that may legitimately go stale.
        with self._lock:
            self._generation += 1
            gen = self._generation
            self._end_hold()
            self._latched = None if v.is_zero or not hold else v
            self._streamed = not hold and not v.is_zero
            if duration is not None:
                # A zero has nothing to re-send, but its event still tells a waiting caller
                # that another verb, close() or a lost link ended the time.
                self._hold_done = threading.Event()
                if not v.is_zero:
                    self._latch_deadline = time.monotonic() + duration
            self._last_mode = None  # a new drive supersedes whatever posture change preceded it
            done = self._hold_done
            sent = self._send(name, v, gen, clamped=(v != asked))
            s = self._state
            if self._latched is not None and s is not None and s.faulted and s.mode in _DOWN:
                # The robot reported its fault-DAMP before this hold began, so that sample's
                # callback found nothing to end. The velocity went out once; the hold ends
                # here, with no expiry zero, and its waiter raises the fault.
                self._release_hold("the robot is fault-DAMPed", fence=False, fault=s)
            return sent, done

    def _end_hold(self) -> None:
        """Under the lock: whatever bounded hold was in force is over (superseded, stopped,
        closed or lost) and anyone blocked in ``wait=True`` may go."""
        self._latched, self._latch_deadline = None, None
        self._streamed = False
        done, self._hold_done = self._hold_done, None
        if done is not None:
            done.set()

    def _await_hold(self, done: threading.Event | None, duration: float, sent: Sent) -> None:
        assert done is not None
        started = time.monotonic()
        if isinstance(sent.command, Velocity) and sent.command.is_zero:
            # Nothing is held: the robot balances for `duration`, and the wait watches the
            # session and the robot's report as a hold's wait does.
            end = started + duration
            while not done.wait(max(0.0, min(READY_POLL_S, end - time.monotonic()))):
                if self._closed or self._link_lost is not None:
                    break
                s = self._state
                if s is not None and s.faulted and s.mode in _DOWN:
                    raise self._walk_faulted(s, started, duration, sent)
                if time.monotonic() >= end:
                    return
        else:
            # The keepalive ticks at KEEPALIVE_HZ, so the zero leaves within a tick of the
            # deadline; the rest of the budget is slack for a loaded machine. The event is
            # set on every path that ends a hold, so running out of it means the keepalive
            # is stalled or gone: the hold is ended and its zero sent from here.
            budget = duration + 2.0 / KEEPALIVE_HZ + 1.0
            if not done.wait(budget):
                with self._lock:
                    stalled = self._hold_done is done
                    if stalled:
                        self._generation += 1
                        gen = self._generation
                        self._end_hold()
                # Not stalled: the keepalive already sent the expiry zero and is releasing
                # the wait, so the hold ran its course.
                if stalled:
                    # Past the recording hook: the stalled keepalive may be holding the
                    # recording's lock, and this zero must not wait for it.
                    self._send("set_velocity", Velocity(), gen, hook=False)
                    raise WaitTimeoutError(
                        f"the {duration:.1f} s hold had not ended after {budget:.1f} s: the "
                        "SDK's keepalive thread stalled (a recording write that blocks, for "
                        "example). The hold is ended here and a zero velocity was sent",
                        last=self._state,
                        sent=sent,
                    )
        with self._lock:
            lost, closed = self._link_lost, self._closed
            faulted = self._faulted_holds.pop(done, None)
        if lost is not None:
            raise lost
        if faulted is not None:
            raise self._walk_faulted(faulted, started, duration, sent)
        if closed:
            raise NotConnectedError("this Robot was closed before the hold ended")

    def _walk_faulted(
        self, s: State, started: float, duration: float, sent: Sent
    ) -> RobotFaultedError:
        names = ", ".join(fault_names(s)) or f"robot mode {s.mode.name}"
        check = self.preflight("move")
        return RobotFaultedError(
            f"the walk ended after {time.monotonic() - started:.1f} s of {duration:.1f} s: "
            f"the firmware latched DAMP ({names}); it stays latched until the firmware "
            "restarts, and nothing the SDK sends clears it",
            state=s,
            action="move",
            preflight=check,
            problems=_faults(check),
            sent=sent,
        )

    def balance(self, *, timeout: float = BALANCE_TIMEOUT_S, wait: bool = True) -> Sent:
        """Balance in place: the robot is in MOVE at zero velocity, and the walking policy
        keeps it upright. For a free-standing robot that IS how it stands still, so a walk
        ends here. Sent once, in any robot mode; not an emergency stop.

        In MOVE: ends any velocity hold and sends zero velocity at once, callable from any
        thread while a ``wait=False`` hold runs.

        Outside MOVE: waits up to ``timeout`` for live state (:class:`NotReadyError`, with
        nothing sent, when none comes), then sends zero velocity. In STAND an armed robot
        enters MOVE; a zero velocity that arrives before it arms leaves it in STAND (``stand()``
        returns once armed, so ``stand()`` then ``balance()`` enters MOVE).
        With ``wait``, returns once the robot reports MOVE; the whole call takes at most
        ``timeout`` seconds, past it :class:`WaitTimeoutError`. In DAMP the firmware does not
        enter MOVE, so the wait fails at once with :class:`WaitTimeoutError` (``stand()``
        first); a latched fault raises :class:`RobotFaultedError`. The zero velocity was sent
        either way. ``wait=False`` returns once sent.

        The robot mode decides, not what this session sent before: outside MOVE a velocity
        hold still in force ends at once (nothing more is re-sent)."""
        _nonnegative_finite("timeout", timeout)
        deadline = time.monotonic() + timeout
        self._usable()
        with self._lock:
            s = self._state
            if s is not None and s.mode is Mode.MOVE:
                return self._drive("balance", Velocity(), None)[0]
        _no_wait_in_callback("balance", wait)
        with self._lock:
            # Not in MOVE: a velocity sent earlier does not make this call a MOVE-at-zero.
            # Its hold ends here, so the keepalive does not re-send it while the live-state
            # check runs; close() still owes the robot a zero for it if the check refuses.
            self._generation += 1
            owed = self._latched is not None or self._streamed
            self._end_hold()
            self._streamed = owed
        check = self._ready("move", timeout)
        sent, _ = self._drive("balance", Velocity(), None)
        if not wait or (check.state is not None and check.state.mode is Mode.MOVE):
            return sent
        s = self._state
        if s is not None and s.mode is Mode.DAMP and not s.faulted:
            # The firmware enters MOVE from STAND only: no wait can succeed from here.
            raise WaitTimeoutError(
                "sent zero velocity, but the robot is in DAMP: stand() first",
                last=s,
                sent=sent,
            )
        try:
            self._wait(
                lambda st: st.mode is Mode.MOVE,
                timeout=max(0.0, deadline - time.monotonic()),
                action="move",
                sent=sent,
            )
        except StateStaleError:
            raise
        except WaitTimeoutError as exc:
            last = exc.last
            if last is not None and last.mode is Mode.DAMP:
                why = "the robot is in DAMP: stand() first"
            elif last is not None and last.mode is Mode.STAND and check.armed is not True:
                why = (
                    "the robot reports STAND and was not armed when the zero velocity was "
                    "sent: the firmware enters MOVE once STAND has been held upright (tilt "
                    "under 30 deg) for 0.5 s, and a velocity that arrives before then leaves "
                    "it in STAND. Wait until robot.armed is True, then balance() again"
                )
            else:
                now = last.mode.name if last is not None else "no state"
                why = (
                    f"the robot still reports {now}. Another controller may hold it (the "
                    "Asimov Manager cockpit or a paired gamepad), and a command it outranks "
                    "has no effect"
                )
            raise WaitTimeoutError(
                f"sent zero velocity, but the robot was not in MOVE within {timeout:.1f} s: {why}",
                last=last,
                sent=sent,
            ) from None
        return sent

    def stand(self, *, timeout: float = STAND_TIMEOUT_S, wait: bool = True) -> Sent:
        """Put the robot in STAND: the actuators hold a standing pose, with no balancing. By
        default, return once the robot is in STAND and armed, so ``balance()`` can put it
        in MOVE.

        Sent from any robot mode. The SDK refuses only without live state: it waits up to
        ``timeout`` for a fresh sample and raises :class:`NotReadyError`, with nothing sent,
        when none comes. Then sends STAND once and, with ``wait``, returns only when the
        robot reports STAND and is armed (STAND held upright for 0.5 s), so the next
        ``balance()`` enters MOVE. The whole call takes at most ``timeout`` seconds: past it,
        :class:`WaitTimeoutError`; a latched fault, before or while standing,
        :class:`RobotFaultedError` (the STAND was sent; the firmware keeps the fault).
        ``wait=False`` returns once sent.

        STAND blends every joint to a fixed pose and holds it there with position gains.
        There is **no balance loop**: the robot does not catch itself. Use it to bring the
        robot out of DAMP (DAMP -> STAND, then ``balance()`` for MOVE), or from MOVE with the
        robot hanging from its gantry hook or seated on a stool or bench. Asking a
        free-standing biped to stiffen after walking tips it over, and a fall latches a
        fault-DAMP that lasts until the firmware restarts; the SDK sends STAND from MOVE as
        asked, so support the robot first. To stand still after walking, stay in MOVE at
        zero velocity (``balance()``), where the policy keeps balancing. Joint control
        (``set_joints``, ``trajectory``) ends with ``damp()``, with the robot still
        supported."""
        _nonnegative_finite("timeout", timeout)
        _no_wait_in_callback("stand", wait)
        deadline = time.monotonic() + timeout
        self._ready("stand", timeout)
        sent = self._once("stand", ModeCommand("stand"))
        if wait:
            try:
                self._wait(
                    lambda s: s.mode is Mode.STAND and self.armed is not False,
                    timeout=max(0.0, deadline - time.monotonic()),
                    action="stand",
                    sent=sent,
                )
            except StateStaleError:
                raise
            except WaitTimeoutError as exc:
                last = exc.last
                if last is not None and last.mode is Mode.STAND:
                    why = (
                        "the robot reports STAND but has not armed: the firmware arms once "
                        "STAND has been held upright (tilt under 30 deg) for 0.5 s. Check "
                        "that the robot is upright"
                    )
                else:
                    now = last.mode.name if last is not None else "no state"
                    why = (
                        f"the robot still reports {now}. Another controller may hold it "
                        "(the Asimov Manager cockpit or a paired gamepad), and a command it "
                        "outranks has no effect"
                    )
                raise WaitTimeoutError(
                    f"sent STAND, but the robot was not armed in STAND within {timeout:.1f} s: "
                    f"{why}",
                    last=last,
                    sent=sent,
                ) from None
        return sent

    def damp(self, *, timeout: float = DAMP_TIMEOUT_S, wait: bool = True) -> Sent:
        """Put the robot in DAMP: every actuator stops holding its position, so a standing
        robot falls. Sent at once, whatever the robot's state, with no live-state check. With
        ``wait``, returns once the robot reports DAMP (or FAULT_DAMP, or a latched fault:
        no actuator holds a position either way), and raises :class:`WaitTimeoutError` after
        ``timeout`` seconds. ``wait=False`` returns once sent, and so does a call from a
        state callback (``on_alert`` damping on an alert): that thread delivers the state
        the wait reads.

        A software command, not an emergency stop: for that, use the E-Stop in Asimov
        Manager, or cut power at the battery unit. A fault latched by the firmware keeps the
        robot in DAMP until the firmware restarts."""
        _nonnegative_finite("timeout", timeout)
        sent = self._once("damp", ModeCommand("damp"))
        if wait and not _in_callback():
            try:
                self._wait(
                    lambda s: s.mode in (Mode.DAMP, Mode.FAULT_DAMP),
                    timeout=timeout,
                    sent=sent,
                    fault_ok=True,
                )
            except StateStaleError:
                raise
            except WaitTimeoutError as exc:
                now = exc.last.mode.name if exc.last is not None else "no state"
                raise WaitTimeoutError(
                    f"sent DAMP, but the robot still reports {now} after {timeout:.1f} s. "
                    "Another controller may hold it (the Asimov Manager cockpit or a paired "
                    "gamepad); if the robot must stop, use the E-Stop in Asimov Manager",
                    last=exc.last,
                    sent=sent,
                ) from None
        return sent

    def trajectory(
        self,
        positions: Iterable[float],
        *,
        kp: tuple[float, ...] | list[float] | None = None,
        kd: tuple[float, ...] | list[float] | None = None,
        timeout: float = 0.0,
    ) -> Sent:
        """The protocol's raw joint command: one set of joint targets for every motor
        (radians, firmware order), sent once, for a loop that clocks its own setpoints.
        Every joint goes under position control and the walking policy is off, so a
        standing biped will not balance itself (see :meth:`set_joints`). Asimov Edge drives
        a trajectory for two seconds after the last setpoint and then DAMPs, so clock these
        yourself or use :meth:`set_joints`.
        Without ``kp``/``kd``, Asimov Edge applies its own per-joint gain table. A ``kp``/``kd``
        entry of zero or less means the firmware substitutes its DAMP gains for that joint,
        so the joint does not hold a position. ``ValueError`` unless
        ``len(positions) == info.dof``, when only one of ``kp``/``kd`` is given (Asimov Edge
        ignores a lone gain), or when a biped ankle target
        is outside the pitch and roll the firmware limits it to (it would be clamped).

        Sent in any robot mode; the firmware decides. The SDK checks only for live state:
        one cached sample, no delay. It is made once: a loop calling this at 50 Hz gets
        :class:`NotReadyError` at once, with nothing sent, on a stale stream, rather than a
        loop that stalls. Pass ``timeout`` to wait that long for a fresh sample. On
        ``livekit``, when the robot reads the SDK's command track, each one is a lossy frame:
        a lost one is replaced by your next."""
        pos = tuple(float(p) for p in positions)
        if self._info is not None and len(pos) != self._info.dof:
            raise ValueError(
                f"trajectory has {len(pos)} positions; this robot has {self._info.dof} motors"
            )
        robots.check_ankle_limits(pos)
        t = Trajectory(
            pos,
            tuple(float(x) for x in kp) if kp is not None else None,
            tuple(float(x) for x in kd) if kd is not None else None,
        )
        self._ready("trajectory", _nonnegative_finite("timeout", timeout))
        return self._once("trajectory", t)

    def set_joints(
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
        """Move every joint smoothly from where it IS to ``positions`` over ``duration``
        seconds, return once there, then hold the target until another command.

        A trajectory puts EVERY joint under position control with the walking policy off:
        the robot does not balance itself while one is in force. On a standing biped, use
        this only with the robot supported, or with gains (``kp``/``kd``) known to hold the
        legs; a fall latches a fault-DAMP that lasts until the firmware restarts.

        Interpolates (minimum-jerk) from the current reported joint positions and clocks
        ``trajectory()`` setpoints at ``hz`` from a background thread, then HOLDS the target
        by re-sending it at the keepalive rate until another verb takes over: Asimov Edge
        DAMPs a trajectory two seconds after the last setpoint, so a reached pose is kept
        alive the way a velocity is. ``stand()`` (or any other verb) ends the hold. With
        ``wait``, blocks until every joint is within ``tolerance`` radians of the target, or
        raises :class:`WaitTimeoutError`; the target is still held after that until another
        verb. Every argument is validated before the first setpoint leaves: a biped ankle
        target outside the firmware's ankle pitch and roll limits raises ``ValueError``, as
        in :meth:`trajectory`. Returns the ``Sent`` of the first setpoint.

        Sent in any robot mode; the firmware decides. Before the first setpoint it waits for
        live state, as :meth:`trajectory` does, and raises :class:`NotReadyError` with
        nothing sent when none comes. ``timeout`` bounds the whole call, the live-state
        check and the wait for the target; left ``None``, the check may wait up to 5 s and
        the target ``duration + 2`` s.
        """
        target = tuple(float(p) for p in positions)
        _positive_finite("duration", duration)
        _positive_finite("hz", hz)
        _nonnegative_finite("tolerance", tolerance)
        if timeout is not None:
            _nonnegative_finite("timeout", timeout)
        _no_wait_in_callback("set_joints", wait)
        begun = time.monotonic()
        # The live-state check also guarantees a fresh pose to plan from.
        self._ready("trajectory", READY_TIMEOUT_S if timeout is None else timeout)
        start = self.get_state().joint_pos
        if len(target) != len(start):
            raise ValueError(
                f"set_joints has {len(target)} positions; this robot reports {len(start)}"
            )
        robots.check_ankle_limits(target)
        kp_t = tuple(float(x) for x in kp) if kp is not None else None
        kd_t = tuple(float(x) for x in kd) if kd is not None else None
        steps = max(1, round(duration * hz))
        period = 1.0 / hz

        def blend(i: int) -> tuple[float, ...]:
            t = i / steps
            a = 10 * t**3 - 15 * t**4 + 6 * t**5  # minimum jerk, 0→1
            return tuple(s0 + (s1 - s0) * a for s0, s1 in zip(start, target, strict=True))

        # Built before anything changes hands: a gain it rejects (ValueError) leaves the
        # velocity or motion in force untouched, and close() still zeroes a held velocity.
        opening = Trajectory(blend(1), kp_t, kd_t)
        with self._lock:
            # Bump, send the first setpoint and record the generation in ONE lock hold: a
            # verb landing between them would otherwise leave this motion running under the
            # newer generation and let stale setpoints follow the takeover command.
            self._generation += 1
            gen = self._generation
            self._end_hold()
            first = self._send("trajectory", opening, gen)
            self._last_mode = first

        def run() -> None:
            i = 2
            while True:
                # Clock the motion at `hz`; once the target is reached keep re-sending it at
                # the keepalive rate: Asimov Edge DAMPs a trajectory two seconds after the last
                # setpoint, so a reached pose must be held until another verb takes over.
                time.sleep(period if i <= steps else 1.0 / KEEPALIVE_HZ)
                with self._lock:
                    if self._generation != gen or self._closed:
                        return  # superseded by another verb, or closed
                try:
                    self._send("trajectory", Trajectory(blend(min(i, steps)), kp_t, kd_t), gen)
                except MenloError:
                    return
                i += 1

        threading.Thread(target=run, name="menlo-sdk-set-joints", daemon=True).start()
        if wait:
            limit = (
                duration + 2.0
                if timeout is None
                else max(0.0, timeout - (time.monotonic() - begun))
            )
            try:
                self._wait(
                    lambda st: all(
                        abs(p - q) <= tolerance for p, q in zip(st.joint_pos, target, strict=False)
                    ),
                    timeout=limit,
                    action="trajectory",
                    sent=first,
                )
            except StateStaleError:
                raise
            except WaitTimeoutError as exc:
                raise WaitTimeoutError(
                    f"set_joints: the joints did not come within {tolerance} rad of the target in "
                    f"{limit:.1f} s; the target is still held. Check that nothing blocks the "
                    "joints, or raise timeout or tolerance",
                    last=exc.last,
                    sent=first,
                ) from None
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
        """Block until the robot's OWN state satisfies ``predicate``, and return that state.

        Ends early, with a typed error, when the answer can no longer come: the stream
        went quiet (:class:`StateStaleError`); the firmware fault-DAMPed
        (:class:`RobotFaultedError`, checked before the predicate, so a fault is never read
        as success); or the ``stand``/``damp``/``trajectory`` still in force (the last verb
        sent, when it was one of those) was refused (:class:`CommandRefusedError`). Past
        ``timeout``, :class:`WaitTimeoutError`.
        """
        return self._wait(predicate, timeout=timeout, stale_after=stale_after, poll=poll)

    def _wait(
        self,
        predicate: Callable[[State], bool],
        *,
        timeout: float,
        stale_after: float | None = None,
        poll: float = READY_POLL_S,
        action: Action | None = None,
        sent: Sent | None = None,
        fault_ok: bool = False,
    ) -> State:
        """``wait_until``, for a command too: ``action`` and ``sent`` go into the errors, and
        ``fault_ok`` lets a latched fault satisfy the wait (``damp()``: no actuator holds a
        position either way)."""
        _no_wait_in_callback("wait_until" if sent is None else sent.name, True)
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
            if self._handshake_error is not None:
                raise self._handshake_error
            s = self._state
            if s is not None:
                if s.age_s > stale:
                    raise StateStaleError(
                        f"the robot has not reported for {s.age_s:.2f}s (limit {stale:.2f}s); "
                        "treating it as absent, not slow. Check the network link to the robot",
                        last=s,
                        sent=sent,
                    )
                if s.faulted and s.mode in _DOWN:
                    if fault_ok:
                        return s
                    names = ", ".join(fault_names(s)) or f"robot mode {s.mode.name}"
                    check = self.preflight(action) if action is not None else None
                    raise RobotFaultedError(
                        f"the firmware latched DAMP ({names}); it stays latched until the "
                        "firmware restarts, and nothing the SDK sends clears it",
                        state=s,
                        action=action,
                        preflight=check,
                        problems=_faults(check) if check is not None else None,
                        sent=sent,
                    )
                if predicate(s):
                    return s
                last = s
            pending_mode = self._last_mode  # only the command still in force can refuse a wait
            if pending_mode is not None and isinstance(pending_mode.outcome, Refused):
                raise CommandRefusedError(pending_mode.outcome)
            if time.monotonic() >= deadline:
                raise WaitTimeoutError(
                    f"the condition was not met within {timeout:.1f} s"
                    + (
                        f"; robot mode {last.mode.name}, last sample {last.age_s:.2f} s old"
                        if last
                        else "; the robot has not reported"
                    ),
                    last=last,
                    sent=sent,
                )
            time.sleep(poll)

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
            self._end_hold()  # a posture change ends whatever drive was in force
            sent = self._send(name, command, gen)
            self._last_mode = sent
            return sent

    def _send(
        self, name: str, command: Command, gen: int, *, clamped: bool = False, hook: bool = True
    ) -> Sent:
        """Send ``command`` unless ``gen`` was superseded. ``hook=False`` skips the recording
        hook, for a send that must not wait on it."""
        lost: LinkLostError | None = None
        with self._lock:
            # Checked under the lock so a close() racing on another thread cannot slip a
            # command onto a transport that is being torn down.
            if self._closed:
                raise NotConnectedError("this Robot is closed")
            if self._link_lost is not None:
                raise self._link_lost
            if self._info is None:
                raise self._no_state()
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
                on_sent = self._on_sent if hook else None
        if lost is not None:
            # Outside the lock: on_link_lost may block on things that need a verb.
            self._mark_link_lost(lost)
            raise lost
        if on_sent is not None:
            self._call(on_sent, sent)
        return sent

    def _keepalive_loop(self, stop: threading.Event) -> None:
        interval = 1.0 / KEEPALIVE_HZ
        last_seen = time.monotonic()
        while not stop.wait(interval):
            s = self._state
            if s is not None:
                last_seen = s.received_at
            elif self._info is None:
                # A session that did not wait for the robot and has not heard it: a
                # stream that never started is not one that went quiet, and no verb has
                # been accepted, so there is nothing to zero.
                last_seen = time.monotonic()
                continue
            if time.monotonic() - last_seen > self.link_timeout and self._link_lost is None:
                quiet = f"no state from {self._tx.endpoint} for {self.link_timeout:.1f}s"

                def lost(zeroed: bool, quiet: str = quiet) -> LinkLostError:
                    zero = "a zero velocity was sent and " if zeroed else ""
                    return LinkLostError(
                        f"{quiet}; {zero}any held trajectory stopped; "
                        "close() and connect() again to reconnect"
                    )

                self._mark_link_lost(lost)
            expired: threading.Event | None = None
            with self._lock:
                v, gen, deadline = self._latched, self._generation, self._latch_deadline
                if v is not None and deadline is not None and time.monotonic() >= deadline:
                    # The bounded hold expired: send zero once and stop holding. Whoever is
                    # blocked in wait=True is released AFTER that zero has gone out.
                    expired, self._hold_done = self._hold_done, None
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
            finally:
                if expired is not None:
                    expired.set()

    def _mark_link_lost(self, exc: LinkLostError | Callable[[bool], LinkLostError]) -> None:
        """End the session: ``exc`` is raised by every verb and wait from now on. A callable
        is given whether a zero velocity goes out, so the message says what was sent."""
        with self._lock:
            if self._link_lost is not None:
                return
            had_velocity = self._latched is not None or self._streamed
            if not isinstance(exc, LinkLostError):
                exc = exc(had_velocity)
            self._link_lost = exc
            self._end_hold()
            self._generation += 1
        if had_velocity:
            # The STATE stream died; the COMMAND path may well still reach Asimov Edge. Do not
            # leave a nonzero velocity as the last word: send the zero, best effort.
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
        self._armed, self._upright_since = False, None

    def _track_arming(self, prev: State | None, state: State, restarted: bool) -> None:
        """Follow the firmware's MOVE gate from the state stream. The firmware disarms on
        every robot mode change except MOVE -> STAND, and arms in STAND once projected
        gravity z has stayed below ``ARM_GRAVITY_Z`` for ``ARM_HOLD_S``. The first sample
        of a session counts as a mode entry: the SDK does not know how long the robot has
        been in STAND, so it counts from the first sample it saw. The hold counts only
        observed time: a sample without gravity, or a gap between samples longer than
        ``ARM_MAX_GAP_S``, restarts it, since the robot may have tilted unseen."""
        mode = state.mode
        if prev is None or restarted or prev.mode is not mode:
            kept = mode is Mode.STAND and prev is not None and prev.mode is Mode.MOVE
            self._armed = mode is Mode.MOVE or (kept and not restarted and self._armed)
            self._upright_since = None
        if mode is not Mode.STAND or self._armed:
            return
        if state.gravity is None:
            self._upright_since = None
            return
        if prev is not None and state.received_at - prev.received_at > ARM_MAX_GAP_S:
            self._upright_since = None
        if state.gravity[2] < ARM_GRAVITY_Z:
            if self._upright_since is None:
                self._upright_since = state.received_at
            elif state.received_at - self._upright_since >= ARM_HOLD_S:
                self._armed = True
        else:
            self._upright_since = None

    # transport callbacks (any thread) ──────────────────────────────────────
    def _on_state(self, state: State) -> None:
        # UDP is unauthenticated: anyone on the network can hit the state port, and protobuf
        # decodes an empty or foreign datagram as a default RobotState. A sample that does
        # not look like THIS robot (no joints; or, once connected, a different protocol
        # version or joint count) must not refresh liveness or become `robot.get_state()`.
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
        # or the caller reads the robot going backwards in time. Half-range compare: a
        # counter that wrapped to 0 is NEWER (difference > 2**31); an unstamped stream (all
        # zeros) differs by 0 and is accepted. A duplicate (same sequence and firmware
        # clock) is dropped: it would refresh the sample's age with nothing observed.
        # But a counter that RESTARTED (the firmware rebooted while Asimov Edge and this
        # session stayed up) is behind by thousands, and dropping until it climbed past
        # the old value would freeze robot.get_state() for minutes. Behind by more than the reorder
        # window, or with the firmware clock a second in the past, is a new stream.
        prev = self._state
        restarted = False
        if prev is not None:
            verdict = self._classify_sample(prev, state)
            if verdict in ("stale", "duplicate"):
                return
            restarted = verdict == "restart"
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
        if (
            self._info is None
            and self._late
            and self._accepting
            and not self._late_handshake(state)
        ):
            return  # a robot this SDK cannot talk to: the mismatch is raised on the next read
        prev = self._state
        self._track_arming(prev, state, restarted)
        self._state = state
        self._state_seen.set()
        # What the robot reports can end a drive too. A rebooted firmware comes up DAMPed
        # and a faulted one DAMPs itself; the last velocity this session latched belongs to
        # a robot that no longer exists, and re-sending MOVE at 10 Hz to the new one is the
        # one thing a script that is busy elsewhere would never want.
        if restarted:
            self._release_hold("the firmware restarted", fence=True)
        elif state.faulted and state.mode in _DOWN:
            self._release_hold("the robot fault-DAMPed", fence=False, fault=state)
        self._fire_state_callbacks(prev, state)

    def _release_hold(self, why: str, *, fence: bool, fault: State | None = None) -> None:
        """The ROBOT ended the drive: drop a latched velocity so the keepalive stops
        re-sending it. Nothing is sent in its place: a MOVE-at-zero would ask a DAMPed or
        booting robot to change mode, which is a decision for the script, not the SDK.
        ``fence`` also bumps the generation so a running ``set_joints`` / re-sent trajectory
        stops; a one-shot event (a restart) may do that, a fault that stays reported for
        seconds must not keep cancelling whatever the script does next. ``fault`` is the
        sample that showed a fault-DAMP: a caller blocked on the hold raises
        :class:`RobotFaultedError` rather than return as if the walk had run its course."""
        with self._lock:
            held = self._latched
            if held is None and not fence:
                # A raw stream (hold=False) has nothing latched, but it is over too: the
                # next balance() must check the DAMPed robot, not send zero to it.
                self._streamed = False
                return
            if fault is not None and held is not None and self._hold_done is not None:
                self._faulted_holds[self._hold_done] = fault
            self._end_hold()
            if fence or held is not None:
                self._generation += 1
        if held is not None:
            log.warning(
                "%s while set_velocity(vx=%.2f, vy=%.2f, vyaw=%.2f) was held: the hold is "
                "released and nothing more is sent",
                why,
                held.vx,
                held.vy,
                held.vyaw,
            )

    @staticmethod
    def _classify_sample(
        prev: State, state: State
    ) -> Literal["newer", "duplicate", "stale", "restart"]:
        """Where ``state`` stands relative to ``prev``: ``"newer"`` (or an unstamped stream:
        accept), ``"duplicate"`` (the same stamped sample again: drop, it observes nothing
        new and must not refresh freshness or arming), ``"stale"`` (a reordered datagram
        from BEHIND: drop) or ``"restart"`` (the first sample of a restarted stream: accept,
        and know it)."""
        back = (prev.sequence - state.sequence) % 2**32
        if (
            back == 0
            and state.fw_timestamp_us == prev.fw_timestamp_us
            and (state.sequence or state.fw_timestamp_us)
        ):
            return "duplicate"
        if not 0 < back < 2**31:
            return "newer"
        if back > STATE_REORDER_WINDOW:
            log.info(
                "state sequence restarted (%d -> %d): the firmware restarted; following it",
                prev.sequence,
                state.sequence,
            )
            return "restart"
        if (
            prev.fw_timestamp_us
            and state.fw_timestamp_us
            and prev.fw_timestamp_us - state.fw_timestamp_us > STATE_CLOCK_RESET_S * 1e6
        ):
            log.info(
                "the firmware clock went back %.1fs: the firmware restarted; following it",
                (prev.fw_timestamp_us - state.fw_timestamp_us) / 1e6,
            )
            return "restart"
        return "stale"

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
        # Restored, not cleared: a callback nests another (a verb in on_alert reaches the
        # recording hook), and the outer one is still on the thread that delivers state.
        outer = _in_callback()
        _CALLBACK.active = True
        try:
            cb(*args)
        except Exception:
            log.exception("%s raised", getattr(cb, "__name__", "callback"))
        finally:
            _CALLBACK.active = outer

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


def _faults(check: Preflight) -> tuple[Problem, ...]:
    """The ``faulted`` fact of ``check``: the problems a :class:`RobotFaultedError` carries."""
    return tuple(p for p in check.problems if p.code == "faulted")


#: Every Robot with an open session. At interpreter exit each is closed, so a script that
#: ends without ``close()`` still sends the zero for a held velocity.
_open_robots: weakref.WeakSet[Robot] = weakref.WeakSet()


def _close_open_robots() -> None:
    for robot in tuple(_open_robots):
        robot.close()


atexit.register(_close_open_robots)
