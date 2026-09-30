"""Check whether the robot is ready to stand, walk or run a trajectory. Sends nothing.

Connects to the saved robot (`menlo setup`), or to the robot the MENLO_* environment variables
describe. The other examples import require_ready() from this file, so every script checks
the robot the same way before it moves it.
Run: python examples/check.py
"""

import sys

from menlo.asimov import Action, Mode, NotReadyError, Preflight, Robot

ACTIONS: tuple[Action, ...] = ("stand", "move", "trajectory")
READY_TIMEOUT_S = 2.0  # time for a robot that has just stood to arm (0.5 s upright in STAND)


def advice(result: Preflight) -> str | None:
    """What to do about the problem that blocks `result`, when there is one thing to do."""
    mode = result.state.mode if result.state is not None else None
    if result.has("faulted"):
        return (
            "The firmware latched DAMP. It stays in DAMP until the firmware restarts; "
            "nothing a script sends clears it."
        )
    if result.has("wrong_mode") and mode is Mode.DAMP:
        return "Stand the robot first: python examples/stand.py"
    if result.has("wrong_mode") and mode is Mode.MOVE:
        return "The robot is in MOVE. Leave it there: stop() makes it stand still, balancing."
    return None


def check(robot: Robot, action: Action) -> Preflight:
    """The check once the robot is ready for `action`, or the last one after READY_TIMEOUT_S.
    Waiting lets the SDK see a standing robot armed: it counts the 0.5 s from its own samples."""
    try:
        return robot.wait_ready(action, timeout=READY_TIMEOUT_S)
    except NotReadyError as exc:
        return exc.preflight


def require_ready(robot: Robot, action: Action) -> None:
    """Return once the robot is ready for `action`. If it is not ready within
    READY_TIMEOUT_S, print every problem and what to do, and exit with code 1."""
    result = check(robot, action)
    if not result.ok:
        print(result)
        if (todo := advice(result)) is not None:
            print(todo)
        sys.exit(1)
    for problem in result.problems:  # only warnings are left: fields the robot does not report
        print(f"warning: {problem}")


if __name__ == "__main__":
    with Robot().connect() as robot:
        # region main
        results = [check(robot, action) for action in ACTIONS]
        print(f"robot mode {robot.state.mode.name}, armed {robot.armed}")
        for result in results:
            print(result)  # "ready to move", or "not ready to move:" and one line per problem
        todo = next(filter(None, map(advice, results)), None)
        if todo is not None:
            print(todo)
        # endregion
