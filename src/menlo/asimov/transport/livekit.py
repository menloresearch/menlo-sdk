"""The robot's LiveKit room, as a transport: the ``hybrid`` and ``livekit`` connection modes.

Two transports live here, and with ``UdpTransport`` they are the three ways to reach a
robot. Nothing above the transport knows which is in use::

    udp       commands UDP 8850 / state UDP 8851         no camera or audio
    hybrid    commands UDP 8850 / state UDP 8851         camera and audio over LiveKit
    livekit   commands and state over LiveKit data       camera and audio over LiveKit

The wire in the room, as Asimov Edge speaks it::

    commands  ->  data topic "commands"   one bare asimov.io.RobotCommand, reliable packets
    state     <-  data track "state"      one bare asimov.io.RobotState per frame, ordered,
                                          user_timestamp = Asimov Edge's receive clock

The same protobufs as over UDP: no envelope, no framing, no type tag; the topic identifies
the type, as the port does over UDP. Commands land in Asimov Edge's arbiter beside the
robot's other controllers, at the lowest priority, and pass the same safety layer. On
``livekit``, Asimov Edge stops a held velocity when the SDK sends zero or leaves the room.

Every ``livekit`` import lives in ``_livekit_client``, behind a lazy function; importing
this module, and driving a robot on ``udp``, never loads it.

**The SDK never holds a LiveKit API secret.** There is no ``api_key``/``api_secret``
parameter anywhere: a caller presents a token the robot's manager minted, or a callable
that fetches a fresh one each time the room is joined.

There is no ``identity`` parameter either. A participant's identity is a claim inside the
token (``sub``) and the server ignores whatever a client says about it, so an argument for
it would be a lie the SDK told its caller. ``identity`` is READ BACK from the token and
reported on the transport and in ``endpoint``.
"""

from __future__ import annotations

import contextlib
import logging
import threading

from menlo.asimov._command import Command
from menlo.asimov._errors import ConnectError, NotConnectedError, UnsupportedError
from menlo.asimov._media import AudioChunk, Frame
from menlo.asimov._state import State, TransportKind
from menlo.asimov.transport._livekit_client import LiveKitClient, TokenProvider, _LiveKitClient
from menlo.asimov.transport._wire import (
    COMMAND_TOPIC,
    STATE_TRACK,
    _pb,
    decode_state,
    encode_command,
)
from menlo.asimov.transport.base import (
    AudioCallback,
    ControllerCallback,
    FrameCallback,
    OutcomeCallback,
    StateCallback,
)
from menlo.asimov.transport.udp import COMMAND_PORT, STATE_PORT, UdpTransport

log = logging.getLogger("menlo.asimov.transport.livekit")

#: How long ``open()`` waits for the room's media tracks before it decides what this
#: transport carries. A capability is claimed from a track that ARRIVED, never from one a
#: room might publish later, so this is the budget for the robot to bring its camera up.
MEDIA_TIMEOUT_S = 3.0


class _MediaPlane:
    """The media half of a LiveKit room: frames in, microphone in, speaker out.

    Both transports below own one. It keeps ``capabilities`` honest: ``camera`` and
    ``microphone`` appear only once the matching track is actually subscribed, and go away
    again when the room does, which is what makes a room with no video track raise
    ``UnsupportedError`` instead of handing out a stream that never yields.
    """

    kind: TransportKind
    capabilities: frozenset[str] = frozenset({"drive", "state"})
    #: What the command/state half carries on its own, media aside.
    _base_capabilities: frozenset[str] = frozenset({"drive", "state"})
    _lk: LiveKitClient
    _media_timeout: float
    _on_frame: list[FrameCallback]
    _on_audio: list[AudioCallback]

    @property
    def identity(self) -> str | None:
        """Who the SDK joined the room AS, read out of the token it presented. ``None``
        before the room is joined, and when the token claims no identity."""
        return self._lk.identity

    def _wire_media(self, client: LiveKitClient, media_timeout: float) -> None:
        self._lk = client
        self._media_timeout = media_timeout
        self._on_frame = []
        self._on_audio = []
        client.on_video(self._deliver_frame)
        client.on_audio(self._deliver_audio)
        client.on_tracks(lambda _tracks: self._refresh_capabilities())
        self._refresh_capabilities()

    def _refresh_capabilities(self) -> None:
        media = set(self._lk.tracks) if self._lk.connected else set()
        if self._lk.connected:
            # The SDK can publish an audio track into the room whenever it is joined;
            # whether the robot plays it is Asimov Edge's business, not a guess made here.
            media.add("speaker")
        self.capabilities = self._base_capabilities | media

    def _await_media(self) -> None:
        self._lk.wait_for_tracks(self._media_timeout)
        self._refresh_capabilities()

    def _deliver_frame(self, frame: Frame) -> None:
        for cb in tuple(self._on_frame):
            cb(frame)

    def _deliver_audio(self, chunk: AudioChunk) -> None:
        for cb in tuple(self._on_audio):
            cb(chunk)

    def subscribe_frames(self, callback: FrameCallback) -> None:
        if "camera" not in self.capabilities:
            raise UnsupportedError("camera", self.kind)
        self._on_frame.append(callback)

    def subscribe_audio(self, callback: AudioCallback) -> None:
        if "microphone" not in self.capabilities:
            raise UnsupportedError("microphone", self.kind)
        self._on_audio.append(callback)

    def play_audio(self, chunk: AudioChunk) -> None:
        if "speaker" not in self.capabilities:
            raise UnsupportedError("speaker", self.kind)
        self._lk.publish_audio(chunk)


class LiveKitTransport(_MediaPlane):
    """The ``livekit`` connection mode: commands, state, video and audio all through one
    LiveKit room.

    Asimov Edge joins the same room and answers on the ``state`` data track, at 10 Hz.
    Nothing needs the robot's network, so this reaches a robot wherever its Asimov Manager
    and LiveKit server are reachable.

    The room carries no per-command verdict: ``subscribe_outcome`` and
    ``subscribe_controller_change`` are honoured and never fire, and
    ``Sent.wait_outcome()`` returns ``Unknown``.
    """

    kind: TransportKind = "livekit"
    #: An SFU round trip, not a LAN datagram.
    default_outcome_timeout: float = 1.0
    silence_hint: str = (
        "The room was joined, so the token and the URL are good. Is the robot's edge in "
        f"this room, and is it publishing the {STATE_TRACK!r} data track?"
    )

    def __init__(
        self,
        url: str,
        room: str,
        *,
        token: TokenProvider,
        media_timeout: float = MEDIA_TIMEOUT_S,
        connect_timeout: float = 10.0,
        client: LiveKitClient | None = None,
    ) -> None:
        """``client`` is the seam the unit suite fakes; leave it ``None`` to talk to a real
        room. There is deliberately no ``api_key``/``api_secret`` (bring a token) and no
        ``identity`` (the token claims it; :attr:`identity` reads it back)."""
        #: The room this transport joins: the robot's, named by its serial.
        self.room, self._url = room, url
        self._lk_open = False
        self._seq = 0
        self._lock = threading.Lock()
        self._on_state: list[StateCallback] = []
        self._on_outcome: list[OutcomeCallback] = []
        self._on_controller: list[ControllerCallback] = []
        if client is None:
            client = _LiveKitClient(url, room, token=token, connect_timeout=connect_timeout)
        self._wire_media(client, media_timeout)
        self.endpoint = self._describe()
        # Registered once, here rather than in open(): a reopened transport must not end up
        # with the state track wired twice and every sample delivered twice.
        client.on_data_track(STATE_TRACK, self._on_state_frame)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def open(self) -> None:
        if self._lk_open:
            raise ConnectError("this LiveKitTransport is already open")
        _pb()  # fail here, with the dependency install hint, not on the loop thread
        self._lk.connect()
        self._lk_open = True
        self.endpoint = self._describe()
        self._await_media()

    def _describe(self) -> str:
        """The address, plus who we are in the room once we know: two SDK sessions in one
        room differ only by identity, and an error naming the room alone would not say
        which of them went quiet."""
        who = self.identity
        return f"{self.room}@{self._url}" + (f" as {who}" if who else "")

    def close(self) -> None:
        self._lk_open = False
        with contextlib.suppress(Exception):  # close() never raises
            self._lk.close()
        self.endpoint = self._describe()
        self._refresh_capabilities()

    # ── subscriptions ────────────────────────────────────────────────────────
    def subscribe_state(self, callback: StateCallback) -> None:
        self._on_state.append(callback)

    def subscribe_outcome(self, callback: OutcomeCallback) -> None:
        # Honoured, never fired: the room has no outcome topic.
        self._on_outcome.append(callback)

    def subscribe_controller_change(self, callback: ControllerCallback) -> None:
        self._on_controller.append(callback)

    # ── in ───────────────────────────────────────────────────────────────────
    def _on_state_frame(self, payload: bytes, edge_timestamp_us: int | None) -> None:
        try:
            state: State = decode_state(payload, None, edge_timestamp_us)  # Robot names the joints
        except Exception:  # a bad frame must not kill the room
            log.debug("dropped an undecodable state frame (%d bytes)", len(payload), exc_info=True)
            return
        for cb in tuple(self._on_state):
            try:
                cb(state)
            except Exception:  # one bad subscriber must not stop the stream
                log.exception("state subscriber raised")

    # ── out ──────────────────────────────────────────────────────────────────
    def send(self, command: Command) -> int:
        if not self._lk_open:
            raise NotConnectedError("this LiveKitTransport is not open")
        with self._lock:
            self._seq = (self._seq + 1) & 0xFFFFFFFF
            seq = self._seq
        self._lk.publish_data(encode_command(command, seq), topic=COMMAND_TOPIC)
        return seq


class HybridTransport(_MediaPlane):
    """The ``hybrid`` connection mode: control and state over UDP, camera and audio through
    the LiveKit room.

    The mode to pick on the robot's network: commands keep UDP's latency and its
    independence from any server, while the camera and microphone ride the LiveKit server
    that already carries them. Control and media fail independently: a room that drops
    takes the frames with it and leaves the robot driveable.
    """

    kind: TransportKind = "hybrid"
    default_outcome_timeout: float = UdpTransport.default_outcome_timeout
    #: State rides the UDP half here, so the silence is a UDP silence.
    silence_hint: str = UdpTransport.silence_hint

    def __init__(
        self,
        host: str,
        *,
        livekit_url: str,
        room: str,
        token: TokenProvider,
        command_port: int = COMMAND_PORT,
        state_bind: tuple[str, int] = ("0.0.0.0", STATE_PORT),
        state_source: str | None = None,
        media_timeout: float = MEDIA_TIMEOUT_S,
        connect_timeout: float = 10.0,
        client: LiveKitClient | None = None,
    ) -> None:
        """``client`` is the seam the unit suite fakes. No ``api_key``/``api_secret``, and
        no ``identity``: the token claims it; :attr:`identity` reads it back."""
        self._udp = UdpTransport(
            host, command_port=command_port, state_bind=state_bind, state_source=state_source
        )
        self.room, self._url = room, livekit_url
        self._base_capabilities = self._udp.capabilities
        if client is None:
            client = _LiveKitClient(livekit_url, room, token=token, connect_timeout=connect_timeout)
        self._wire_media(client, media_timeout)
        self.endpoint = self._describe()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def open(self) -> None:
        self._udp.open()  # raises if it is already open; nothing has been started yet
        try:
            self._lk.connect()
        except Exception:
            self._udp.close()  # never leave a socket and a reader thread behind
            raise
        self.endpoint = self._describe()
        self._await_media()

    def _describe(self) -> str:
        who = self.identity
        return f"{self._udp.endpoint} + {self.room}@{self._url}" + (f" as {who}" if who else "")

    def close(self) -> None:
        with contextlib.suppress(Exception):  # close() never raises
            self._lk.close()
        self.endpoint = self._describe()
        self._refresh_capabilities()
        self._udp.close()

    # ── control: UDP, unchanged ─────────────────────────────────────────────
    def send(self, command: Command) -> int:
        return self._udp.send(command)

    def subscribe_state(self, callback: StateCallback) -> None:
        self._udp.subscribe_state(callback)

    def subscribe_outcome(self, callback: OutcomeCallback) -> None:
        self._udp.subscribe_outcome(callback)

    def subscribe_controller_change(self, callback: ControllerCallback) -> None:
        self._udp.subscribe_controller_change(callback)
