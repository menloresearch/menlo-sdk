"""Everything this SDK raises, as types.

Three rules, all load-bearing for callers:

* ``except MenloError`` catches everything that is about the ROBOT or the LINK. Caller
  bugs stay builtins (a non-finite velocity is a ``ValueError``, an unknown joint name a
  ``KeyError``) because those are programming errors, not robot conditions, and the
  standard library already has the right names for them.
* A motion command checks the robot before it sends anything. ``stand()``,
  ``balance()`` (from STAND or DAMP), ``set_velocity()``, ``trajectory()`` and ``set_joints()``
  raise :class:`NotReadyError` (or its subclass :class:`RobotFaultedError`) when the robot
  cannot do it now, and then nothing was sent. ``balance()`` in MOVE and ``damp()`` are
  never checked.
* Once a command is sent, the robot's own state says whether it took effect. A command
  that waits for that raises :class:`WaitTimeoutError` when it does not come in time.
  Asimov Edge's per-command verdict is an outcome (see :mod:`menlo.asimov._outcome`) and
  becomes an exception only when the caller asks (``Sent.require()``).

::

    MenloError
    ├── ConnectError
    │   └── ProtocolMismatchError
    ├── NotConnectedError
    ├── LinkLostError
    ├── UnsupportedError
    ├── NotReadyError               the check before sending failed; nothing was sent
    │   └── RobotFaultedError       the firmware latched DAMP (before or after sending)
    ├── WaitTimeoutError            sent, but the robot did not get there in time
    │   └── StateStaleError         the state stream went quiet during a wait
    ├── CommandRefusedError
    └── OutcomeUnknownError
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from menlo.asimov._outcome import Refused, Sent, Unknown
    from menlo.asimov._preflight import Action, Preflight, Problem
    from menlo.asimov._state import State


class MenloError(Exception):
    """Base for every robot- or link-related error this SDK raises."""


# ── the link ─────────────────────────────────────────────────────────────────


class ConnectError(MenloError):
    """No robot answered within the connect timeout, or the transport could not open."""


class ProtocolMismatchError(ConnectError):
    """The robot speaks a different ``asimov.io`` protocol version than this SDK was built
    against. Commands would be silently misread; refuse to start instead."""

    def __init__(self, message: str, *, expected: int, observed: int) -> None:
        super().__init__(message)
        self.expected = expected
        self.observed = observed


class UnsupportedError(MenloError):
    """This robot, over this transport, does not provide the capability. Check
    ``robot.has(...)`` first when a script should degrade instead of fail."""

    def __init__(self, capability: str, transport: str) -> None:
        super().__init__(
            f"this robot does not provide {capability!r} over the {transport!r} transport"
        )
        self.capability = capability
        self.transport = transport


class NotConnectedError(MenloError):
    """A verb was called before ``robot.connect(mode)`` / ``open()`` or after ``close``."""


class LinkLostError(MenloError):
    """The robot stopped talking (no state for ``link_timeout`` seconds).

    The session is over: every verb and wait on this ``Robot`` raises this error until the
    caller reconnects with ``close()`` followed by ``connect()`` (or ``open()``), which starts
    a clean session. There is no automatic reconnect: the SDK sent a zero velocity when it
    declared the link lost, and whether to try again is the caller's decision.
    """


# ── commands: the check before sending, and the wait after ───────────────────


class NotReadyError(MenloError):
    """The robot cannot do the command now, so nothing was sent. The one exception is its
    subclass :class:`RobotFaultedError` raised by a wait: a fault that ended the command
    after it went out, with ``sent`` set.

    Raised by ``stand()``, ``balance()``, ``set_velocity()``, ``trajectory()`` and ``set_joints()``
    when their readiness check fails: at once for a problem that does not clear on its own, or after
    the command's ``timeout`` for one that could have (``not_armed``, ``stale_state``).
    ``action`` is what was checked (``"stand"``, ``"move"`` or ``"trajectory"``),
    ``preflight`` the last check and ``problems`` its blocking problems, each with a stable
    ``code``. The message names every problem and what fixes it."""

    def __init__(
        self,
        message: str,
        *,
        action: Action | None,
        preflight: Preflight | None,
        problems: tuple[Problem, ...] | None = None,
    ) -> None:
        super().__init__(message)
        self.action = action
        self.preflight = preflight
        self.problems: tuple[Problem, ...] = (
            problems if problems is not None else preflight.blocking if preflight else ()
        )

    def has(self, code: str) -> bool:
        return any(p.code == code for p in self.problems)


class RobotFaultedError(NotReadyError):
    """The firmware latched DAMP (a fall, a critical alert, robot mode FAULT_DAMP), before
    a command was sent or while one was being waited for. The latch holds until the
    firmware restarts; retrying will not help. ``state`` is the sample that showed it.

    A subclass of :class:`NotReadyError`, so ``except NotReadyError`` catches every "the
    robot cannot do that now"; catch this one first to tell the fault apart. ``sent`` is
    the command that went out when the fault came during a wait for it (``stand()``,
    ``balance()``, ``set_joints()``, ``set_velocity(wait=True)``), and ``None`` when the
    check refused before anything was sent. ``action`` and ``preflight`` are ``None`` when
    it comes from ``wait_until``, which has no action."""

    def __init__(
        self,
        message: str,
        *,
        state: State,
        action: Action | None = None,
        preflight: Preflight | None = None,
        problems: tuple[Problem, ...] | None = None,
        sent: Sent | None = None,
    ) -> None:
        super().__init__(message, action=action, preflight=preflight, problems=problems)
        self.state = state
        self.sent = sent


class WaitTimeoutError(MenloError, TimeoutError):
    """The robot did not reach the expected state in time: after ``stand()``,
    ``balance()``, ``damp()``, ``set_joints(wait=True)`` or ``wait_until``. The command, if any,
    was sent (``sent``); ``last`` is the last state seen. Also a builtin ``TimeoutError``."""

    def __init__(
        self, message: str, *, last: State | None = None, sent: Sent | None = None
    ) -> None:
        super().__init__(message)
        self.last = last
        self.sent = sent


class StateStaleError(WaitTimeoutError):
    """The state stream went quiet, so a cached snapshot is no longer an observation and
    the wait refused to succeed on it."""


# ── outcomes, when the caller asks for exceptions ────────────────────────────


class CommandRefusedError(MenloError):
    """Raised by ``Sent.require()`` (or by a wait that saw its command refused)."""

    def __init__(self, refused: Refused) -> None:
        super().__init__(f"{refused.name} refused: {refused.reason.name} {refused.detail}".strip())
        self.refused = refused


class OutcomeUnknownError(MenloError, TimeoutError):
    """Raised by ``Sent.require(unknown_ok=False)`` when no outcome arrived in time."""

    def __init__(self, unknown: Unknown) -> None:
        super().__init__(
            f"{unknown.name} (seq {unknown.sequence}): no outcome within {unknown.waited_s:.2f}s"
        )
        self.unknown = unknown
