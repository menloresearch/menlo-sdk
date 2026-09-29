"""menlo-sdk — Menlo's robots from Python. One subpackage per robot.

    from menlo.asimov import Mode, Robot          # the Asimov biped

    with Robot().connect() as robot:
        robot.stand()
        robot.wait_for(Mode.STAND)

``menlo.asimov`` is the whole SDK today; the ``menlo`` console script (``menlo login``)
keeps the credentials scripts connect with. ``__version__`` is the distribution's.
"""

__version__ = "0.1.0rc5"

__all__ = ["__version__"]
