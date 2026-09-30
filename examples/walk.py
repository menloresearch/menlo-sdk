"""Walk forward at VX for DURATION_S seconds, then stop. Never stands or damps the robot.

Run stand.py first. The robot must be on its feet, hanging from its gantry hook, with 2 m of
clear floor ahead. After stop() the robot stays in MOVE at zero velocity, balancing.
Run: python examples/walk.py
"""

from check import require_ready
from menlo.asimov import Robot

VX = 0.3  # m/s forward; the firmware caps it at 0.4 m/s
VY = 0.0  # m/s to the left
VYAW = 0.0  # rad/s counter-clockwise; the firmware caps it at 0.8 rad/s
DURATION_S = 3.0

with Robot().connect() as robot:
    require_ready(robot, "move")  # armed STAND or MOVE, fresh state, no fault

    # region main
    # Held and re-sent at 10 Hz for DURATION_S, then zero velocity. wait=True returns after.
    sent = robot.set_velocity(vx=VX, vy=VY, vyaw=VYAW, duration=DURATION_S, wait=True)
    if sent.clamped:
        print("the SDK reduced the speed to the robot's limits:", sent.command)
    robot.stop()  # zero velocity: the robot stays in MOVE and balances in place
    print(f"robot mode {robot.state.mode.name}")
    # endregion
