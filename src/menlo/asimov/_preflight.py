"""What the robot reports, as a list of facts: read it before you decide.

    check = robot.preflight("move")    # sends nothing, never blocks
    print(check)                       # every fact, one per line
    if not check.ok:                   # no live state: a command would wait, then refuse
        ...

The SDK does not guard. Safety is the firmware's job, command handling is Asimov Edge's
job, and a guard (a joint too hot, a battery too low, a fault you will not drive through)
is yours: write it from ``robot.get_state()``, as ``examples/guard.py`` does. A command
refuses only when there is no live state to send against: ``stand()``, ``balance()`` from
outside MOVE, ``set_velocity()``, ``trajectory()`` and ``set_joints()`` wait up to their
``timeout`` for a live state stream and raise :class:`~menlo.asimov.NotReadyError` with
nothing sent when it does not come. For ``not_connected`` a command raises at once:
:class:`~menlo.asimov.NotConnectedError` for a closed Robot,
:class:`~menlo.asimov.LinkLostError` for a lost link and
:class:`~menlo.asimov.ProtocolMismatchError` for a robot on another protocol version.

Each :class:`Problem` has a stable ``code`` a script can branch on, a message for people,
and ``blocking``. Only the codes for no live state are blocking, and only they make ``ok``
false. The others are information: nothing in the SDK acts on them.

Codes:

========================  ========  ==========================================================
code                      blocking  meaning
========================  ========  ==========================================================
``not_connected``         yes       the Robot is closed, its link was lost, or the protocol
                                    version differs
``no_state``              yes       the session is open but the robot has not reported state
``stale_state``           yes       the latest state is older than ``MAX_STATE_AGE_S``
``faulted``               no        the firmware latched DAMP (the alerts that caused it are
                                    named); it stays so until the firmware restarts
``alerts``                no        the firmware reports active alerts, named
``not_armed``             no        ``move`` from STAND: STAND has not been held upright for
                                    ``ARM_HOLD_S``; a velocity that arrives before then leaves
                                    the robot in STAND
========================  ========  ==========================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, get_args

from menlo.asimov._state import ALERT_NAMES, Mode, State

#: What a script asks about. ``move`` is ``balance`` and ``set_velocity``; ``trajectory`` is
#: ``trajectory`` and ``set_joints``.
Action = Literal["stand", "move", "trajectory"]
ACTIONS: tuple[Action, ...] = get_args(Action)
#: How each action reads in a sentence: "ready to stand", "not ready to run a trajectory".
PHRASES: dict[Action, str] = {"stand": "stand", "move": "move", "trajectory": "run a trajectory"}

#: A command is sent against a sample at most this old (5 samples at the 10 Hz of the
#: livekit connection mode).
MAX_STATE_AGE_S = 0.5
#: The firmware's upright test for arming: projected gravity z below this (tilt under 30 deg).
ARM_GRAVITY_Z = -0.87
#: The firmware accepts MOVE once STAND has been held upright this long.
ARM_HOLD_S = 0.5
#: A longer gap between samples restarts the hold: half the staleness limit, so no hold is
#: made of one unobserved stretch (2.5 samples at 10 Hz).
ARM_MAX_GAP_S = MAX_STATE_AGE_S / 2

#: The blocking codes a command waits out, up to its ``timeout``: a sample arrives and they
#: clear. ``not_connected`` does not clear on its own.
TRANSIENT = frozenset({"no_state", "stale_state"})

#: What to do about a blocking problem.
FIXES: dict[str, str] = {
    "not_connected": "close() and connect() again",
    "no_state": "check that the robot's firmware is running",
    "stale_state": "check the network link to the robot",
}


@dataclass(frozen=True, slots=True)
class Problem:
    """One fact about the robot. See the module docstring for codes."""

    code: str
    message: str
    blocking: bool

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + ("" if self.blocking else " (information)")


@dataclass(frozen=True, slots=True)
class Preflight:
    """The answer to ``robot.preflight(action)``: ``ok`` when nothing is blocking, which is
    when there is live state to send against. The non-blocking problems are facts for the
    caller to act on, or not."""

    action: Action
    problems: tuple[Problem, ...]
    #: The sample the check read; ``None`` when there was none.
    state: State | None = None
    #: Whether the SDK has seen the robot armed (see ``Robot.armed``).
    armed: bool | None = None

    @property
    def ok(self) -> bool:
        return not any(p.blocking for p in self.problems)

    @property
    def blocking(self) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.blocking)

    def has(self, code: str) -> bool:
        return any(p.code == code for p in self.problems)

    def __str__(self) -> str:
        head = ("ready to " if self.ok else "not ready to ") + PHRASES[self.action]
        if not self.problems:
            return head
        return head + ":\n" + "\n".join(f"  - {p}" for p in self.problems)

    def explain(self) -> str:
        """The blocking problems in one sentence each, with what fixes them: the message
        of the :class:`~menlo.asimov.NotReadyError` a command raises for this check."""
        head = ("ready to " if self.ok else "not ready to ") + PHRASES[self.action]
        reasons = []
        for p in self.blocking:
            fix = FIXES.get(p.code)
            reasons.append(f"{p.message} ({p.code})" + (f"; {fix}" if fix else ""))
        return head + (": " + ". ".join(reasons) if reasons else "")


def tilt_deg(gravity: tuple[float, float, float]) -> float:
    """Angle between the body's vertical and measured gravity, in degrees."""
    norm = math.sqrt(sum(g * g for g in gravity)) or 1.0
    return math.degrees(math.acos(max(-1.0, min(1.0, -gravity[2] / norm))))


def fault_names(state: State) -> list[str]:
    """Why the firmware latched DAMP: the critical alerts reported now, plus every alert
    recorded in ``error_flags`` (bit 0 = latched, bit 1+n = critical alert n)."""
    names = [a.name for a in state.alerts if a.critical]
    for n in range(31):
        if state.error_flags & (1 << (n + 1)):
            name = ALERT_NAMES.get(n, f"ALERT_{n}")
            if name not in names:
                names.append(name)
    return names


def evaluate(
    action: Action,
    state: State | None,
    *,
    armed: bool | None,
    no_state: Problem | None = None,
) -> Preflight:
    """The report itself, on one sample. ``no_state`` is the problem to report when there
    is no sample to read."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}; one of {', '.join(ACTIONS)}")
    if state is None:
        problem = no_state or Problem("no_state", "the robot has not reported state", True)
        return Preflight(action, (problem,), None, None)
    problems: list[Problem] = []
    add = problems.append

    if state.age_s > MAX_STATE_AGE_S:
        add(
            Problem(
                "stale_state",
                f"the latest state is {state.age_s:.1f} s old (limit {MAX_STATE_AGE_S} s)",
                True,
            )
        )
    if state.faulted:
        names = ", ".join(fault_names(state)) or (
            "robot mode FAULT_DAMP"
            if state.mode is Mode.FAULT_DAMP
            else f"error_flags={state.error_flags:#x}"
        )
        add(
            Problem(
                "faulted",
                f"the firmware latched DAMP ({names}); it stays latched until the firmware "
                "restarts",
                False,
            )
        )
    if state.alerts:
        names = ", ".join(dict.fromkeys(a.name for a in state.alerts))
        add(Problem("alerts", f"the firmware reports {names}", False))
    if action == "move" and state.mode is Mode.STAND and armed is False:
        if state.gravity is not None and state.gravity[2] >= ARM_GRAVITY_Z:
            why = f"the robot is tilted {tilt_deg(state.gravity):.0f} deg"
        else:
            why = f"STAND has not been held upright for {ARM_HOLD_S} s"
        add(
            Problem(
                "not_armed",
                f"{why}; the firmware enters MOVE once STAND has been held under 30 deg for "
                f"{ARM_HOLD_S} s; a velocity that arrives before then leaves it in STAND",
                False,
            )
        )

    # Blocking problems first, each group in the order checked.
    ordered = sorted(problems, key=lambda p: not p.blocking)
    return Preflight(action, tuple(ordered), state, armed)


__all__ = [
    "ACTIONS",
    "ARM_GRAVITY_Z",
    "ARM_HOLD_S",
    "FIXES",
    "MAX_STATE_AGE_S",
    "TRANSIENT",
    "Action",
    "Preflight",
    "Problem",
    "evaluate",
    "fault_names",
    "tilt_deg",
]
