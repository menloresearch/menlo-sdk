"""``menlo status``, ``stand``, ``walk``, ``stop`` and ``damp``.

Each command connects the way a script does (``Robot(config).connect(mode)``) and leaves
through ``with``, so ``close()`` always runs. ``stand``, ``walk`` and ``damp`` print a
plan and ask before they send anything (``go_ahead``). ``stand`` and ``walk`` first check
``robot.preflight(action)`` and stop with ``Not feasible:`` when it refuses (``feasible``);
``walk`` checks again after the answer. ``damp`` is not gated by preflight: it sends DAMP
whatever the robot's state. ``stop`` never asks and refuses only a stale or lost state
stream. ``status`` sends nothing at all.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import time
from dataclasses import astuple
from typing import Any

from menlo.asimov import (
    Action,
    ManagerConfig,
    MenloError,
    Mode,
    NotReadyError,
    Preflight,
    Robot,
    RobotFaultedError,
    Velocity,
    WaitTimeoutError,
)
from menlo.asimov._preflight import MAX_STATE_AGE_S, fault_names
from menlo.cli._common import (
    EXIT_CANCELLED,
    EXIT_OK,
    NotFeasible,
    StateRate,
    UsageError,
    connected,
)

#: A walk from the command line is bounded, and short.
MAX_WALK_S = 10.0
#: How long ``stand`` waits for STAND to be reported, then for the robot to arm.
STAND_TIMEOUT_S = 5.0
ARM_TIMEOUT_S = 5.0
#: How long ``status`` counts state samples before it reports the rate.
RATE_WINDOW_S = 1.0


def _say(text: str) -> None:
    from menlo.cli._ui import console

    console.print(text)


def _refuse(check: Preflight) -> NotReadyError:
    return NotReadyError(str(check), preflight=check)


# ── menlo status ─────────────────────────────────────────────────────────────
def host(robot: Robot) -> str:
    """The address the robot is reached at on this connection mode."""
    config, mode = robot.config, robot.info.transport
    if config.udp is not None and mode != "livekit":
        return config.udp.host
    if isinstance(config.livekit, ManagerConfig):
        return config.livekit.host
    return str(robot.info.endpoint)


def snapshot(robot: Robot, rate: StateRate) -> dict[str, Any]:
    """What ``status`` shows, as plain data (``--json`` prints exactly this)."""
    config = robot.config
    mode = robot.info.transport
    check = robot.preflight("move")
    s = check.state
    verdict = "READY" if check.ok else "FAULTED" if check.has("faulted") else "NOT READY"
    temps = [j.temp for j in s.joints if j.temp is not None] if s is not None else []
    battery = s.battery if s is not None else None
    return {
        "robot": config.name,
        "mode": mode,
        "host": host(robot),
        "verdict": verdict,
        "ready": check.ok,
        "problems": [
            {"code": p.code, "message": p.message, "blocking": p.blocking} for p in check.problems
        ],
        "robot_mode": s.mode.name if s is not None else None,
        "armed": robot.armed,
        "battery_percent": battery.soc_percent if battery is not None else None,
        "faults": fault_names(s) if s is not None else [],
        "error_flags": s.error_flags if s is not None else None,
        "max_joint_temp_c": max(temps) if temps else None,
        "state_rate_hz": round(rate.hz, 1),
        "state_age_s": round(s.age_s, 3) if s is not None else None,
        "fresh": s is not None and not check.has("stale_state"),
    }


def render(snap: dict[str, Any]) -> Any:
    """``snapshot`` as a small panel: a title and four lines."""
    from rich.panel import Panel
    from rich.text import Text

    colour = {"READY": "green", "NOT READY": "yellow", "FAULTED": "red"}[snap["verdict"]]
    blocking = [p for p in snap["problems"] if p["blocking"]]
    body = Text.from_markup(f"[bold reverse {colour}] {snap['verdict']} [/]")
    if blocking:
        body.append(f"  {blocking[0]['message']}")
    armed = (
        {True: " (armed)", False: " (not armed)", None: " (armed unknown)"}[snap["armed"]]
        if snap["robot_mode"] in ("STAND", "MOVE")
        else ""
    )
    battery = (
        f"{snap['battery_percent']:.0f} %"
        if snap["battery_percent"] is not None
        else "not reported"
    )
    joint = (
        f"{snap['max_joint_temp_c']:.0f} C"
        if snap["max_joint_temp_c"] is not None
        else "not reported"
    )
    age = f"{snap['state_age_s']:.2f} s" if snap["state_age_s"] is not None else "no state"
    lines = [
        f"robot mode [bold]{snap['robot_mode'] or '-'}[/]{armed}   battery {battery}"
        f"   hottest joint {joint}",
        f"faults {', '.join(snap['faults']) or 'none'}",
        f"state {snap['state_rate_hz']:.0f} Hz, {'fresh' if snap['fresh'] else 'stale'} ({age})",
    ]
    for line in lines:
        body.append("\n")
        body.append_text(Text.from_markup(line))
    title = " · ".join(str(x) for x in (snap["robot"] or "environment", snap["mode"], snap["host"]))
    return Panel(body, title=title, title_align="left", expand=False)


def status(args: argparse.Namespace) -> int:
    with connected(args) as robot:
        rate = StateRate(robot)
        if not args.watch:
            time.sleep(RATE_WINDOW_S)
            snap = snapshot(robot, rate)
            if args.json:
                print(json.dumps(snap, indent=2))
            else:
                from menlo.cli._ui import console

                console.print(render(snap))
            return EXIT_OK

        from rich.live import Live

        from menlo.cli._ui import console, keypresses

        with (
            Live(render(snapshot(robot, rate)), console=console, auto_refresh=False) as live,
            keypresses() as pressed,
        ):
            try:
                while pressed() not in ("q", "Q"):
                    time.sleep(0.25)
                    live.update(render(snapshot(robot, rate)), refresh=True)
            except KeyboardInterrupt:
                pass
    return EXIT_OK


# ── before anything is sent: feasible, a plan, a yes ─────────────────────────
#: The fix for a blocking problem, said after its message.
_FIX = {
    "battery_low": "Charge the battery.",
    "joint_hot": "Let the actuators cool.",
    "not_armed": "Run the command again once `menlo status` shows it armed.",
}
#: What to do instead, when the robot mode does not allow the action.
_WRONG_MODE = {
    "stand": "`stand` only runs from DAMP; end a walk with `menlo stop`.",
    "move": "Run `menlo stand` first.",
}
_NO_STATE = ("not_connected", "no_state", "stale_state")


def _name(robot: Robot) -> str:
    return robot.config.name or "the robot"


def not_feasible(robot: Robot, check: Preflight) -> NotFeasible:
    """``check``'s blocking problems as one ``Not feasible:`` message: the robot mode,
    the reason in plain words, and the fix."""
    s = check.state
    gone = [p for p in check.blocking if p.code in _NO_STATE]
    if gone or s is None:
        why = gone[0].message if gone else "no state"
        return NotFeasible(
            f"Not feasible: no fresh state from {_name(robot)}: {why}. "
            "Check the link with `menlo status`."
        )
    posture = (
        "faulted"
        if s.faulted
        else {
            Mode.DAMP: "not balancing",
            Mode.STAND: "armed" if check.armed else "not armed",
            Mode.MOVE: "balancing",
        }.get(s.mode, "robot mode unknown")
    )
    # A latched fault is the whole story: nothing else can be fixed until it clears.
    problems = [p for p in check.blocking if p.code == "faulted"] or check.blocking
    reasons = [
        _WRONG_MODE["stand" if check.action == "stand" else "move"]
        if p.code == "wrong_mode" and s.mode is not Mode.UNKNOWN
        else f"{p.message[0].upper()}{p.message[1:]}. {_FIX.get(p.code, '')}".strip()
        for p in problems
    ]
    return NotFeasible(
        f"Not feasible: {_name(robot)} is in {s.mode.name}, {posture}. " + " ".join(reasons)
    )


def feasible(robot: Robot, action: Action, *, wait_for_arming: bool = False) -> None:
    """Raise :class:`NotFeasible` unless ``robot.preflight(action)`` is ok. With
    ``wait_for_arming``, a robot whose only problem is ``not_armed`` gets ``ARM_TIMEOUT_S``
    to arm:
    a new session counts the 0.5 s upright hold from its first sample."""
    check = robot.preflight(action)
    if wait_for_arming and all(p.code == "not_armed" for p in check.blocking):
        try:
            check = robot.wait_ready(action, timeout=ARM_TIMEOUT_S)
        except NotReadyError as exc:
            check = exc.preflight
    if not check.ok:
        raise not_feasible(robot, check)


def plan(robot: Robot, doing: str) -> str:
    """One line: which robot and how it is reached, its state, and what will happen."""
    s = robot.state
    now = [s.mode.name + (", faulted" if s.faulted else "")]
    if s.mode is Mode.STAND:
        now[0] += {True: ", armed", False: ", not armed", None: ", armed unknown"}[robot.armed]
    now.append(f"battery {s.battery.soc_percent:.0f} %" if s.battery else "battery not reported")
    if s.age_s > MAX_STATE_AGE_S:
        now.append(f"state {s.age_s:.1f} s old")
    where = f"[bold]{_name(robot)}[/] ({robot.info.transport}, {host(robot)})"
    return f"{where} · {' · '.join(now)} [bold cyan]→[/] {doing}"


def go_ahead(args: argparse.Namespace, robot: Robot, doing: str) -> bool:
    """Print the plan and ask ``Proceed? [y/N]``; ``--yes`` answers yes. With no terminal
    to ask on and no ``--yes``, a :class:`UsageError`."""
    from menlo.cli import _ui

    _ui.console.print(plan(robot, doing), soft_wrap=True)
    if args.yes:
        return True
    if not _ui.interactive():
        raise UsageError("no terminal to ask on; pass --yes to go ahead without asking")
    if _ui.confirm("Proceed?"):
        return True
    _say("Cancelled; nothing sent.")
    return False


# ── menlo stand ──────────────────────────────────────────────────────────────
def stand(args: argparse.Namespace) -> int:
    with connected(args) as robot:
        feasible(robot, "stand")
        if robot.state.mode is Mode.STAND:
            armed = "armed" if robot.armed else "not armed"
            _say(f"{_name(robot)} is already in STAND ({armed}); nothing sent.")
            return EXIT_OK
        if not go_ahead(args, robot, "stand, then wait until armed"):
            return EXIT_CANCELLED
        feasible(robot, "stand")  # the robot may have changed while you read the plan
        robot.stand()
        try:
            robot.wait_for(Mode.STAND, timeout=STAND_TIMEOUT_S)
        except WaitTimeoutError:
            check = robot.preflight("stand")
            raise NotReadyError(
                f"STAND was not reported within {STAND_TIMEOUT_S:.0f} s", preflight=check
            ) from None
        _say("STAND reported. Waiting for the robot to arm (0.5 s upright)...")
        robot.wait_ready("move", timeout=ARM_TIMEOUT_S)
        _say("[green]Armed[/]: ready to walk, e.g. [bold]menlo walk --vx 0.2 --duration 3[/]")
    return EXIT_OK


# ── menlo walk ───────────────────────────────────────────────────────────────
def walk(args: argparse.Namespace) -> int:
    speeds = (args.vx, args.vy, args.vyaw)
    if not all(math.isfinite(v) for v in speeds):
        raise UsageError("--vx, --vy and --vyaw must be finite numbers")
    if not any(speeds):
        raise UsageError("give --vx, --vy or --vyaw")
    if not 0 < args.duration <= MAX_WALK_S:
        raise UsageError(f"--duration must be more than 0 and at most {MAX_WALK_S:.0f} s")
    with connected(args) as robot:
        feasible(robot, "move", wait_for_arming=True)
        going = Velocity(*speeds).clamped(robot.limits)
        parts = [
            f"{label} {sent:.2f} {unit}" + (f" (asked {asked:.2f})" if sent != asked else "")
            for label, unit, asked, sent in zip(
                ("vx", "vy", "vyaw"),
                ("m/s", "m/s", "rad/s"),
                speeds,
                astuple(going),
                strict=True,
            )
        ]
        doing = f"walk {', '.join(parts)} for {args.duration:.1f} s, then stop"
        if not go_ahead(args, robot, doing):
            return EXIT_CANCELLED
        feasible(robot, "move")  # the robot may have changed while you read the plan
        _say("Walking. Ctrl-C stops.")
        started = time.monotonic()
        try:
            robot.set_velocity(*speeds, duration=args.duration, wait=True)
        finally:
            with contextlib.suppress(MenloError):
                robot.stop()
        walked = time.monotonic() - started
        s = robot.state
        if s.faulted:
            names = ", ".join(fault_names(s)) or "a latched fault"
            raise NotReadyError(
                f"the walk ended after {walked:.1f} s: the firmware latched DAMP ({names}); "
                "it stays latched until the firmware restarts",
                preflight=robot.preflight("move"),
            )
        if walked < args.duration:
            raise NotReadyError(
                f"the walk ended after {walked:.1f} s of {args.duration:.1f} s; "
                f"robot mode {s.mode.name}",
                preflight=robot.preflight("move"),
            )
        _say(f"Stopped. Robot mode {s.mode.name}.")
    return EXIT_OK


# ── menlo stop ───────────────────────────────────────────────────────────────
def stop(args: argparse.Namespace) -> int:
    with connected(args) as robot:
        check = robot.preflight("move")
        stale = [p for p in check.blocking if p.code in ("stale_state", "not_connected")]
        if stale:
            raise _refuse(check)
        mode = robot.state.mode
        if mode is not Mode.MOVE:
            # A zero velocity in STAND is a request for MOVE; in DAMP Asimov Edge drops it.
            _say(f"Robot mode {mode.name}: nothing to stop; nothing sent.")
            return EXIT_OK
        robot.stop()
        time.sleep(0.1)
        # A datagram can be lost, so a second zero follows, but only to a robot still
        # reported in MOVE: if it left MOVE meanwhile, a zero in STAND asks for MOVE again.
        s = robot.state
        if s.mode is Mode.MOVE and s.age_s <= MAX_STATE_AGE_S:
            robot.stop()
        _say("Sent zero velocity. The robot stays in MOVE, balancing in place.")
        _say(
            "[dim]A script that holds a velocity re-sends it at 10 Hz; stop that script to "
            "end its walk.[/]"
        )
    return EXIT_OK


# ── menlo damp ───────────────────────────────────────────────────────────────
def damp(args: argparse.Namespace) -> int:
    with connected(args) as robot:
        doing = (
            "damp: every actuator goes limp and a standing robot folds. "
            "Not an emergency stop; use the E-Stop in Asimov Manager for that."
        )
        if not go_ahead(args, robot, doing):
            return EXIT_CANCELLED
        robot.damp()
        try:
            robot.wait_for(Mode.DAMP, timeout=STAND_TIMEOUT_S)
        except RobotFaultedError as exc:
            names = ", ".join(fault_names(exc.state)) or "a latched fault"
            _say(f"Robot mode DAMP, held by {names} until the firmware restarts.")
            return EXIT_OK
        _say("Robot mode DAMP.")
    return EXIT_OK


__all__ = [
    "damp",
    "feasible",
    "go_ahead",
    "not_feasible",
    "plan",
    "render",
    "snapshot",
    "stand",
    "status",
    "stop",
    "walk",
]
