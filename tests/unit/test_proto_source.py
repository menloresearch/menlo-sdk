"""The bindings resolve from an installed asimov-protocol OR the vendored tree — and the
vendored tree alone is enough to speak the wire (that is what `pip install asimov-sdk` gets)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from asimov_sdk import _proto

VENDOR = Path(_proto.__file__).parent / "_vendor"


def test_bindings_load_and_name_their_source():
    b = _proto.load()
    assert b.source in ("asimov-protocol", "vendored")
    assert b.state.RobotState and b.command.RobotCommand and b.common.CONTROL_MODE_DAMP == 0


def test_vendored_tree_is_pinned_and_complete():
    note = (VENDOR / "VENDORED.md").read_text()
    assert "v1.1.0" in note and "d753b84" in note
    for name in ("asimov_command", "asimov_common", "asimov_state", "asimov_diagnostics"):
        assert (VENDOR / "asimov_protocol" / "v1" / f"{name}_pb2.py").exists()
        assert (VENDOR / "asimov_protocol" / "v1" / f"{name}_pb2.pyi").exists(), (
            "typed stubs ship too"
        )


def test_the_sdk_speaks_the_wire_with_only_the_vendored_bindings():
    """Hide any installed asimov-protocol and round-trip a RobotState through the SDK."""
    code = """
import sys
sys.modules['asimov_protocol'] = None  # ImportError on 'from asimov_protocol...'
from asimov_sdk import _proto
b = _proto.load(); assert b.source == 'vendored', b.source
from asimov_sdk.transport.udp import state_from_robot_state
m = b.state.RobotState(current_mode=1, protocol_version=1); m.joint_pos.extend([0.0]*25)
s = state_from_robot_state(m, None); assert s.mode.name == 'STAND' and len(s.joints) == 25
from asimov_sdk._command import Velocity
print('vendored round-trip ok')
"""
    r = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert r.returncode == 0, r.stderr
    assert "vendored round-trip ok" in r.stdout
