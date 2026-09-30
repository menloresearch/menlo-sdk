"""Is the robot ready for a command? Read from its own report.

    check = robot.preflight("move")    # sends nothing, never blocks
    if not check.ok:
        print(check)                   # every problem, one per line

``stand()``, ``set_velocity()``, ``trajectory()`` and ``set_joints()`` run the same check
before they send anything, and so does ``balance()`` from STAND; they raise
:class:`~menlo.asimov.NotReadyError` when it fails. ``set_velocity()`` also needs the
robot in MOVE: from STAND it refuses with ``wrong_mode``, where ``preflight("move")``
(can the robot enter or be in MOVE) passes.
``preflight`` is for a script, or a status display, that wants to decide for itself.

Each :class:`Problem` has a stable ``code`` a script can branch on, a message for people,
and ``blocking``: a blocking problem makes ``ok`` false. A field the robot does not report
is a non-blocking problem (``unknown_*``): the check says it could not look, and never
assumes the answer. A command waits up to its ``timeout`` for the codes in ``TRANSIENT``,
which clear on their own; any other blocking problem fails the command at once.

Codes:

========================  ========  ==========================================================
code                      blocking  meaning
========================  ========  ==========================================================
``not_connected``         yes       the Robot is closed, or its link was lost
``no_state``              yes       the session is open but the robot has not reported state
``stale_state``           yes       the latest state is older than ``MAX_STATE_AGE_S``
``faulted``               yes       the firmware latched DAMP; it stays so until it restarts
``battery_protecting``    yes       the battery management system is protecting the pack
``battery_low``           yes       state of charge below ``BATTERY_LOW_PERCENT``
``unknown_battery``       no        the robot reports no battery
``joint_hot``             yes       an actuator at or above ``JOINT_HOT_C``
``unknown_joint_temp``    no        the robot reports no actuator temperatures
``wrong_mode``            yes       the robot mode does not allow the action
``not_armed``             yes       STAND has not been held upright for ``ARM_HOLD_S``
``unknown_gravity``       no        the robot reports no gravity vector, so tilt and arming
                                    cannot be checked
========================  ========  ==========================================================
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, get_args

from menlo.asimov._state import ALERT_NAMES, Mode, State

#: What a script checks readiness for. ``move`` is ``balance`` from STAND and
#: ``set_velocity``; ``trajectory`` is ``trajectory`` and ``set_joints``.
Action = Literal["stand", "move", "trajectory"]
ACTIONS: tuple[Action, ...] = get_args(Action)
#: How each action reads in a sentence: "ready to stand", "not ready to run a trajectory".
PHRASES: dict[Action, str] = {"stand": "stand", "move": "move", "trajectory": "run a trajectory"}

#: A decision to move is made on a sample at most this old (5 samples at the 10 Hz of
#: the livekit connection mode).
MAX_STATE_AGE_S = 0.5
#: The firmware raises its BMS_LOW_SOC warning below this state of charge.
BATTERY_LOW_PERCENT = 20.0
#: The firmware raises MOTOR_TEMP_HIGH at this actuator temperature and latches DAMP at 80 C.
JOINT_HOT_C = 60.0
#: The firmware's upright test for arming: projected gravity z below this (tilt under 30 deg).
ARM_GRAVITY_Z = -0.87
#: The firmware accepts MOVE once STAND has been held upright this long.
ARM_HOLD_S = 0.5
#: A longer gap between samples restarts the hold: half the staleness limit, so no hold is
#: made of one unobserved stretch (2.5 samples at 10 Hz).
ARM_MAX_GAP_S = MAX_STATE_AGE_S / 2

#: Blocking codes a command waits out, up to its ``timeout``: they clear on their own (a
#: sample arrives, the robot arms). Every other blocking code fails the command at once.
TRANSIENT = frozenset({"no_state", "stale_state", "not_armed"})

#: What to do about a blocking problem, when its message does not already say.
FIXES: dict[str, str] = {
    "not_connected": "close() and connect() again",
    "no_state": "check that the robot's firmware is running",
    "stale_state": "check the network link to the robot",
    "battery_protecting": "check the battery before you drive",
    "battery_low": "charge the battery",
    "joint_hot": "let the actuators cool",
    "not_armed": "keep the robot upright in STAND; stand() returns once it is armed",
}


@dataclass(frozen=True, slots=True)
class Problem:
    """One reason the robot is, or may not be, ready. See the module docstring for codes."""

    code: str
    message: str
    blocking: bool

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + ("" if self.blocking else " (warning)")


@dataclass(frozen=True, slots=True)
class Preflight:
    """The answer to ``robot.preflight(action)``: ``ok`` when no problem is blocking."""

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


def _mode_problems(action: Action, state: State, armed: bool | None) -> list[Problem]:
    """Whether the robot mode allows ``action`` (and, for MOVE, whether it is armed)."""
    problems: list[Problem] = []
    add = problems.append
    mode = state.mode
    if action == "stand":
        if mode is Mode.MOVE:
            add(
                Problem(
                    "wrong_mode",
                    "the robot is in MOVE; STAND has no balance loop and a free-standing "
                    "robot tips over. Use balance() to stand still in MOVE",
                    True,
                )
            )
        elif mode is Mode.UNKNOWN:
            add(Problem("wrong_mode", "the robot reports an unknown robot mode", True))
        if state.gravity is None:
            add(Problem("unknown_gravity", "the robot reports no gravity vector", False))
        return problems

    # move / trajectory: from an armed STAND, or in MOVE
    if mode is Mode.FAULT_DAMP or (mode is Mode.DAMP and state.faulted):
        pass  # the latched fault is the reason, and stand() does not clear it
    elif mode is Mode.DAMP:
        add(Problem("wrong_mode", "the robot is in DAMP; stand() it first", True))
    elif mode is Mode.UNKNOWN:
        add(Problem("wrong_mode", "the robot reports an unknown robot mode", True))
    elif mode is Mode.STAND and not armed:
        if state.gravity is None:
            add(
                Problem(
                    "unknown_gravity",
                    "the robot reports no gravity vector, so arming cannot be checked",
                    False,
                )
            )
        elif state.gravity[2] >= ARM_GRAVITY_Z:
            add(
                Problem(
                    "not_armed",
                    f"the robot is tilted {tilt_deg(state.gravity):.0f} deg; the firmware "
                    f"accepts MOVE after {ARM_HOLD_S} s in STAND under 30 deg",
                    True,
                )
            )
        else:
            add(
                Problem(
                    "not_armed",
                    f"STAND has not been held upright for {ARM_HOLD_S} s; the firmware "
                    "accepts MOVE after that",
                    True,
                )
            )
    return problems


def evaluate(
    action: Action,
    state: State | None,
    *,
    armed: bool | None,
    no_state: Problem | None = None,
) -> Preflight:
    """The check itself, on one sample. ``no_state`` is the problem to report when there
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
                True,
            )
        )

    problems += _mode_problems(action, state, armed)

    battery = state.battery
    if battery is None:
        add(Problem("unknown_battery", "the robot reports no battery", False))
    elif battery.protecting:
        add(
            Problem(
                "battery_protecting",
                "the battery management system is protecting the pack "
                f"({battery.protection.name or int(battery.protection)})",
                True,
            )
        )
    elif battery.soc_percent < BATTERY_LOW_PERCENT:
        add(
            Problem(
                "battery_low",
                f"battery at {battery.soc_percent:.0f} % (below {BATTERY_LOW_PERCENT:.0f} %)",
                True,
            )
        )

    temps = [(j.name or f"joint {i}", j.temp) for i, j in enumerate(state.joints)]
    known = [(name, t) for name, t in temps if t is not None]
    if not known:
        add(Problem("unknown_joint_temp", "the robot reports no actuator temperatures", False))
    else:
        hot = [f"{name} {t:.0f} C" for name, t in known if t >= JOINT_HOT_C]
        if hot:
            add(
                Problem(
                    "joint_hot",
                    f"{', '.join(hot)} (at or above {JOINT_HOT_C:.0f} C)",
                    True,
                )
            )

    # Blocking problems first, each group in the order checked.
    ordered = sorted(problems, key=lambda p: not p.blocking)
    return Preflight(action, tuple(ordered), state, armed)


__all__ = [
    "ACTIONS",
    "ARM_GRAVITY_Z",
    "ARM_HOLD_S",
    "BATTERY_LOW_PERCENT",
    "FIXES",
    "JOINT_HOT_C",
    "MAX_STATE_AGE_S",
    "TRANSIENT",
    "Action",
    "Preflight",
    "Problem",
    "evaluate",
    "fault_names",
    "tilt_deg",
]
