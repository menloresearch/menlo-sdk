"""``asimov`` — the credential store from the shell, so a script can be ``Robot().connect()``.

    asimov login http://192.168.22.32 --credential <credential>   # validate, then save
    asimov robots                                                  # what is saved, default first
    asimov use menlo-0001                                          # the default robot
    asimov logout menlo-0001                                       # forget one

``login`` proves the credential before it is kept: the manager is asked for a LiveKit
token exactly as ``connect()`` would ask, and a manager that refuses leaves the store as it
was. The credential itself is minted ON the robot (``asimovctl sdk-token create --role
control``, or the manager's SDK page); the manager has no HTTP endpoint that issues one, so
``login`` takes it from ``--credential``, or prompts without echo. Nothing here ever prints
a credential back.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence

from asimov_sdk import __version__
from asimov_sdk._errors import ConnectError
from asimov_sdk.connection import ManagerConfig
from asimov_sdk.store import RobotStore, StoredRobot, robot_name


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    handler = {"login": _login, "robots": _robots, "use": _use, "logout": _logout}[args.command]
    try:
        return int(handler(args))
    except (ConnectError, ValueError, KeyError, OSError) as exc:
        print(f"asimov {args.command}: {_message(exc)}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="asimov",
        description="Save the robots this machine may drive, so scripts can be Robot().connect().",
    )
    p.add_argument("--version", action="version", version=f"menlo-sdk {__version__}")
    sub = p.add_subparsers(dest="command")

    login = sub.add_parser(
        "login",
        help="validate a credential against a robot's manager and save it",
        description=(
            "Ask the manager for a LiveKit token with the credential — exactly what connect() "
            "does — and, when it answers, save the manager URL and credential. A manager that "
            "refuses leaves the store as it was."
        ),
    )
    login.add_argument(
        "manager_url",
        help="the robot's manager, e.g. http://192.168.22.32 or http://asimov.local:8080",
    )
    login.add_argument(
        "--credential",
        help="an SDK credential from `asimovctl sdk-token create --role control` on the robot; "
        "prompted for (no echo) when omitted",
    )
    login.add_argument(
        "--name", help="what to call this robot in the store (default: its serial, from the room)"
    )
    login.add_argument(
        "--no-default", action="store_true", help="save without making this the default robot"
    )
    login.add_argument(
        "--timeout", type=float, default=5.0, help="seconds to wait for the manager (default 5)"
    )

    sub.add_parser("robots", help="list the saved robots; the default is marked with *")

    use = sub.add_parser("use", help="make a saved robot the default")
    use.add_argument("name")

    logout = sub.add_parser("logout", help="forget a saved robot and its credential")
    logout.add_argument("name")
    return p


def _login(args: argparse.Namespace) -> int:
    credential = args.credential or _prompt_credential()
    if not credential:
        raise ValueError("no credential given")
    manager = ManagerConfig(url=args.manager_url, credential=credential, timeout=args.timeout)
    answer = manager.resolve()  # one mint, one short-lived grant the SFU never sees used
    store = RobotStore()
    entry = StoredRobot(
        name=args.name or robot_name(manager.url, answer.room),
        manager_url=manager.url,
        credential=credential,
        room=answer.room,
    )
    replaced = entry.name in store
    # --name is the user choosing the key; without it the key is whatever the manager
    # answered, and RobotStore refuses to let that overwrite another manager's entry.
    store.put(
        entry,
        default=False if args.no_default else None,
        allow_manager_change=args.name is not None,
    )
    what = "updated" if replaced else "saved"
    mark = " (default)" if store.default == entry.name else ""
    print(f"{what} {entry.name}{mark}: manager {entry.manager_url}, room {answer.room}")
    print(f"  in {store.path}. Scripts can now use Robot().connect().")
    return 0


def _prompt_credential() -> str:
    prompt = "SDK credential (from `asimovctl sdk-token create` on the robot): "
    if sys.stdin.isatty():
        return getpass.getpass(prompt).strip()
    return sys.stdin.readline().strip()  # piped in; never echoed either way


def _robots(_args: argparse.Namespace) -> int:
    store = RobotStore()
    if not len(store):
        print(f"no robots saved in {store.path}; add one with: asimov login <manager-url>")
        return 0
    width = max(len(r.name) for r in store)
    for robot in sorted(store, key=lambda r: (r.name != store.default, r.name)):
        mark = "*" if robot.name == store.default else " "
        room = robot.room or "-"
        print(f"{mark} {robot.name:<{width}}  {robot.manager_url}  {room}")
    return 0


def _use(args: argparse.Namespace) -> int:
    store = RobotStore()
    robot = store.use(args.name)
    print(f"default robot: {robot.name} ({robot.manager_url})")
    return 0


def _logout(args: argparse.Namespace) -> int:
    store = RobotStore()
    gone = store.remove(args.name)
    if gone is None:
        raise KeyError(f"no robot named {args.name!r} in {store.path}; have: {store.names()}")
    print(f"forgot {gone.name} ({gone.manager_url})")
    return 0


def _message(exc: BaseException) -> str:
    # KeyError repr-quotes its argument; every other message reads as written.
    return str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)


if __name__ == "__main__":
    sys.exit(main())
