"""An agent's tools for the robot: look through its camera and walk, from inside its room.

The robot publishes its camera and microphone as ordinary LiveKit tracks, so an agent
framework in the same room sees and hears it directly; the SDK joins as one more participant
to drive. Connection mode: livekit. Run: python examples/apps/agent_room.py
"""

from __future__ import annotations

from pathlib import Path

from menlo.asimov import Mode, Robot


def look(robot: Robot, path: str = "view.jpg") -> str:
    """Tool: save what the robot sees as a JPEG (needs Pillow) and return the path."""
    # region look
    if not robot.has("camera"):
        return "this robot publishes no camera"
    photo = robot.camera.photo()  # the next fresh frame, rgb8
    Path(path).write_bytes(photo.to_jpeg())
    return path
    # endregion


def walk(robot: Robot, vx: float = 0.0, vyaw: float = 0.0, seconds: float = 2.0) -> str:
    """Tool: walk at vx m/s and turn at vyaw rad/s for at most 5 s, then stop."""
    # region walk
    check = robot.preflight("move")
    if any(p.code != "not_armed" for p in check.blocking):
        return str(check)  # the model reads why it cannot walk
    robot.wait_ready("move")
    sent = robot.set_velocity(vx=vx, vyaw=vyaw, duration=min(seconds, 5.0), wait=True)
    robot.stop()  # end in MOVE at zero velocity, balancing; never STAND after a walk
    return "walked" + (" (clamped to the robot's limits)" if sent.clamped else "")
    # endregion


def main() -> None:
    with Robot().connect("livekit") as robot:
        print(robot.info.endpoint)  # room@url as the identity Asimov Manager issued
        if robot.state.mode is Mode.DAMP:
            robot.wait_ready("stand")
            robot.stand()
            robot.wait_for(Mode.STAND)
        print(look(robot))
        print(walk(robot, vyaw=0.4, seconds=2.0))
        # An agent in the same room calls look() and walk() as its tools.


if __name__ == "__main__":
    main()
