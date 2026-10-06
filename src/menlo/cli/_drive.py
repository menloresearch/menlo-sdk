"""``menlo status``, ``stand``, ``balance``, ``walk`` and ``damp``.

Each command connects the way a script does (``Robot(config).connect(mode)``) and leaves
through ``with``, so ``close()`` always runs. ``stand``, ``walk``, ``damp`` and
``balance`` outside MOVE print the plan with the robot's facts (robot mode, armed,
faults, active alerts, hottest joint, battery) and ask before they send anything
(``go_ahead``). The facts are for you to judge: no command refuses because of them. The one
refusal is no live state (``live``): ``Not feasible:`` and nothing sent. ``balance`` in
MOVE never asks. ``status`` sends nothing at all.
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
    Robot,
    RobotFaultedError,
    StateStaleError,
    Velocity,
    WaitTimeoutError,
)
from menlo.asimov._preflight import MAX_STATE_AGE_S, fault_names
from menlo.cli._common import (
    EXIT_CANCELLED,
    EXIT_NOT_READY,
    EXIT_OK,
    NotFeasible,
    StateRate,
    UsageError,
    connected,
)

#: A walk from the command line is bounded, and short.
MAX_WALK_S = 10.0
#: How long ``stand`` waits for the robot to report STAND and arm, and ``damp`` for DAMP.
STAND_TIMEOUT_S = 10.0
#: How long ``balance`` and ``walk`` wait for live state and for MOVE.
ARM_TIMEOUT_S = 5.0
#: A new session counts the 0.5 s upright hold from its first sample: how long a command
#: gives a robot in STAND to be seen armed before it reports the fact.
ARM_SEEN_S = 1.0
#: How long ``status`` counts state samples before it reports the rate.
RATE_WINDOW_S = 1.0


def _say(text: str) -> None:
    from menlo.cli._ui import console

    console.print(text, soft_wrap=True)


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
    verdict = "NOT READY" if not check.ok else "FAULTED" if check.has("faulted") else "READY"
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
        "alerts": list(dict.fromkeys(a.name for a in s.alerts)) if s is not None else [],
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
    first = [p for p in snap["problems"] if p["blocking"] or p["code"] == "faulted"]
    body = Text.from_markup(f"[bold reverse {colour}] {snap['verdict']} [/]")
    if first:
        body.append(f"  {first[0]['message']}")
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
        f"faults {', '.join(snap['faults']) or 'none'}"
        f"   alerts {', '.join(snap.get('alerts', [])) or 'none'}",
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


# ── before anything is sent: live state, the plan and the facts, a yes ───────
_NO_STATE = ("not_connected", "no_state", "stale_state")
#: Said before ``stand`` from MOVE: STAND stiffens to a pose with no balance loop.
SUPPORT_WARNING = (
    "[bold yellow]Warning:[/] the robot is in MOVE. STAND has no balance loop: hang the "
    "robot from its gantry hook or seat it on a stool or bench first, or it tips over."
)


def _name(robot: Robot) -> str:
    return robot.config.name or "the robot"


def live(robot: Robot, action: Action) -> None:
    """Raise :class:`NotFeasible` when there is no live state to send against: the one
    thing the command line refuses on. Everything else the robot reports is shown in the
    plan, for you to judge."""
    check = robot.preflight(action)
    gone = [p for p in check.blocking if p.code in _NO_STATE]
    if gone or check.state is None:
        why = gone[0].message if gone else "no state"
        raise NotFeasible(
            f"Not feasible: no fresh state from {_name(robot)}: {why}. "
            "Check the link with `menlo status`."
        )


def _see_armed(robot: Robot) -> None:
    """In STAND, give the session ``ARM_SEEN_S`` to see an armed robot as armed."""
    deadline = time.monotonic() + ARM_SEEN_S
    while (
        robot.get_state().mode is Mode.STAND
        and robot.armed is False
        and time.monotonic() < deadline
    ):
        time.sleep(0.05)


def facts(robot: Robot) -> list[str]:
    """What the robot reports, in short: robot mode, armed, faults, active alerts, the
    hottest joint, the battery, and the state's age when it is stale."""
    _see_armed(robot)
    s = robot.get_state()
    out = [s.mode.name]
    if s.mode in (Mode.STAND, Mode.MOVE):
        out.append({True: "armed", False: "not armed", None: "armed unknown"}[robot.armed])
    faults = fault_names(s) if s.faulted else []
    out.append(f"faults {', '.join(faults) or ('latched' if s.faulted else 'none')}")
    alerts = list(dict.fromkeys(a.name for a in s.alerts))
    out.append(f"alerts {', '.join(alerts) or 'none'}")
    temps = [(j.temp, j.name or f"joint {i}") for i, j in enumerate(s.joints) if j.temp is not None]
    if temps:
        hottest, joint = max(temps)
        out.append(f"hottest joint {hottest:.0f} C ({joint})")
    else:
        out.append("joint temperatures not reported")
    out.append(f"battery {s.battery.soc_percent:.0f} %" if s.battery else "battery not reported")
    if s.age_s > MAX_STATE_AGE_S:
        out.append(f"state {s.age_s:.1f} s old")
    return out


def plan(robot: Robot, doing: str) -> str:
    """One line: which robot and how it is reached, its facts, and what will happen."""
    where = f"[bold]{_name(robot)}[/] ({robot.info.transport}, {host(robot)})"
    return f"{where} · {' · '.join(facts(robot))} [bold cyan]→[/] {doing}"


def go_ahead(args: argparse.Namespace, robot: Robot, doing: str) -> bool:
    """Print the plan with the robot's facts and ask ``Proceed? [y/N]``; ``--yes`` answers
    yes. With no terminal to ask on and no ``--yes``, a :class:`UsageError`."""
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
        live(robot, "stand")
        mode = robot.get_state().mode
        if mode is Mode.STAND:
            _see_armed(robot)
            if robot.armed is not False:  # armed, or the robot does not report gravity
                armed = "armed" if robot.armed else "armed unknown"
                _say(f"{_name(robot)} is already in STAND ({armed}); nothing sent.")
                return EXIT_OK
            _say(
                f"{_name(robot)} is already in STAND (not armed); nothing sent. Waiting for "
                "the robot to arm (0.5 s upright)..."
            )
            try:
                robot.wait_until(
                    lambda s: s.mode is Mode.STAND and robot.armed is not False,
                    timeout=STAND_TIMEOUT_S,
                )
            except StateStaleError:
                raise
            except WaitTimeoutError as exc:
                last = exc.last
                now = last.mode.name if last is not None else "no state"
                raise WaitTimeoutError(
                    f"the robot was not armed in STAND within {STAND_TIMEOUT_S:.1f} s (robot "
                    f"mode {now}): the firmware arms once STAND has been held upright (tilt "
                    "under 30 deg) for 0.5 s. Check that the robot is upright",
                    last=last,
                ) from None
            _say("[green]Armed[/]: ready to balance, [bold]menlo balance[/]")
            return EXIT_OK
        if mode is Mode.MOVE:
            _say(SUPPORT_WARNING)
        if not go_ahead(args, robot, "stand, then wait until armed"):
            return EXIT_CANCELLED
        _say("Standing. Waiting for the robot to arm (0.5 s upright)...")
        robot.stand(timeout=STAND_TIMEOUT_S)
        _say("[green]Armed[/]: ready to balance, [bold]menlo balance[/]")
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
        live(robot, "move")
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
        doing = f"walk {', '.join(parts)} for {args.duration:.1f} s, then balance in place"
        if not go_ahead(args, robot, doing):
            return EXIT_CANCELLED
        _say("Walking. Ctrl-C ends the walk.")
        started = time.monotonic()
        try:
            sent = robot.set_velocity(
                *speeds, duration=args.duration, wait=True, timeout=ARM_TIMEOUT_S
            )
        except NotReadyError:  # no live state (nothing sent), or a fault ended the walk
            raise
        except BaseException:
            with contextlib.suppress(MenloError):
                robot.balance()
            raise
        walked = time.monotonic() - started
        s = robot.get_state()
        if s.mode is Mode.MOVE:
            robot.balance()
        if s.faulted:
            names = ", ".join(fault_names(s)) or "a latched fault"
            raise RobotFaultedError(
                f"the walk ended after {walked:.1f} s: the firmware latched DAMP ({names}); "
                "it stays latched until the firmware restarts",
                state=s,
                action="move",
                preflight=robot.preflight("move"),
                sent=sent,
            )
        if walked < args.duration:
            raise NotReadyError(
                f"the walk ended after {walked:.1f} s of {args.duration:.1f} s; "
                f"robot mode {s.mode.name}",
                action="move",
                preflight=robot.preflight("move"),
            )
        if s.mode is not Mode.MOVE:
            _say(
                f"Sent the walk, but the robot reports {s.mode.name}: the firmware walks in "
                "MOVE only. Run `menlo balance` first."
            )
            return EXIT_NOT_READY
        _say(f"Walk done. Robot mode {s.mode.name}, balancing in place.")
    return EXIT_OK


# ── menlo balance ────────────────────────────────────────────────────────────
def balance(args: argparse.Namespace) -> int:
    with connected(args) as robot:
        live(robot, "move")
        if robot.get_state().mode is not Mode.MOVE:
            doing = "balance: MOVE at zero velocity, the walking policy balances the robot"
            if not go_ahead(args, robot, doing):
                return EXIT_CANCELLED
            robot.balance(timeout=ARM_TIMEOUT_S)  # returns once MOVE is reported
            _say(
                "[green]Balancing[/] in MOVE: ready to walk, e.g. "
                "[bold]menlo walk --vx 0.3 --duration 3[/]"
            )
            return EXIT_OK
        robot.balance()
        time.sleep(0.1)
        # A datagram can be lost, so a second zero follows, but only to a robot still
        # reported in MOVE.
        s = robot.get_state()
        if s.mode is Mode.MOVE and s.age_s <= MAX_STATE_AGE_S:
            robot.balance()
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
            "damp: every actuator stops holding its position and a standing robot falls, "
            "so hang the robot from its gantry hook or seat it on a stool or bench first. "
            "Not an emergency stop: use the E-Stop in Asimov Manager, or cut power at the "
            "battery unit."
        )
        if not go_ahead(args, robot, doing):
            return EXIT_CANCELLED
        robot.damp(timeout=STAND_TIMEOUT_S)  # returns once DAMP is reported
        s = robot.get_state()
        if s.faulted:
            names = ", ".join(fault_names(s)) or "a latched fault"
            _say(f"Robot mode {s.mode.name}, held by {names} until the firmware restarts.")
            return EXIT_OK
        _say("Robot mode DAMP.")
    return EXIT_OK


__all__ = [
    "balance",
    "damp",
    "facts",
    "go_ahead",
    "live",
    "plan",
    "render",
    "snapshot",
    "stand",
    "status",
    "walk",
]
