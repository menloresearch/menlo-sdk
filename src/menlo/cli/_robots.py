"""``menlo setup`` and ``menlo robots``: save a robot, check it answers, pick the default.

A saved robot is what ``Robot().connect()`` reads (``menlo.asimov.store``). Before one is
saved, the checks below connect to it the way a script would.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from menlo.asimov import (
    CompatibilityError,
    ConnectError,
    ConnectionConfig,
    Limits,
    ManagerGrant,
    ProtocolMismatchError,
    Robot,
    RobotStore,
    StoredRobot,
    UdpConfig,
)
from menlo.asimov.connection import MODES, ConnectMode
from menlo.asimov.store import FLAGS
from menlo.cli._common import EXIT_ERROR, EXIT_OK, Asker, StateRate, UsageError

#: How long the UDP check waits for the first state sample.
UDP_TIMEOUT_S = 3.0
#: How long the livekit check waits for the room and, on livekit, the first state sample.
LIVEKIT_TIMEOUT_S = 8.0
#: How long a check counts state samples to report the rate.
RATE_WINDOW_S = 1.0

MODE_CHOICES: list[tuple[str, str]] = [
    ("hybrid", "control and state over UDP, camera and audio over LiveKit"),
    ("udp", "control and state over UDP, no camera or audio"),
    ("livekit", "everything through LiveKit, wherever Asimov Manager is reachable"),
]
_NAME = re.compile(r"[A-Za-z0-9_.-]+")


# ── the checks ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Check:
    """One live check of a robot about to be saved."""

    name: Literal["credential", "udp", "livekit"]
    status: Literal["ok", "warn", "fail", "skip"]
    detail: str


def udp_config(host: str) -> UdpConfig:
    """The UDP connection the checks use for ``host``."""
    return UdpConfig(host)


def local_address(host: str) -> str:
    """This machine's address on the route to ``host``: what Asimov Edge must send UDP
    state to. Nothing is sent to find it."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((host, 8850))
            return str(s.getsockname()[0])
    except OSError:
        return "this machine's address"


def check_credential(robot: StoredRobot) -> tuple[Check, ManagerGrant | None]:
    """Ask Asimov Manager for a token with the credential."""
    try:
        grant = robot.manager().check()
    except ConnectError as exc:
        return Check("credential", "fail", str(exc)), None
    where = f"room {grant.room}"
    if grant.role == "observe":
        return (
            Check(
                "credential",
                "warn",
                f"accepted, role observe: it can watch, not drive over livekit; {where}",
            ),
            grant,
        )
    role = f"role {grant.role}" if grant.role else "role not reported"
    return Check("credential", "ok", f"accepted, {role}, {where}"), grant


def check_udp(robot: StoredRobot) -> Check:
    """Connect on ``udp`` and count state samples."""
    host = robot.udp_host or ""
    started = time.monotonic()
    try:
        with Robot(ConnectionConfig(udp=udp_config(host))).connect(
            "udp", timeout=UDP_TIMEOUT_S
        ) as r:
            first = time.monotonic() - started
            rate = StateRate(r)
            time.sleep(RATE_WINDOW_S)
            return Check(
                "udp",
                "ok",
                f"state from {host} after {first:.2f} s, {rate.hz:.0f} Hz, "
                f"robot mode {r.get_state().mode.name}",
            )
    except CompatibilityError as exc:
        return Check("udp", "fail", str(exc))
    except ProtocolMismatchError as exc:
        return Check("udp", "fail", str(exc))
    except ConnectError as exc:
        if "no state" not in str(exc):
            return Check("udp", "fail", str(exc))
        return Check(
            "udp",
            "fail",
            f"no state from {host} within {UDP_TIMEOUT_S:.0f} s. Asimov Edge sends UDP state "
            f"to one address: it needs udp-control on and udp-state-host set to "
            f"{local_address(host)} (the Asimov Edge parameters in Asimov Manager)",
        )


def check_livekit(robot: StoredRobot, *, state: bool) -> Check:
    """Join the robot's LiveKit room; with ``state``, also wait for state over it."""
    try:
        with Robot(ConnectionConfig(livekit=robot.manager())).connect(
            "livekit", timeout=LIVEKIT_TIMEOUT_S, require_state=state
        ) as r:
            media = [c for c in ("camera", "microphone") if r.has(c)]
            has = ", ".join(media) if media else "no camera or microphone track"
            if not state:
                return Check("livekit", "ok", f"room joined ({has})")
            rate = StateRate(r)
            time.sleep(RATE_WINDOW_S)
            return Check("livekit", "ok", f"room joined, state {rate.hz:.0f} Hz ({has})")
    except CompatibilityError as exc:
        return Check("livekit", "fail", str(exc))
    except ConnectError as exc:
        return Check("livekit", "fail", str(exc))


def run_checks(
    robot: StoredRobot, report: Callable[[Check], None]
) -> tuple[list[Check], str | None]:
    """Every check the robot's connection mode needs, reported as each finishes. Returns
    the results and the room Asimov Manager named."""
    mode = robot.resolved_mode()
    results: list[Check] = []
    room: str | None = None

    def done(check: Check) -> None:
        results.append(check)
        report(check)

    if mode in ("hybrid", "livekit"):
        credential, grant = check_credential(robot)
        done(credential)
        room = grant.room if grant is not None else None
    if mode in ("udp", "hybrid"):
        done(check_udp(robot))
    if mode in ("hybrid", "livekit"):
        if room is None:
            done(Check("livekit", "skip", "not tried: the credential was not accepted"))
        else:
            done(check_livekit(robot, state=mode == "livekit"))
    return results, room


def failed(results: list[Check]) -> bool:
    return any(c.status == "fail" for c in results)


# ── menlo setup ──────────────────────────────────────────────────────────────
def valid_name(name: str) -> str | None:
    if not _NAME.fullmatch(name):
        return "use letters, digits, '.', '_' or '-'"
    return None


def wizard(
    store: RobotStore,
    ask: Asker,
    *,
    name: str | None = None,
    prefill: StoredRobot | None = None,
    check: bool = True,
    default: bool | None = None,
) -> int:
    """Ask for a robot, check it, save it. ``prefill`` supplies answers already known
    (from ``menlo robots add`` flags or the saved entry)."""
    from menlo.cli import _ui

    _ui.intro("menlo setup")
    suggested = name or (prefill.name if prefill else "") or ("asimov" if not store else "")
    name = ask.text("Robot name", default=suggested, validate=valid_name)
    known = prefill if prefill is not None and prefill.name == name else store.get(name)

    mode: ConnectMode = ask.select(  # type: ignore[assignment]
        "Connection mode",
        MODE_CHOICES,
        default=(known.mode if known and known.mode else "hybrid"),
    )
    udp_host = manager_url = credential = None
    if mode in ("udp", "hybrid"):
        udp_host = ask.text(
            "Robot address",
            default=(known.udp_host if known and known.udp_host else ""),
            validate=lambda v: None if v else "the robot's IP address or hostname",
        )
    if mode in ("hybrid", "livekit"):
        manager_url = ask.text(
            "Asimov Manager URL",
            default=(known.manager_url if known and known.manager_url else "")
            or (f"http://{udp_host}" if udp_host else ""),
            validate=lambda v: None if v else "for example http://192.168.22.32",
        )
        _ui.line("[dim]An SDK credential comes from the Developer page of Asimov Manager.[/]")
        saved = known.credential if known else None
        credential = ask.secret("SDK credential", keep=saved is not None) or saved

    robot = StoredRobot(
        name,
        manager_url=manager_url,
        credential=credential,
        room=known.room if known and known.manager_url == manager_url else None,
        mode=mode,
        udp_host=udp_host,
        limits=known.limits if known else None,
    )
    if check:
        _ui.step("Checking")
        results, room = run_checks(robot, _report_in_gutter)
        if room:
            robot = replace(robot, room=room)
        if failed(results) and not ask.confirm("Save anyway?", default=False):
            _ui.outro("Nothing saved.")
            return EXIT_ERROR
    if default is None:
        default = (
            True
            if store.default in (None, name)
            else ask.confirm(f"Make {name} the default robot?", default=True)
        )
    store.put(robot, default=default, allow_manager_change=True)
    mark = " (default)" if store.default == name else ""
    _ui.outro(f"Saved [bold]{name}[/]{mark} in {store.path}. Next: [bold]menlo status[/]")
    return EXIT_OK


def _report_in_gutter(check: Check) -> None:
    from menlo.cli import _ui

    show = {"ok": _ui.ok, "warn": _ui.warn, "fail": _ui.fail, "skip": _ui.warn}[check.status]
    show(f"{check.name}: {check.detail}")


def _report_plain(check: Check) -> None:
    from menlo.cli._ui import console

    mark = {"ok": "[green]✓[/]", "warn": "[yellow]![/]", "fail": "[red]✗[/]", "skip": "[dim]-[/]"}
    console.print(f"{mark[check.status]} {check.name}: {check.detail}")


def setup(args: argparse.Namespace) -> int:
    from menlo.cli import _ui

    if not _ui.interactive():
        raise UsageError(
            "menlo setup asks questions and needs a terminal; in a script use "
            "`menlo robots add NAME --mode MODE ... --no-input`"
        )
    return wizard(RobotStore(), _ui.Prompts(), check=not args.no_check)


# ── menlo robots ─────────────────────────────────────────────────────────────
def as_json(robot: StoredRobot, default: str | None) -> dict[str, object]:
    """A saved robot for ``--json``. The credential is never printed: only whether one is
    saved."""
    limits = robot.limits
    return {
        "name": robot.name,
        "default": robot.name == default,
        "mode": robot.resolved_mode(),
        "udp_host": robot.udp_host,
        "manager_url": robot.manager_url,
        "room": robot.room,
        "credential_saved": robot.credential is not None,
        "limits": None
        if limits is None
        else {"vx": limits.vx, "vy": limits.vy, "vyaw": limits.vyaw},
    }


def list_robots(args: argparse.Namespace) -> int:
    store = RobotStore()
    ordered = sorted(store, key=lambda r: (r.name != store.default, r.name))
    if args.json:
        print(
            json.dumps(
                {"default": store.default, "robots": [as_json(r, store.default) for r in ordered]},
                indent=2,
            )
        )
        return EXIT_OK
    from rich import box
    from rich.table import Table

    from menlo.cli._ui import console

    if not ordered:
        console.print(f"No saved robots in {store.path}. Run [bold]menlo setup[/] to add one.")
        return EXIT_OK
    table = Table(box=box.SIMPLE_HEAD, pad_edge=False, show_edge=False)
    for column in ("", "name", "mode", "robot address", "Asimov Manager", "room"):
        table.add_column(column)
    for r in ordered:
        table.add_row(
            "*" if r.name == store.default else "",
            r.name,
            r.resolved_mode() or "-",
            r.udp_host or "-",
            r.manager_url or "-",
            r.room or "-",
        )
    console.print(table)
    return EXIT_OK


def add_robot(args: argparse.Namespace) -> int:
    from menlo.cli import _ui

    store = RobotStore()
    if valid_name(args.name):
        raise UsageError(f"robot name {args.name!r}: {valid_name(args.name)}")
    known = store.get(args.name)
    try:
        limits = Limits.parse(args.limits) if args.limits else None
    except ValueError as exc:
        raise UsageError(f"--limits: {exc}") from None
    robot = StoredRobot(
        args.name,
        manager_url=args.manager or (known.manager_url if known else None),
        credential=args.credential or (known.credential if known else None),
        room=known.room if known else None,
        mode=args.mode or (known.mode if known else None),
        udp_host=args.udp or (known.udp_host if known else None),
        limits=limits or (known.limits if known else None),
    )
    mode = robot.resolved_mode()
    missing = ["--mode"] if mode is None else [FLAGS[f] for f in robot.missing(mode)]
    if missing:
        if _ui.interactive() and not args.no_input:
            return wizard(
                store,
                _ui.Prompts(),
                name=args.name,
                prefill=robot,
                check=not args.no_check,
                default=True if args.default else None,
            )
        needs = f"connection mode {mode} needs" if mode else f"{args.name} needs"
        raise UsageError(f"{needs} {', '.join(missing)}")
    robot = replace(robot, mode=mode)
    if not args.no_check:
        results, room = run_checks(robot, _report_plain)
        if failed(results):
            _ui.errors.print(f"{args.name} was not saved; --no-check saves it without checking")
            return EXIT_ERROR
        if room:
            robot = replace(robot, room=room)
    store.put(robot, default=True if args.default else None, allow_manager_change=True)
    mark = " (default)" if store.default == robot.name else ""
    _ui.console.print(f"Saved {robot.name}{mark} in {store.path}")
    return EXIT_OK


def remove_robot(args: argparse.Namespace) -> int:
    store = RobotStore()
    gone = store.remove(args.name)
    if gone is None:
        raise KeyError(f"no saved robot named {args.name!r}; have: {store.names()}")
    print(f"Removed {gone.name}")
    return EXIT_OK


def use_robot(args: argparse.Namespace) -> int:
    robot = RobotStore().use(args.name)
    print(f"Default robot: {robot.name}")
    return EXIT_OK


__all__ = ["MODES", "Check", "run_checks", "wizard"]
