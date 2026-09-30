"""Drive the robot from the keyboard, in small steps that stop when you let go.

  w / s   forward / back        a / d   left / right        q / e   turn left / right
  space   balance in place      t       stand (from DAMP)   b       damp (asks first)
  x       quit (Ctrl-C also quits)

Each movement key sends a short bounded hold (HOLD_S), so the robot stops when you release
the key. Holding a key down repeats it. Every movement is checked with preflight first. The
robot must be on its feet, hanging from its gantry hook, with clear floor around it. On exit
the script sends zero velocity and closes the connection.
Run in a terminal: python examples/keyboard.py
"""

import contextlib
import sys
import time
from collections.abc import Callable, Iterator

from check import require_ready
from menlo.asimov import Mode, Robot

VX = 0.3  # m/s forward and back; the firmware caps it at 0.4 m/s
VY = 0.3  # m/s left and right; the firmware caps it at 0.4 m/s
VYAW = 0.6  # rad/s turning; the firmware caps it at 0.8 rad/s
HOLD_S = 0.3  # how long one key press keeps the robot moving
TICK_S = 0.1  # how often the status line is refreshed
CONFIRM_TIMEOUT_S = 10.0  # a damp question not answered in time is a no

MOVES = {
    "w": (VX, 0.0, 0.0),
    "s": (-VX, 0.0, 0.0),
    "a": (0.0, VY, 0.0),
    "d": (0.0, -VY, 0.0),
    "q": (0.0, 0.0, VYAW),
    "e": (0.0, 0.0, -VYAW),
}
QUIT = ("x", "\x03")  # x, or Ctrl-C read as a key
HELP = "w/s forward/back  a/d left/right  q/e turn  space balance  t stand  b damp  x quit"

#: next_key(timeout) returns the key pressed within timeout seconds, or None.
NextKey = Callable[[float], str | None]


@contextlib.contextmanager
def terminal_keys() -> Iterator[NextKey]:
    """Read single key presses from this terminal, without Enter. Restores the terminal."""
    if not sys.stdin.isatty():
        sys.exit("Run this script in a terminal: it reads single key presses.")
    if sys.platform == "win32":
        import msvcrt

        def next_key(timeout: float) -> str | None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if msvcrt.kbhit():
                    key = msvcrt.getwch()
                    if key in ("\x00", "\xe0"):  # an arrow or function key: skip its code
                        msvcrt.getwch()
                        return None
                    return key
                time.sleep(0.01)
            return None

        yield next_key
    else:
        import os
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)

        def next_key(timeout: float) -> str | None:
            if not select.select([fd], [], [], timeout)[0]:
                return None
            key = os.read(fd, 1).decode(errors="ignore")
            if key == "\x1b":  # an arrow or function key: skip the rest of its sequence
                while select.select([fd], [], [], 0.01)[0]:
                    os.read(fd, 16)
                return None
            return key or None

        tty.setcbreak(fd)  # keys arrive one at a time; Ctrl-C still interrupts
        try:
            yield next_key
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def show(robot: Robot, last: str) -> None:
    """One status line, rewritten in place."""
    s = robot.state
    battery = f"{s.battery.soc_percent:.0f} %" if s.battery else "n/a"
    line = f"{s.mode.name:5}  armed {robot.armed!s:5}  battery {battery:5}  last: {last}"
    print("\r" + line.ljust(100)[:100], end="", flush=True)


def stop_if_moving(robot: Robot) -> None:
    """Zero velocity, only in MOVE: in an armed STAND a velocity, even zero, starts MOVE."""
    if robot.connected and robot.state.mode is Mode.MOVE:
        robot.stop()


def balance(robot: Robot) -> str:
    """Zero velocity: in MOVE the robot stands still and keeps balancing."""
    if robot.state.mode is not Mode.MOVE:
        return f"balance works in MOVE; the robot is in {robot.state.mode.name}"
    robot.stop()
    return "balance in place"


def confirm_and_damp(robot: Robot, next_key: NextKey) -> str:
    """Ask on the terminal, and damp only on y."""
    stop_if_moving(robot)
    print(
        "\nDamp: every actuator goes limp and a standing robot folds. The robot must be "
        "hanging from its gantry hook or seated on a bench. Damp now? [y/N] ",
        end="",
        flush=True,
    )
    answer = next_key(CONFIRM_TIMEOUT_S)
    print(answer if answer and answer.isprintable() else "")
    if answer not in ("y", "Y"):
        return "damp cancelled"
    robot.damp()
    robot.wait_for(Mode.DAMP)
    return "damped"


def stand(robot: Robot) -> str:
    """STAND, only from DAMP and only when preflight allows it."""
    if robot.state.mode is not Mode.DAMP:
        return f"stand works only from DAMP; the robot is in {robot.state.mode.name}"
    check = robot.preflight("stand")
    if not check.ok:
        return f"not ready to stand: {check.blocking[0]}"
    robot.stand()
    robot.wait_for(Mode.STAND)
    return "stand (arms after 0.5 s upright)"


def move(robot: Robot, key: str) -> str:
    """One bounded hold for a movement key, when preflight allows it."""
    check = robot.preflight("move")
    if not check.ok:
        problem = check.blocking[0]
        hint = ", press t to stand" if robot.state.mode is Mode.DAMP else ""
        return f"not ready to move: {problem.code}{hint}"
    vx, vy, vyaw = MOVES[key]
    robot.set_velocity(vx=vx, vy=vy, vyaw=vyaw, duration=HOLD_S)
    return f"{key}: vx {vx:+.2f} vy {vy:+.2f} vyaw {vyaw:+.2f}"


def main(next_key: NextKey) -> None:
    with Robot().connect() as robot:
        require_ready(robot, "stand" if robot.state.mode is Mode.DAMP else "move")
        print(HELP)
        last = "ready"
        try:
            # region main
            while True:
                show(robot, last)
                key = next_key(TICK_S)
                if key is None:
                    continue
                if key in QUIT:
                    break
                if key in MOVES:
                    last = move(robot, key)
                elif key == " ":
                    last = balance(robot)
                elif key == "t":
                    last = stand(robot)
                elif key == "b":
                    last = confirm_and_damp(robot, next_key)
            # endregion
        except KeyboardInterrupt:
            pass
        finally:
            stop_if_moving(robot)
            print()
    # Leaving the with block closes the connection, with a zero velocity if one is held.


if __name__ == "__main__":
    with terminal_keys() as keys:
        main(keys)
