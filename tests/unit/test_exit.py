"""A script that ends without close() still leaves a held velocity at zero."""

from __future__ import annotations

import subprocess
import sys
import textwrap

from tests.conftest import FakeEdge

ZERO = (0.0, 0.0, 0.0)


def _run(edge: FakeEdge, body: str) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""
        from menlo.asimov import ConnectionConfig, Robot, UdpConfig

        udp = UdpConfig(
            "127.0.0.1",
            version_port={edge.version.port}, command_port={edge.command_port},
            state_bind=("127.0.0.1", {edge.state_port}),
        )
        robot = Robot(ConnectionConfig(udp=udp)).connect("udp", timeout=3.0)
        """
    ) + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False
    )


def test_unrelated_fake_edge_lifecycle_does_not_start_version_http():
    """UDP-only fixture setup and teardown never allocate an unused HTTP listener."""
    edge = FakeEdge()
    try:
        assert edge._version is None
    finally:
        edge.close()
    assert edge._version is None


def test_opted_in_fake_edge_reuses_version_http_and_stops_it_on_teardown():
    """Explicit preflight access reuses one listener, and teardown stops its server thread."""
    edge = FakeEdge()
    try:
        endpoint = edge.version
        assert edge.version is endpoint
        assert endpoint.thread.is_alive()
    finally:
        edge.close()
    assert not endpoint.thread.is_alive()
    assert endpoint.server.fileno() == -1


def test_a_script_that_exits_holding_a_velocity_sends_zero(edge):
    edge.set_mode("move")
    done = _run(
        edge,
        """
        import time
        robot.set_velocity(vx=0.2, wait=False)  # held until the next command
        time.sleep(0.3)
        # no close(), no `with`: the interpreter exits here
        """,
    )
    assert done.returncode == 0, done.stderr
    assert edge.wait_for(lambda rx: bool(edge.velocities()) and edge.velocities()[-1] == ZERO)
    assert (0.2, 0.0, 0.0) in edge.velocities()


def test_a_script_that_exits_after_a_hold_false_packet_sends_zero(edge):
    edge.set_mode("move")
    done = _run(edge, "robot.set_velocity(vx=0.2, hold=False)\n")
    assert done.returncode == 0, done.stderr
    assert edge.wait_for(lambda rx: edge.velocities() == [(0.2, 0.0, 0.0), ZERO])


def test_a_script_that_exits_holding_nothing_sends_nothing(edge):
    edge.set_mode("move")
    done = _run(edge, "robot.get_state()\n")
    assert done.returncode == 0, done.stderr
    assert edge.received == []
