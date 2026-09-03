"""Everything this SDK raises, as types.

Two rules, both load-bearing for callers:

* ``except AsimovError`` catches everything that is about the ROBOT or the LINK. Caller
  bugs stay builtins — a non-finite velocity is a ``ValueError``, an unknown joint name a
  ``KeyError`` — because those are programming errors, not robot conditions, and the
  standard library already has the right names for them.
* A command is never refused synchronously. Refusals arrive as outcomes (see
  :mod:`asimov_sdk._outcome`) and only become exceptions when the caller asks
  (``Sent.require()``) or when a wait can no longer succeed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asimov_sdk._outcome import Refused, Unknown
    from asimov_sdk._state import State


class AsimovError(Exception):
    """Base for every robot- or link-related error this SDK raises."""


# ── the link ─────────────────────────────────────────────────────────────────


class ConnectFailed(AsimovError):
    """No robot answered within the connect timeout, or the transport could not open."""


class ProtocolMismatch(ConnectFailed):
    """The robot speaks a different ``asimov.io`` protocol version than this SDK was built
    against. Commands would be silently misread; refuse to start instead."""

    def __init__(self, message: str, *, expected: int, observed: int) -> None:
        super().__init__(message)
        self.expected = expected
        self.observed = observed


class NotConnected(AsimovError):
    """A verb was called before ``connect`` or after ``close``."""


class LinkLost(AsimovError):
    """The robot stopped talking. Terminal for this ``Robot``; there is no auto-reconnect,
    because reconnecting would re-latch a velocity across a gap the caller never saw.

    The robot is not left running: the edge's own velocity watchdog zero-and-STANDs it
    about two seconds after the last command, independently of this process.
    """


# ── waits ────────────────────────────────────────────────────────────────────


class WaitTimedOut(AsimovError, TimeoutError):
    """``wait_until``/``wait_for`` gave up. Also a builtin ``TimeoutError``, so callers who
    reach for that name still catch it."""

    def __init__(self, message: str, *, last: State | None = None) -> None:
        super().__init__(message)
        self.last = last


class StateStale(WaitTimedOut):
    """The state stream went quiet, so a cached snapshot is no longer an observation and
    the wait refused to succeed on it."""


class RobotFaulted(AsimovError):
    """The firmware fault-DAMPed (a fall, a critical alert) while a wait was in progress.
    Fault authority outranks every client; retrying the same command will not help."""

    def __init__(self, message: str, *, state: State) -> None:
        super().__init__(message)
        self.state = state


# ── outcomes, when the caller asks for exceptions ────────────────────────────


class CommandRefusedError(AsimovError):
    """Raised by ``Sent.require()`` (or by a wait that saw its command refused)."""

    def __init__(self, refused: Refused) -> None:
        super().__init__(f"{refused.name} refused: {refused.reason.name} {refused.detail}".strip())
        self.refused = refused


class OutcomeUnknownError(AsimovError, TimeoutError):
    """Raised by ``Sent.require(unknown_ok=False)`` when no outcome arrived in time."""

    def __init__(self, unknown: Unknown) -> None:
        super().__init__(
            f"{unknown.name} (seq {unknown.sequence}): no outcome within {unknown.waited_s:.2f}s"
        )
        self.unknown = unknown
