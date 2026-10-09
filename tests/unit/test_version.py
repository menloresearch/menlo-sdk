"""Target policy at production connect, with HTTP confined to a loopback robot."""

from __future__ import annotations

import json

import pytest

from menlo.asimov import ConnectError, ConnectionConfig, LiveKitConfig, Robot, UdpConfig, _version
from menlo.asimov.transport.udp import UdpTransport


@pytest.fixture
def target_robot(edge, monkeypatch, version_document_fetch):
    """Restore real HTTP only for this local endpoint; retain the production connect path."""
    monkeypatch.setattr(_version, "_fetch_version_document", version_document_fetch)
    cfg = ConnectionConfig(
        udp=UdpConfig(
            "127.0.0.1",
            command_port=edge.command_port,
            state_bind=("127.0.0.1", edge.state_port),
            version_port=edge.version.port,
            version_timeout=0.15,
        ),
        livekit=LiveKitConfig("ws://unused", "robot-test", "t"),
    )
    robot = Robot(cfg)
    yield robot, edge.version
    robot.close()


@pytest.mark.parametrize(
    ("version", "accepted"),
    [
        ("0.2.0", True),
        ("0.2.91", True),
        ("0.2.0.dev.202610080305.765cbac", True),
        ("1.2.0", False),
        ("0.3.0", False),
        ("1.3.0", False),
    ],
)
def test_target_policy_accepts_only_the_supported_major_minor_line(version, accepted):
    """One target-policy matrix covers final, patch, real dev, and incompatible lines.

    Keeping the model fixed proves each rejection comes from Robot OS compatibility,
    not from an unrelated model mismatch.
    """
    document = json.dumps({"robot_model": "asimov_1", "robot_os_version": version}).encode()

    if accepted:
        _version.check_target_document(
            document,
            "test://system-info",
            allow_unsupported_target=False,
        )
        return

    with pytest.raises(ConnectError, match="unsupported robot target"):
        _version.check_target_document(
            document,
            "test://system-info",
            allow_unsupported_target=False,
        )


@pytest.mark.parametrize("version", ["0.2.0", "0.2.91"])
def test_supported_patch_connects(target_robot, version, monkeypatch):
    """Supported release patches open UDP after checking the configured Edge, not a proxy."""
    robot, endpoint = target_robot
    endpoint.body = json.dumps({"robot_model": "asimov_1", "robot_os_version": version}).encode()
    monkeypatch.setenv("http_proxy", "http://wrong-robot.invalid:9")
    monkeypatch.setenv("HTTP_PROXY", "http://wrong-robot.invalid:9")
    robot.connect("udp", timeout=1.0)
    assert robot.connected
    assert endpoint.requests == ["/api/version"]


@pytest.mark.parametrize("mode", ["udp", "hybrid"])
@pytest.mark.parametrize(
    "body,status,delay,override",
    [
        (b"{}", 200, 0, False),
        (b"{}", 200, 0, True),
        (b"", 200, 0, True),
        (b"[]", 200, 0, True),
        (b"not JSON", 200, 0, True),
        (b"\xff", 200, 0, True),
        (b"x" * 4097, 200, 0, True),
        (b"{}", 404, 0, True),
        (b"{}", 302, 0, True),
        (b"{}", 503, 0, True),
        (b"{}", 200, 0.25, True),
        (b'{"robot_model":3,"robot_os_version":"0.2.0"}', 200, 0, True),
        (b'{"robot_model":"asimov_1","robot_os_version":"0.3.0"}', 200, 0, False),
        (b'{"robot_model":"unknown","robot_os_version":"0.2.0"}', 200, 0, False),
    ],
)
def test_failed_preflight_opens_no_lane_and_can_retry(
    target_robot, monkeypatch, mode, body, status, delay, override
):
    """Rejected HTTP facts leave no partial session, including hybrid media.

    The same Robot can retry after Edge reports valid facts instead of staying opening.
    """
    robot, endpoint = target_robot
    endpoint.body, endpoint.status, endpoint.delay = body, status, delay
    built = []
    original = ConnectionConfig.transport_for

    def build(config, mode, **kwargs):
        built.append(mode)
        return original(config, mode, **kwargs)

    monkeypatch.setattr(ConnectionConfig, "transport_for", build)
    with pytest.raises(ConnectError):
        robot.connect(mode, allow_unsupported_target=override, require_state=False)
    assert built == [mode]
    assert robot._transport is None and robot._closed and not robot._opening
    assert robot._keepalive is None
    assert endpoint.requests == ["/api/version"]
    assert endpoint.wait_idle()
    endpoint.body = b'{"robot_model":"asimov_1","robot_os_version":"0.2.0"}'
    endpoint.status, endpoint.delay = 200, 0
    robot.connect("udp", timeout=1.0)
    assert built == [mode, "udp"] and robot.connected


@pytest.mark.parametrize(
    "version",
    [
        "0.2.0.dev.202610061234.abcdef1",
        "0.2.0.dev.202610061234.abcdef1.dirty.202610061235",
        "0.2.0rc1",
        "0.2.0-rc.1",
        "0.2.0.dev1",
        "0.2.0-dev.2",
    ],
)
def test_supported_channels_connect(target_robot, version):
    """Valid prerelease and development facts inherit their supported major/minor line."""
    robot, endpoint = target_robot
    endpoint.body = json.dumps({"robot_model": "asimov_1", "robot_os_version": version}).encode()
    robot.connect("udp", timeout=1.0)
    assert robot.connected


@pytest.mark.parametrize(
    "version",
    [
        "",
        "0.2",
        "v0.2.0",
        "00.2.0",
        "0.2.0 garbage",
        "0.2.0+foo",
        "0.2.0rc",
        "0.2.0.dev.bad.sha",
        "0.2.0.dirty.202610061235",
        "0.2.0.dev.202610061234.abcdef1.dirty",
        "0.2.0.dev.202610061234.abcdef1.dirty.bad",
        "0.2.0.dev.202610061234.abcdef1.dirty.20261006123",
        "0.2.0.dev.202610061234.abcdef1.dirty.202610061235.extra",
    ],
)
def test_invalid_versions_cannot_use_target_override(target_robot, version):
    """A target override cannot turn malformed clean or dirty versions into valid facts."""
    robot, endpoint = target_robot
    endpoint.body = json.dumps({"robot_model": "asimov_1", "robot_os_version": version}).encode()
    with pytest.raises(ConnectError, match="invalid robot_os_version"):
        robot.connect("udp", allow_unsupported_target=True)
    assert robot._transport is None


def test_target_override_allows_unknown_valid_target(target_robot):
    """A deliberate development override permits a well-formed unsupported model and line."""
    robot, endpoint = target_robot
    endpoint.body = b'{"robot_model":"future_robot","robot_os_version":"9.8.7"}'
    robot.connect("udp", allow_unsupported_target=True, timeout=1.0)
    assert robot.connected


def test_unreachable_endpoint_cannot_use_target_override(target_robot):
    """An absent Edge listener cannot become compatibility approval through a target override."""
    robot, endpoint = target_robot
    endpoint.close()
    with pytest.raises(ConnectError, match="could not read robot version"):
        robot.connect("udp", allow_unsupported_target=True)
    assert robot._transport is None and not robot._opening


def test_already_connected_does_not_repeat_preflight(target_robot):
    """Session admission rejects a second connect before it contacts Edge or replaces state."""
    robot, endpoint = target_robot
    robot.connect("udp", timeout=1.0)
    transport = robot._transport
    with pytest.raises(RuntimeError, match="already connected"):
        robot.connect("udp")
    assert endpoint.requests == ["/api/version"]
    assert robot._transport is transport and robot.connected


def test_pure_livekit_uses_native_rpc_without_querying_lan(edge, monkeypatch):
    """Remote-only configs read Edge facts through native RPC, never the unreachable LAN."""
    from menlo.asimov import connection as connection_module
    from menlo.asimov.transport.livekit import LiveKitTransport
    from tests.conftest import FakeLiveKitClient

    def unexpected(*args, **kwargs):
        pytest.fail("pure LiveKit queried the LAN endpoint")

    monkeypatch.setattr(_version, "_fetch_version_document", unexpected)
    client = FakeLiveKitClient(edge)

    def build(url, room, *, token, **kwargs):
        return LiveKitTransport(url, room, token=token, client=client, **kwargs)

    monkeypatch.setattr(connection_module, "LiveKitTransport", build)
    robot = Robot(ConnectionConfig(livekit=LiveKitConfig("ws://unused", "room", "t")))
    try:
        robot.connect("livekit", timeout=1.0)
        assert robot.connected
        assert client.rpc_calls == [("MENLO-TEST", "edge.getSystemInfo", "{}", 1.0)]
    finally:
        robot.close()


def test_transport_bound_udp_checks_target_before_opening_socket(
    edge, monkeypatch, version_document_fetch
):
    """Robot(UdpTransport(...)) cannot bypass the HTTP target check."""
    monkeypatch.setattr(_version, "_fetch_version_document", version_document_fetch)
    edge.version.body = b'{"robot_model":"future_robot","robot_os_version":"9.8.7"}'
    tx = UdpTransport(
        "127.0.0.1",
        command_port=edge.command_port,
        state_bind=("127.0.0.1", edge.state_port),
        version_port=edge.version.port,
    )
    robot = Robot(tx)
    with pytest.raises(ConnectError, match="unsupported robot target"):
        robot.open(require_state=False)
    assert tx._sock is None
    assert edge.received == []
    assert edge.version.requests == ["/api/version"]

    edge.version.body = b'{"robot_model":"asimov_1","robot_os_version":"0.2.0"}'
    robot.open(timeout=1.0)
    assert robot.connected
    robot.close()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_version_timeout_must_be_finite_positive(timeout):
    """Invalid HTTP budgets fail at config construction instead of creating unbounded I/O."""
    with pytest.raises(ValueError, match="finite and positive"):
        UdpConfig("localhost", version_timeout=timeout)


def test_target_success_does_not_waive_protocol_mismatch(target_robot, edge):
    """Supported HTTP facts cannot authorize a mismatched first-state wire protocol."""
    from menlo.asimov import ProtocolMismatchError

    robot, endpoint = target_robot
    edge.state.protocol_version = 99
    with pytest.raises(ProtocolMismatchError):
        robot.connect("udp", timeout=1.0)
    assert endpoint.requests == ["/api/version"]
    assert not robot.connected and not robot._opening


def test_failed_reconnect_retires_previous_closed_session(target_robot):
    """Rejected new facts cannot expose a previous robot's identity or prevent a clean retry."""
    robot, endpoint = target_robot
    robot.connect("udp", timeout=1.0)
    robot.close()
    previous = robot._transport
    endpoint.body = b"{}"
    with pytest.raises(ConnectError):
        robot.connect("hybrid")
    assert robot._transport is None and robot._closed and not robot._opening
    assert robot._info is None and robot._state is None
    assert not robot._derived_caps
    assert robot._keepalive is None or not robot._keepalive.is_alive()
    endpoint.body = b'{"robot_model":"asimov_1","robot_os_version":"0.2.0"}'
    robot.connect("udp", timeout=1.0)
    assert robot._transport is not previous and robot.connected


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_version_port_rejects_invalid_tcp_ports(port):
    """A bad endpoint port is a caller error, rejected before any session reservation."""
    with pytest.raises(ValueError, match="between 1 and 65535"):
        UdpConfig("localhost", version_port=port)


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize(
    "key,first,last",
    [
        ("robot_model", "unknown", "asimov_1"),
        ("robot_model", "asimov_1", "unknown"),
        ("robot_os_version", "0.3.0", "0.2.0"),
        ("robot_os_version", "0.2.0", "0.3.0"),
    ],
)
def test_duplicate_facts_never_authorize_transport(target_robot, key, first, last, override):
    """Conflicting identities or versions fail closed regardless of key order or target override."""
    robot, endpoint = target_robot
    other = '"robot_os_version":"0.2.0"' if key == "robot_model" else '"robot_model":"asimov_1"'
    endpoint.body = f'{{{other},"{key}":"{first}","{key}":"{last}"}}'.encode()
    with pytest.raises(ConnectError, match="malformed"):
        robot.connect("udp", allow_unsupported_target=override)
    assert robot._transport is None


@pytest.mark.parametrize("status", [200, 503])
def test_http_response_closed_before_return_or_error(target_robot, monkeypatch, status):
    """HTTP/1.0 transfers socket ownership to its response; preflight must close that owner."""
    robot, endpoint = target_robot
    endpoint.status = status
    responses = []
    original = _version.HTTPConnection

    class TrackedConnection(original):
        def getresponse(self):
            response = super().getresponse()
            responses.append(response)
            return response

    monkeypatch.setattr(_version, "HTTPConnection", TrackedConnection)
    if status == 200:
        robot.connect("udp", timeout=1.0)
    else:
        with pytest.raises(ConnectError):
            robot.connect("udp")
    assert len(responses) == 1
    assert responses[0].isclosed()


@pytest.mark.parametrize("model,version", [("future_robot", "0.2.0"), ("asimov_1", "9.8.7")])
def test_wire_skew_cannot_bypass_target_policy(target_robot, model, version):
    """A wire override cannot admit an unsupported model or Robot OS line."""
    robot, endpoint = target_robot
    endpoint.body = json.dumps({"robot_model": model, "robot_os_version": version}).encode()
    with pytest.raises(ConnectError, match="unsupported robot target"):
        robot.connect("udp", allow_version_skew=True)
    assert robot._transport is None


@pytest.mark.parametrize("target_override", [False, True])
def test_target_override_does_not_waive_wire_mismatch(target_robot, edge, target_override):
    """Unsupported but valid HTTP facts pass the target override, then fail wire admission."""
    from menlo.asimov import ProtocolMismatchError

    robot, endpoint = target_robot
    if target_override:
        endpoint.body = b'{"robot_model":"future_robot","robot_os_version":"9.8.7"}'
    edge.state.protocol_version = 99
    with pytest.raises(ProtocolMismatchError):
        robot.connect(
            "udp", allow_unsupported_target=target_override, allow_version_skew=False, timeout=1.0
        )
    assert endpoint.requests == ["/api/version"]
    assert not robot.connected and not robot._opening


@pytest.mark.parametrize("content_length", [False, True])
def test_remaining_timeout_updates_actual_read_socket(target_robot, monkeypatch, content_length):
    """HTTP/1.0 hands off its socket; body reads must use that socket's remaining budget.

    A duplicated deadline descriptor has separate Python timeout state and cannot set it.
    """
    import socket
    from http.server import BaseHTTPRequestHandler

    if not content_length:
        original_send_header = BaseHTTPRequestHandler.send_header

        def send_header(handler, keyword, value):
            if keyword != "Content-Length":
                original_send_header(handler, keyword, value)

        monkeypatch.setattr(BaseHTTPRequestHandler, "send_header", send_header)

    robot, endpoint = target_robot
    endpoint.delay = 0.03
    read_sockets = []
    timeouts = []
    original_connection = _version.HTTPConnection
    original_settimeout = socket.socket.settimeout

    def settimeout(sock, timeout):
        timeouts.append((sock, timeout))
        return original_settimeout(sock, timeout)

    class TrackedConnection(original_connection):
        def getresponse(self):
            read_sockets.append(self.sock)
            response = super().getresponse()
            assert self.sock is None  # HTTP/1.0 response now owns the read socket.
            return response

    monkeypatch.setattr(socket.socket, "settimeout", settimeout)
    monkeypatch.setattr(_version, "HTTPConnection", TrackedConnection)
    robot.connect("udp", timeout=1.0)
    remaining = [timeout for sock, timeout in timeouts if sock is read_sockets[0]]
    assert len(remaining) >= 2
    assert remaining[0] == robot.config.udp.version_timeout
    assert 0 < remaining[-1] < remaining[0] - endpoint.delay / 2


def test_truncated_http_body_cannot_use_target_override(target_robot, monkeypatch):
    """Edge closes before its declared body length; a target override cannot admit the facts."""
    from http.server import BaseHTTPRequestHandler

    robot, endpoint = target_robot
    original = BaseHTTPRequestHandler.send_header

    def send_header(handler, keyword, value):
        if keyword == "Content-Length":
            value = str(len(endpoint.body) + 10)
        original(handler, keyword, value)

    monkeypatch.setattr(BaseHTTPRequestHandler, "send_header", send_header)
    with pytest.raises(ConnectError, match="truncated"):
        robot.connect("udp", allow_unsupported_target=True)
    assert robot._transport is None and not robot._opening
