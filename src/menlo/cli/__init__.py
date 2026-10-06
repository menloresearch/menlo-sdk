"""``menlo``: save a robot, check it, and move it from the shell.

    menlo setup                                     # ask, check, save a robot
    menlo robots                                    # saved robots, default first
    menlo robots add lab --mode udp --udp 192.168.22.32
    menlo status --watch                            # READY / NOT READY / FAULTED, live
    menlo stand                                     # STAND, then wait until armed
    menlo balance                                   # MOVE at zero velocity: balancing
    menlo walk --vx 0.3 --duration 3                # a bounded walk in MOVE, then balance
    menlo damp                                      # every actuator compliant

Every command that talks to the robot connects the way a script does (``Robot().connect()``
with ``--robot`` and ``--mode`` applied). ``stand``, ``walk``, ``damp`` and ``balance``
outside MOVE print one line, the plan: the robot, its facts (robot mode, armed, faults,
active alerts, hottest joint, battery) and what will happen, and ask ``Proceed? [y/N]``;
``-y``/``--yes`` answers yes. The facts are yours to judge: no command refuses because of
them. The one refusal is no live state: ``Not feasible:``, no question, nothing sent.
``stand`` from MOVE first warns that STAND has no balance loop: hang the robot from its
gantry hook or seat it on a stool or bench. ``balance`` in MOVE never asks. Exit codes:

    0    done
    1    error: connection, robot, or saved robots
    2    usage: the command line cannot run as given (a missing flag, no terminal to ask on)
    3    not feasible or not reached: no live state (nothing sent), or the command was sent
         and the robot did not get there (not armed in time, still in DAMP, a fault)
    4    cancelled: you answered no; nothing was sent
    130  interrupted (Ctrl-C); a walk sends balance() first
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

from menlo import __version__
from menlo.asimov import MenloError, NotReadyError, WaitTimeoutError
from menlo.asimov.connection import MODES
from menlo.cli._common import (
    EXIT_ERROR,
    EXIT_INTERRUPTED,
    EXIT_NOT_READY,
    EXIT_USAGE,
    NotFeasible,
    UsageError,
)

Handler = Callable[[argparse.Namespace], int]


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.run is None:
        parser.print_help()
        return EXIT_USAGE
    name = args.command_name
    try:
        return int(args.run(args))
    except KeyboardInterrupt:
        print(f"\nmenlo {name}: interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED
    except NotFeasible as exc:
        print(exc, file=sys.stderr)
        return EXIT_NOT_READY
    except UsageError as exc:
        print(f"menlo {name}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except NotReadyError as exc:
        print(f"menlo {name}: {exc}", file=sys.stderr)
        return EXIT_NOT_READY
    except WaitTimeoutError as exc:
        print(f"menlo {name}: {exc}", file=sys.stderr)
        return EXIT_NOT_READY
    except (MenloError, ValueError, KeyError, OSError) as exc:
        print(f"menlo {name}: {_message(exc)}", file=sys.stderr)
        return EXIT_ERROR


def _message(exc: BaseException) -> str:
    # KeyError repr-quotes its argument; every other message reads as written.
    return str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)


# ── handlers: each imports its module on use, so `menlo --help` stays light ──
def _run(module: str, function: str) -> Handler:
    def run(args: argparse.Namespace) -> int:
        import importlib

        handler: Handler = getattr(importlib.import_module(f"menlo.cli.{module}"), function)
        return handler(args)

    return run


def build_parser() -> argparse.ArgumentParser:
    connection = argparse.ArgumentParser(add_help=False)
    _connection_flags(connection, default=argparse.SUPPRESS)
    sends = argparse.ArgumentParser(add_help=False)
    sends.add_argument(
        "-y", "--yes", action="store_true", help="do not ask; go ahead once the plan is printed"
    )

    p = argparse.ArgumentParser(
        prog="menlo",
        description="Save an Asimov robot, check it, and move it from the shell.",
        epilog="Exit codes: 0 done, 1 error, 2 usage, 3 not feasible or not reached, "
        "4 cancelled, 130 interrupted.",
    )
    p.add_argument("--version", action="version", version=f"menlo-sdk {__version__}")
    _connection_flags(p, default=None)
    p.set_defaults(run=None, command_name="")
    sub = p.add_subparsers(title="commands", metavar="COMMAND")

    def command(
        name: str, handler: Handler, help: str, *, asks: bool = False
    ) -> argparse.ArgumentParser:
        """A subcommand; ``asks`` for one that prints a plan and asks before it sends."""
        parents = [connection, sends] if asks else [connection]
        cmd = sub.add_parser(name, help=help, description=help, parents=parents)
        cmd.set_defaults(run=handler, command_name=name)
        return cmd

    setup = command("setup", _run("_robots", "setup"), "ask for a robot, check it, save it")
    setup.add_argument("--no-check", action="store_true", help="save without the live checks")

    robots = command("robots", _run("_robots", "list_robots"), "list the saved robots")
    robots.add_argument("--json", action="store_true", help="print JSON")
    robots_sub = robots.add_subparsers(title="robots commands", metavar="ACTION")

    def robots_command(name: str, handler: Handler, help: str) -> argparse.ArgumentParser:
        cmd = robots_sub.add_parser(name, help=help, description=help)
        cmd.set_defaults(run=handler, command_name=f"robots {name}")
        return cmd

    add = robots_command(
        "add",
        _run("_robots", "add_robot"),
        "save a robot, or change a saved one (flags not given keep their saved values)",
    )
    add.add_argument("name")
    add.add_argument("--mode", choices=MODES, default=argparse.SUPPRESS, help="connection mode")
    add.add_argument("--udp", metavar="HOST", help="the robot's address (udp, hybrid)")
    add.add_argument("--manager", metavar="URL", help="Asimov Manager URL (hybrid, livekit)")
    add.add_argument("--credential", metavar="C", help="SDK credential (hybrid, livekit)")
    add.add_argument("--limits", metavar="VX,VY,VYAW", help="velocity limits (m/s, m/s, rad/s)")
    add.add_argument("--default", action="store_true", help="make it the default robot")
    add.add_argument("--no-check", action="store_true", help="save without the live checks")
    add.add_argument(
        "--no-input", action="store_true", help="never ask: a missing flag is an error"
    )
    remove = robots_command("remove", _run("_robots", "remove_robot"), "forget a saved robot")
    remove.add_argument("name")
    use = robots_command("use", _run("_robots", "use_robot"), "make a saved robot the default")
    use.add_argument("name")

    status = command("status", _run("_drive", "status"), "show whether the robot is ready")
    status.add_argument("--watch", action="store_true", help="keep updating; q or Ctrl-C quits")
    status.add_argument("--json", action="store_true", help="print JSON once")

    command(
        "stand",
        _run("_drive", "stand"),
        "put the robot in STAND and wait until armed; from MOVE, support it first (asks first)",
        asks=True,
    )
    command(
        "balance",
        _run("_drive", "balance"),
        "balance in place in MOVE: from STAND enter MOVE (asks first); in MOVE end a walk at once",
        asks=True,
    )

    walk = command(
        "walk",
        _run("_drive", "walk"),
        "walk for a bounded time, then balance in place (asks first)",
        asks=True,
    )
    walk.add_argument("--vx", type=float, default=0.0, help="forward, m/s")
    walk.add_argument("--vy", type=float, default=0.0, help="left, m/s")
    walk.add_argument("--vyaw", type=float, default=0.0, help="counter-clockwise, rad/s")
    walk.add_argument(
        "--duration", type=float, required=True, metavar="S", help="seconds, at most 10"
    )

    command(
        "damp",
        _run("_drive", "damp"),
        "make every actuator compliant; not an emergency stop (asks first)",
        asks=True,
    )
    return p


def _connection_flags(p: argparse.ArgumentParser, *, default: object) -> None:
    p.add_argument(
        "--robot", metavar="NAME", default=default, help="a saved robot (default: the default)"
    )
    p.add_argument(
        "--mode",
        choices=MODES,
        default=default,
        help="connection mode (default: the saved robot's)",
    )


if __name__ == "__main__":
    sys.exit(main())
