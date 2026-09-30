"""The SDK's heavy dependencies load only when they are used."""

from __future__ import annotations

import subprocess
import sys

from tests.conftest import FakeEdge


def test_importing_the_sdk_and_driving_over_udp_loads_no_livekit_and_no_cli_libraries(edge):
    edge: FakeEdge
    script = f"""
import sys
from menlo.asimov import ConnectionConfig, Robot, UdpConfig
cfg = ConnectionConfig(udp=UdpConfig(
    "127.0.0.1", command_port={edge.command_port}, state_bind=("127.0.0.1", {edge.state_port})))
with Robot(cfg).connect("udp", timeout=3.0) as robot:
    robot.state
loaded = sorted(m for m in ("livekit", "questionary", "rich") if m in sys.modules)
print(",".join(loaded))
"""
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=True
    )
    assert out.stdout.strip() == "", f"loaded: {out.stdout.strip()}"


def test_the_cli_entry_point_imports_without_its_ui_libraries():
    script = "import sys, menlo.cli; print('rich' in sys.modules, 'questionary' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=True
    )
    assert out.stdout.strip() == "False False"
