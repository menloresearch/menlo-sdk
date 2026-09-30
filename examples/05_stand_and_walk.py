"""Stand the robot up if it is in DAMP, walk forward at 0.2 m/s for 3 s, and stop.

The robot must be on its feet, hanging from its gantry hook, with 1 m of clear floor ahead.
Connection mode: the saved robot's. Run: python examples/05_stand_and_walk.py
"""

from menlo.asimov import Mode, Robot

with Robot().connect() as robot:
    # region stand
    if robot.state.mode is Mode.DAMP:
        robot.wait_ready("stand")  # fresh state, no latched fault, battery and actuators ok
        robot.stand()
        robot.wait_for(Mode.STAND)
    # endregion

    # region walk
    robot.wait_ready("move")  # armed: STAND held upright for 0.5 s
    robot.set_velocity(vx=0.2, duration=3.0, wait=True)
    robot.stop()  # zero velocity: the robot stays in MOVE and balances in place
    # endregion
    print(f"robot mode {robot.state.mode.name}")
