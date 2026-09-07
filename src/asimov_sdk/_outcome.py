"""What happened to a command, as values.

Every verb returns a :class:`Sent` immediately: the transport took the command, and here
is its sequence number, what was actually encoded (after clamping), and a place for the
robot's verdict to land. The verdict is one of three, and the third is the honest one:

* :class:`Applied` — the arbiter admitted it and forwarded it to the firmware.
* :class:`Refused` — the arbiter dropped it, with a typed :class:`Refusal`.
* :class:`Unknown` — no outcome arrived. UDP drops datagrams, rooms stall, and today's
  edge does not report outcomes at all. Unknown is never treated as refused, and never
  as success.

"Admitted" is a different question from "took effect". A STAND can be admitted and take
four seconds to come true; that second question is answered from state, by
``Robot.wait_for`` and ``Robot.wait_until``.
"""

from __future__ import annotations

import dataclasses
import enum
import threading
import time
from dataclasses import dataclass

from asimov_sdk._command import Command
from asimov_sdk._errors import CommandRefusedError, OutcomeUnknownError


class Refusal(enum.IntEnum):
    """Why the robot refused a command.

    Values follow ``menlo.edge.RefusalReason``; the UDP lane delivers none of them, so an
    outcome from any transport maps 1:1. ``UNRECOGNIZED`` is a value this SDK build does not know:
    the robot is newer than the client. It is still a refusal.
    """

    UNSPECIFIED = 0
    FW_DAMPED = 1
    FAULT_DAMPED = 2
    GATE = 3
    UNSUPPORTED_SOURCE = 4
    BAD_LENGTH = 5
    EMPTY_TRAJECTORY = 6
    NON_FINITE = 7
    PARSE = 8
    UNKNOWN_COMMAND = 9
    UNKNOWN_MODE = 10
    SHUTTING_DOWN = 11
    NO_CAMERA = 12
    #: The arbiter dropped the command because another controller holds the body
    #: (the edge's arbiter reason ``not_active``).
    NOT_ACTIVE = 100
    UNRECOGNIZED = -1

    @property
    def retryable(self) -> bool:
        """Is sending the identical command again ever worth it? Only a transient is."""
        return self in (Refusal.SHUTTING_DOWN, Refusal.NOT_ACTIVE)

    @classmethod
    def from_wire(cls, value: int) -> Refusal:
        try:
            return cls(value)
        except ValueError:
            return cls.UNRECOGNIZED


@dataclass(frozen=True, slots=True)
class Applied:
    sequence: int


@dataclass(frozen=True, slots=True)
class Refused:
    sequence: int
    reason: Refusal
    detail: str = ""  # free text from the robot; never branch on it
    verb: str = "command"  # filled in by the Sent that owns this outcome

    @property
    def name(self) -> str:
        return self.verb


@dataclass(frozen=True, slots=True)
class Unknown:
    """No outcome arrived. Not success. Not refusal."""

    sequence: int
    waited_s: float
    verb: str = "command"

    @property
    def name(self) -> str:
        return self.verb


Outcome = Applied | Refused | Unknown


class Sent:
    """Handle for one command the transport accepted for sending.

    Cheap to ignore: a 10 Hz velocity loop can drop it on the floor. Cheap to use: the
    ``outcome`` property never blocks, ``wait_outcome`` blocks for at most a timeout, and
    ``require`` turns a refusal into an exception for the one-shot commands that want one.
    """

    __slots__ = (
        "_event",
        "_name",
        "_outcome",
        "clamped",
        "command",
        "default_timeout",
        "sent_at",
        "sequence",
    )

    def __init__(
        self,
        *,
        name: str,
        sequence: int,
        command: Command,
        clamped: bool,
        default_timeout: float,
    ) -> None:
        self._name = name
        self.sequence = sequence
        self.command = command
        self.clamped = clamped
        self.sent_at = time.monotonic()
        self.default_timeout = default_timeout
        self._outcome: Applied | Refused | None = None
        self._event = threading.Event()

    @property
    def name(self) -> str:
        """The verb that produced this: ``set_velocity``, ``stand``, ``damp``, ``stop``,
        ``trajectory``."""
        return self._name

    # ── written by the Robot when the transport reports back ─────────────────
    def _resolve(self, outcome: Applied | Refused) -> None:
        if self._outcome is None:
            if isinstance(outcome, Refused) and outcome.verb != self._name:
                outcome = dataclasses.replace(outcome, verb=self._name)
            self._outcome = outcome
            self._event.set()

    # ── read by the caller ───────────────────────────────────────────────────
    @property
    def outcome(self) -> Applied | Refused | None:
        """The verdict if it has arrived, else ``None`` (still pending). Never blocks."""
        return self._outcome

    def wait_outcome(self, timeout: float | None = None) -> Outcome:
        """Block until the robot's verdict arrives, or ``timeout`` seconds pass.

        ``None`` uses the robot's default for its transport. Returns :class:`Unknown` on
        timeout rather than raising: a missing verdict on a velocity stream is noise, and
        the caller decides whether it matters.
        """
        t = self.default_timeout if timeout is None else timeout
        if self._event.wait(t) and self._outcome is not None:
            return self._outcome
        return Unknown(self.sequence, waited_s=t, verb=self._name)

    def require(self, timeout: float | None = None, *, unknown_ok: bool = True) -> Outcome:
        """Like ``wait_outcome`` but raise on refusal.

        ``unknown_ok=False`` also raises when nothing arrived. Keep the default while the
        UDP lane reports no outcomes — otherwise every call raises for the wrong reason.
        """
        outcome = self.wait_outcome(timeout)
        if isinstance(outcome, Refused):
            raise CommandRefusedError(outcome)
        if isinstance(outcome, Unknown) and not unknown_ok:
            raise OutcomeUnknownError(outcome)
        return outcome

    def __repr__(self) -> str:
        state = "pending" if self._outcome is None else type(self._outcome).__name__
        return f"Sent({self._name}, seq={self.sequence}, {state})"
