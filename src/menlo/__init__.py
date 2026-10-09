"""menlo-sdk: Menlo's robots from Python. One subpackage per robot.

    from menlo.asimov import Robot                # the Asimov biped

    with Robot().connect() as robot:              # the saved default robot
        print(robot.preflight("move"))

``menlo.asimov`` drives the Asimov robot. The ``menlo`` command (``menlo setup``) saves the
robots scripts connect to. ``__version__`` is the distribution's.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
