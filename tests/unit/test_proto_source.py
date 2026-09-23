"""The bindings come from the installed ``asimov-protocol`` package, and that package is the
release line the SDK declares."""

from __future__ import annotations

from importlib.metadata import version

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from menlo.asimov import _proto, robots


def test_bindings_load_from_asimov_protocol():
    b = _proto.load()
    assert b.source == "asimov-protocol"
    assert b.state.RobotState and b.command.RobotCommand and b.common.CONTROL_MODE_DAMP == 0


def test_the_installed_protocol_is_the_declared_line():
    """pyproject's asimov-protocol specifier: MAJOR tracks the wire directory the SDK speaks
    (v1/, PROTOCOL_VERSION 1), so a 2.x would be a different wire, not a newer package.
    The specifier is read from the installed SDK's own metadata, so this test follows it."""
    from importlib.metadata import requires

    (spec,) = [r for r in requires("menlo-sdk") or [] if r.startswith("asimov-protocol")]
    specifier = SpecifierSet(spec.split(" ", 1)[1] if " " in spec else spec[len("asimov-protocol") :])
    installed = Version(version("asimov-protocol"))
    assert installed in specifier, (installed, specifier)
    assert Version("2.0") not in specifier and specifier.contains("1.2.1rc1", prereleases=True)
    assert installed.major == robots.PROTOCOL_VERSION


def test_a_state_round_trips_through_the_installed_bindings():
    from menlo.asimov.transport.udp import state_from_robot_state

    b = _proto.load()
    m = b.state.RobotState(current_mode=1, protocol_version=1)
    m.joint_pos.extend([0.0] * 25)
    s = state_from_robot_state(m, None)
    assert s.mode.name == "STAND" and len(s.joints) == 25
