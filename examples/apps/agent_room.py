"""An agent's tools for the robot: look through its camera and walk, from inside its room.

  g       let the agent move              space   pause the agent, balancing in place
  b       damp (asks first)               x       quit (Ctrl-C also quits)

The robot publishes its camera and microphone as ordinary LiveKit tracks, so an agent
framework in the same room sees and hears it directly; the SDK joins as one more participant
to drive. The walk tool refuses until you press g. Here a short plan stands in for the agent.
Connection mode: livekit. Run stand.py first; the robot must be on its feet, hanging from its
gantry hook, with clear floor around it.
Run in a terminal: python examples/apps/agent_room.py
"""

import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # check.py and keyboard.py

from check import require_ready
from keyboard import NextKey, confirm_and_damp, stop_if_moving, terminal_keys
from menlo.asimov import Robot

MAX_WALK_S = 5.0  # the walk tool never holds a velocity longer than this
TICK_S = 0.1


class Tools:
    """What the agent may call. walk() does nothing until the operator allows it."""

    def __init__(self, robot: Robot) -> None:
        self.robot = robot
        self.may_move = False

    # region main
    def look(self, path: str = "view.jpg") -> str:
        """Save what the robot sees as a JPEG (needs Pillow) and return the path."""
        if not self.robot.has("camera"):
            return "this robot publishes no camera"
        Path(path).write_bytes(self.robot.camera.photo().to_jpeg())
        return path

    def walk(self, vx: float = 0.0, vyaw: float = 0.0, seconds: float = 2.0) -> str:
        """Walk at vx m/s and turn at vyaw rad/s for at most MAX_WALK_S, then stop."""
        if not self.may_move:
            return "paused: the operator has not allowed the robot to move"
        check = self.robot.preflight("move")
        if not check.ok:
            return str(check)  # the model reads why it cannot walk
        duration = min(seconds, MAX_WALK_S)
        sent = self.robot.set_velocity(vx=vx, vyaw=vyaw, duration=duration)
        return f"walking for {duration:.1f} s" + (" (clamped)" if sent.clamped else "")

    # endregion


def main(next_key: NextKey) -> None:
    with Robot().connect("livekit") as robot:
        print(robot.info.endpoint)  # room@url, as the identity Asimov Manager issued
        require_ready(robot, "move")
        tools = Tools(robot)
        # An agent in the room calls look() and walk() as its tools. This plan stands in.
        plan: list[Callable[[], str]] = [tools.look, partial(tools.walk, vx=0.3, seconds=2.0)]
        print("g let the agent move, space pause, b damp, x quit")
        try:
            while True:
                key = next_key(TICK_S)
                if key in ("x", "\x03"):
                    break
                if key == "g":
                    tools.may_move = True
                elif key == " ":
                    tools.may_move = False
                    stop_if_moving(robot)
                    print("paused")
                elif key == "b":
                    tools.may_move = False
                    print(confirm_and_damp(robot, next_key))
                if tools.may_move and plan:
                    print(plan.pop(0)())
        except KeyboardInterrupt:
            pass
        finally:
            stop_if_moving(robot)


if __name__ == "__main__":
    with terminal_keys() as keys:
        main(keys)
