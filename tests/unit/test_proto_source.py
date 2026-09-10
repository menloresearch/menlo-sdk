"""The bindings resolve from an installed asimov-protocol OR the vendored tree — and the
vendored tree alone is enough to speak the wire (that is what an installed wheel has)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("missing", ["asimov_state_pb2", "edge_eol_pb2"])
def test_a_partially_installed_asimov_protocol_falls_back_wholesale_without_importing_it(
    tmp_path, missing
):
    """An installed package missing one module must send ALL imports to the vendored tree,
    decided before anything is imported: a half-import registers the same .proto twice in
    protobuf's process-global pool and crashes. The 'installed' package is a copy of the
    vendored tree with asimov_state_pb2 deleted, put on sys.path under its own name."""
    import shutil

    partial = tmp_path / "site"
    shutil.copytree(VENDOR / "asimov_protocol", partial / "asimov_protocol")
    (partial / "asimov_protocol" / "v1" / f"{missing}.py").unlink()  # incl. one the SDK never uses
    code = """
import os, sys
sys.path.insert(0, os.environ['PARTIAL_SITE'])
from asimov_sdk import _proto
b = _proto.load()
assert b.source == 'vendored', b.source
assert not any(k == 'asimov_protocol' or k.startswith('asimov_protocol.') for k in sys.modules), \
    'the incomplete installed package was imported'
m = b.state.RobotState(protocol_version=1); m.joint_pos.extend([0.0] * 3)
print('wholesale fallback ok')
"""
    env = {**os.environ, "PARTIAL_SITE": str(partial)}
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert "wholesale fallback ok" in r.stdout


def test_an_installed_package_that_differs_from_the_pin_is_not_used(tmp_path):
    """Same module names, different bytes (another protocol release): the vendored tree wins
    and a warning says so, instead of silently decoding with the wrong descriptors."""
    import shutil

    other = tmp_path / "site"
    shutil.copytree(VENDOR / "asimov_protocol", other / "asimov_protocol")
    common = other / "asimov_protocol" / "v1" / "asimov_common_pb2.py"
    common.write_text(common.read_text() + "\n# a different release\n")
    code = """
import logging, os, sys
logging.basicConfig(level=logging.WARNING)
sys.path.insert(0, os.environ['OTHER_SITE'])
from asimov_sdk import _proto
b = _proto.load()
assert b.source == 'vendored', b.source
assert not any(k == 'asimov_protocol' or k.startswith('asimov_protocol.') for k in sys.modules)
print('pinned bindings kept')
"""
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "OTHER_SITE": str(other)},
    )
    assert r.returncode == 0, r.stderr
    assert "pinned bindings kept" in r.stdout and "differs from the bindings" in r.stderr


def test_a_tree_imported_before_the_sdk_is_reused_even_when_it_differs(tmp_path):
    """Descriptors are process-global: if someone already imported a different asimov_protocol,
    importing the vendored copy too would crash. The SDK reuses the imported tree and warns."""
    import shutil

    other = tmp_path / "site"
    shutil.copytree(VENDOR / "asimov_protocol", other / "asimov_protocol")
    common = other / "asimov_protocol" / "v1" / "asimov_common_pb2.py"
    common.write_text(common.read_text() + "\n# a different release\n")
    code = """
import logging, os, sys
logging.basicConfig(level=logging.WARNING)
sys.path.insert(0, os.environ['OTHER_SITE'])
import asimov_protocol.v1.asimov_command_pb2  # someone else got there first
from asimov_sdk import _proto
b = _proto.load()
assert b.source == 'asimov-protocol', b.source
print('reused the imported tree')
"""
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "OTHER_SITE": str(other)},
    )
    assert r.returncode == 0, r.stderr
    assert "reused the imported tree" in r.stdout and "imported before the SDK" in r.stderr
