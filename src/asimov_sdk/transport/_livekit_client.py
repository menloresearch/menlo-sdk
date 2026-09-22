"""The ONE place ``livekit`` is imported.

``livekit.rtc`` is asyncio; this SDK is threaded. This module owns that mismatch and
nothing else: a dedicated event-loop thread, ``run_coroutine_threadsafe`` in, plain
callbacks out. Everything above it — :class:`~asimov_sdk.transport.livekit.LiveKitTransport`,
:class:`~asimov_sdk.transport.livekit.HybridTransport`, ``Robot`` — sees the
:class:`LiveKitClient` protocol and never an ``rtc`` object, which is why the unit suite
fakes this seam in forty lines and never needs livekit installed.

LiveKit is an EXTRA (``pip install "asimov-sdk[livekit]"``). The import happens inside
:func:`_rtc`, when a room is actually joined — importing the SDK, and
``robot.connect("udp")``, must work with livekit absent.

The room convention, agreed with the edge team:

* data topic ``commands``  -> one bare serialized ``asimov.io.RobotCommand`` per packet
* data track ``state``     <- one bare serialized ``asimov.io.RobotState`` per frame
* the robot publishes its camera as a video track and its microphone as an audio track;
  the SDK publishes one audio track back for the speaker.

No envelope, no framing, no type tag: the topic identifies the type.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any, Protocol

from asimov_sdk._errors import ConnectError, LinkLostError
from asimov_sdk._media import AudioChunk, Frame

log = logging.getLogger("asimov_sdk.transport.livekit")

#: A token, or something that mints a fresh one each time a room is joined. The SDK never
#: holds the LiveKit API secret: the robot's manager mints tokens, the SDK presents them.
TokenProvider = str | Callable[[], str]

#: (payload, user_timestamp) for one data-track frame; the timestamp is None when the
#: publisher did not set one.
DataTrackCallback = Callable[[bytes, int | None], None]
FrameCallback = Callable[[Frame], None]
AudioCallback = Callable[[AudioChunk], None]
TracksCallback = Callable[[frozenset[str]], None]


def _rtc() -> Any:
    """``livekit.rtc``, imported lazily. The LiveKit lane is an extra; this is the only
    import of it in the SDK, and the error names the fix (the ``_pb()`` precedent)."""
    try:
        from livekit import rtc
    except ImportError as exc:  # pragma: no cover - environment, not logic
        raise ConnectError(
            "the LiveKit lane needs the livekit extra, which is not part of the core SDK "
            '(`pip install "asimov-sdk[livekit]"`, or `pip install "livekit>=1.1,<2"`). '
            "The UDP lane (connect('udp')) needs none of it."
        ) from exc
    return rtc


def identity_from_token(token: str) -> str | None:
    """The identity a LiveKit access token claims, or ``None`` when it claims none.

    The identity of a participant is a claim INSIDE the JWT (``sub``); a client cannot
    choose it, and a client-side "identity" argument would be silently ignored by the
    server. So the SDK does not take one — it reads back what the token says, and reports
    that. Decoded, never verified: this is the SDK telling the truth about the token it
    was handed, not a security check. The server is the authority.
    """
    try:
        payload = token.split(".")[1]
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(raw)
        sub = claims.get("sub") if isinstance(claims, dict) else None
    except (IndexError, ValueError, binascii.Error, UnicodeDecodeError):
        return None
    return str(sub) if isinstance(sub, str) and sub else None


class LiveKitClient(Protocol):
    """What the transports need from a room. :class:`_LiveKitClient` is the real one; the
    unit suite substitutes a fake, which is why no test needs a LiveKit server."""

    @property
    def connected(self) -> bool:
        """Is the room joined right now?"""

    @property
    def tracks(self) -> frozenset[str]:
        """Which of ``camera`` / ``microphone`` are SUBSCRIBED on this room. Empty until a
        remote participant publishes one: a capability is claimed from what arrived, never
        from what a room might one day carry."""

    @property
    def identity(self) -> str | None:
        """Who this client joined the room AS, read out of the token it presented.
        ``None`` before a join, and when the token claims no identity."""

    def connect(self) -> None:
        """Join the room. ``ConnectError`` on failure."""

    def close(self) -> None:
        """Leave the room and stop the loop thread. Idempotent; never raises."""

    def wait_for_tracks(self, timeout: float) -> frozenset[str]:
        """Block until the robot's video track is subscribed (audio, if any, follows on its
        own), or ``timeout`` passes. Returns whatever is subscribed by then."""

    def publish_data(self, payload: bytes, *, topic: str) -> None:
        """One reliable data packet. Returns as soon as the packet is queued on the loop —
        never blocks the caller on the network. ``LinkLostError`` when the room is gone."""

    def publish_audio(self, chunk: AudioChunk) -> None:
        """One block of PCM onto the SDK's own audio track, publishing the track on first
        use. Blocks until LiveKit has taken the samples, so chunks keep their order."""

    def on_data_track(self, name: str, callback: DataTrackCallback) -> None:
        """Every frame of the remote data track called ``name``, on the loop thread, in
        publish order. Keep the callback short."""

    def on_video(self, callback: FrameCallback) -> None:
        """Every decoded camera frame, as ``rgb8``."""

    def on_audio(self, callback: AudioCallback) -> None:
        """Every microphone block, as ``pcm_s16le``."""

    def on_tracks(self, callback: TracksCallback) -> None:
        """Called with the new track set whenever it changes, so a transport's advertised
        capabilities follow what the room actually carries."""


class _LiveKitClient:
    """A LiveKit room behind a threaded API. See the module docstring."""

    def __init__(
        self,
        url: str,
        room: str,
        *,
        token: TokenProvider,
        connect_timeout: float = 10.0,
    ) -> None:
        """No ``identity`` argument, by design: the identity is a claim inside the token
        and the server ignores anything a client says about it. Read it back with
        :attr:`identity` once joined."""
        self._url = url
        self._room_name = room
        self._token = token
        self._identity: str | None = None
        self._connect_timeout = connect_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._room: Any = None
        self._source: Any = None  # the published speaker track's AudioSource
        self._source_format: tuple[int, int] | None = None
        self._connected = False
        self._send_error: BaseException | None = None
        self._tasks: set[Future[Any]] = set()
        self._tracks: set[str] = set()
        self._cv = threading.Condition()
        self._data_track_cbs: dict[str, list[DataTrackCallback]] = {}
        self._data_track_readers: dict[str, Future[Any]] = {}  # by track sid
        self._video_cbs: list[FrameCallback] = []
        self._audio_cbs: list[AudioCallback] = []
        self._track_cbs: list[TracksCallback] = []

    # ── what the transports read ─────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tracks(self) -> frozenset[str]:
        with self._cv:
            return frozenset(self._tracks)

    @property
    def identity(self) -> str | None:
        """Who this client joined AS, from the token's ``sub``. ``None`` before a join."""
        return self._identity

    @property
    def endpoint(self) -> str:
        return f"{self._room_name}@{self._url}"

    # ── subscriptions (may be registered before connect) ─────────────────────
    def on_data_track(self, name: str, callback: DataTrackCallback) -> None:
        self._data_track_cbs.setdefault(name, []).append(callback)

    def on_video(self, callback: FrameCallback) -> None:
        self._video_cbs.append(callback)

    def on_audio(self, callback: AudioCallback) -> None:
        self._audio_cbs.append(callback)

    def on_tracks(self, callback: TracksCallback) -> None:
        self._track_cbs.append(callback)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def _resolve_token(self) -> str:
        """The token for THIS join. A callable is called every time, so a caller can hand
        the SDK a provider that mints a fresh short-lived token per reconnect."""
        token = self._token() if callable(self._token) else self._token
        if not token:
            raise ConnectError(
                "a LiveKit access token is required. The SDK never holds the LiveKit API "
                "secret: ask the robot's manager for a token (or pass a callable that "
                "fetches one) as LiveKitConfig(token=...), or use ManagerConfig and let the "
                "SDK ask."
            )
        return token

    def connect(self) -> None:
        if self._loop is not None:
            raise ConnectError("this LiveKit client is already connected")
        rtc = _rtc()  # fail here, with the extras hint, not on the loop thread
        token = self._resolve_token()
        self._identity = identity_from_token(token)
        self._start_loop()
        try:
            self._await(self._join(rtc, token), self._connect_timeout)
        except ConnectError:
            self._stop_loop()
            raise
        except Exception as exc:
            self._stop_loop()
            raise ConnectError(
                f"could not join the LiveKit room {self._room_name!r} at {self._url}: {exc}"
            ) from exc

    def close(self) -> None:
        loop, thread = self._loop, self._thread
        self._connected = False
        if loop is not None and threading.current_thread() is thread:
            # Called ON the loop thread: a state callback (delivered by _pump_data_track)
            # closed the Robot. Blocking here on our own loop would stall until the
            # timeout, and stopping the loop right after would drop the zero-velocity
            # packet Robot.close() just queued and the room.disconnect() with it. Leave
            # asynchronously instead, and let that task stop the loop once it is done.
            self._loop, self._thread = None, None
            self._source = None

            async def leave_then_stop() -> None:
                with contextlib.suppress(Exception):
                    await self._leave()
                loop.stop()

            loop.create_task(leave_then_stop())
            return
        if loop is not None:
            with contextlib.suppress(Exception):
                self._await(self._leave(), 5.0)
        self._stop_loop()
        self._room = None
        self._source = None
        self._source_format = None
        self._send_error = None
        self._identity = None
        self._set_tracks(set())

    def wait_for_tracks(self, timeout: float) -> frozenset[str]:
        # Returns the moment the VIDEO track is up. A robot that publishes a camera is the
        # common shape, and a mic-less robot is a real configuration — making every one of
        # those pay the whole budget at connect would be a tax on the normal case. An audio
        # track that lands after this still attaches and still works; it simply misses the
        # RobotInfo snapshot, which is what `media_timeout` is the knob for.
        with self._cv:
            self._cv.wait_for(lambda: "camera" in self._tracks, timeout)
            return frozenset(self._tracks)

    # ── out ──────────────────────────────────────────────────────────────────
    def publish_data(self, payload: bytes, *, topic: str) -> None:
        room, loop = self._room, self._loop
        if room is None or loop is None or not self._connected:
            raise LinkLostError(f"the LiveKit room {self.endpoint} is not joined")
        failed, self._send_error = self._send_error, None
        if failed is not None:
            raise LinkLostError(f"a data packet to {self.endpoint} failed: {failed!r}")
        # Queued, not awaited: a 10 Hz keepalive must never sit behind a round trip to the
        # SFU. A publish that fails surfaces on the NEXT send, and a room that has gone
        # quiet is caught by Robot's own link timeout.
        future = asyncio.run_coroutine_threadsafe(
            room.local_participant.publish_data(payload, reliable=True, topic=topic), loop
        )
        future.add_done_callback(self._note_publish)

    def _note_publish(self, future: Future[Any]) -> None:
        with contextlib.suppress(Exception):
            exc = future.exception()
            if exc is not None:
                self._send_error = exc
                log.debug("a LiveKit data packet failed", exc_info=exc)

    def publish_audio(self, chunk: AudioChunk) -> None:
        if chunk.encoding != "pcm_s16le":
            raise ValueError(
                f"the LiveKit lane carries pcm_s16le to the speaker, not {chunk.encoding!r}"
            )
        if self._room is None or not self._connected:
            raise LinkLostError(f"the LiveKit room {self.endpoint} is not joined")
        # Awaited on purpose: LiveKit's AudioSource applies the backpressure that keeps
        # playback in order. Data packets are fire-and-forget; audio is not.
        self._await(self._capture(chunk), 15.0)

    # ── the loop thread ──────────────────────────────────────────────────────
    def _start_loop(self) -> None:
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            try:
                loop.run_forever()
            finally:  # a loop stopped from inside itself (close() on this thread) ends here
                with contextlib.suppress(Exception):
                    loop.close()

        thread = threading.Thread(target=run, name="asimov-sdk-livekit", daemon=True)
        thread.start()
        if not ready.wait(5.0):  # pragma: no cover - the interpreter is wedged
            raise ConnectError("the LiveKit event loop thread did not start")
        self._loop, self._thread = loop, thread

    def _stop_loop(self) -> None:
        loop, thread = self._loop, self._thread
        self._loop, self._thread = None, None
        if loop is None:
            return
        with contextlib.suppress(Exception):
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with contextlib.suppress(Exception):
            loop.close()

    def _await(self, coro: Any, timeout: float) -> Any:
        loop = self._loop
        if loop is None:
            raise LinkLostError("the LiveKit client is closed")
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    def _spawn(self, coro: Any) -> None:
        """Run a coroutine on the loop without waiting. Safe from the loop thread too."""
        loop = self._loop
        if loop is None:  # pragma: no cover - closed under us
            return
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        self._tasks.add(future)
        future.add_done_callback(self._tasks.discard)

    # ── the room ─────────────────────────────────────────────────────────────
    async def _join(self, rtc: Any, token: str) -> None:
        room = rtc.Room()
        room.on("track_subscribed", self._on_track_subscribed)
        room.on("track_unsubscribed", self._on_track_unsubscribed)
        # The SFU announces a data track that was published BEFORE we joined too, right
        # after connect, so the robot's state track is found whichever side came first.
        room.on("data_track_published", self._on_data_track_published)
        room.on("data_track_unpublished", self._on_data_track_unpublished)
        room.on("disconnected", self._on_disconnected)
        await room.connect(self._url, token, options=rtc.RoomOptions(auto_subscribe=True))
        self._room = room
        self._connected = True
        log.debug("joined %s as %s", self.endpoint, self._identity or "(no sub in the token)")

    async def _leave(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        self._data_track_readers.clear()
        room, self._room = self._room, None
        if room is not None:
            with contextlib.suppress(Exception):
                await room.disconnect()

    def _on_disconnected(self, *_: Any) -> None:
        self._connected = False
        self._set_tracks(set())

    def _on_track_subscribed(self, track: Any, *_: Any) -> None:
        rtc = _rtc()
        if track.kind == rtc.TrackKind.KIND_VIDEO:
            self._add_track("camera")
            self._spawn(self._pump_video(rtc, track))
        elif track.kind == rtc.TrackKind.KIND_AUDIO:
            self._add_track("microphone")
            self._spawn(self._pump_audio(rtc, track))

    def _on_track_unsubscribed(self, track: Any, *_: Any) -> None:
        rtc = _rtc()
        with self._cv:
            tracks = set(self._tracks)
        tracks.discard("camera" if track.kind == rtc.TrackKind.KIND_VIDEO else "microphone")
        self._set_tracks(tracks)

    def _on_data_track_published(self, track: Any) -> None:
        name = getattr(getattr(track, "info", None), "name", None)
        if not name or name not in self._data_track_cbs:
            return  # not a track anyone here asked for
        sid = getattr(track.info, "sid", name)
        self._on_data_track_unpublished(sid)  # a re-publish replaces its reader
        loop = self._loop
        if loop is None:  # pragma: no cover - closed under us
            return
        future = asyncio.run_coroutine_threadsafe(self._pump_data_track(track, name), loop)
        self._data_track_readers[sid] = future
        self._tasks.add(future)
        future.add_done_callback(self._tasks.discard)

    def _on_data_track_unpublished(self, sid: str, *_: Any) -> None:
        reader = self._data_track_readers.pop(sid, None)
        if reader is not None:
            reader.cancel()

    async def _pump_data_track(self, track: Any, name: str) -> None:
        stream = track.subscribe()
        try:
            async for frame in stream:
                for cb in tuple(self._data_track_cbs.get(name, ())):
                    try:
                        cb(bytes(frame.payload), frame.user_timestamp)
                    except Exception:  # one bad subscriber must not stop the stream
                        log.exception("a %r data-track subscriber raised", name)
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()

    # ── media pumps ──────────────────────────────────────────────────────────
    async def _pump_video(self, rtc: Any, track: Any) -> None:
        stream = rtc.VideoStream(track)
        sequence = 0
        try:
            async for event in stream:
                sequence += 1
                frame = self._to_frame(rtc, event, sequence)
                if frame is not None:
                    self._fan(self._video_cbs, frame)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - the room went away
            log.debug("the video stream ended", exc_info=True)
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()

    @staticmethod
    def _to_frame(rtc: Any, event: Any, sequence: int) -> Frame | None:
        """LiveKit's I420 -> ``rgb8``, so ``Frame.to_numpy()`` works (it refuses yuv420)."""
        frame = event.frame
        try:
            rgb = frame.convert(rtc.VideoBufferType.RGB24)
        except Exception:  # pragma: no cover - an exotic buffer type
            log.debug("could not convert a %s frame to RGB24", frame.type, exc_info=True)
            return None
        return Frame(
            width=frame.width,
            height=frame.height,
            encoding="rgb8",
            data=bytes(rgb.data),
            stride_bytes=frame.width * 3,
            timestamp_ns=int(getattr(event, "timestamp_us", 0)) * 1_000,
            sequence=sequence,
        )

    async def _pump_audio(self, rtc: Any, track: Any) -> None:
        stream = rtc.AudioStream(track)
        sequence = 0
        try:
            async for event in stream:
                sequence += 1
                frame = event.frame
                self._fan(
                    self._audio_cbs,
                    AudioChunk(
                        sample_rate_hz=int(frame.sample_rate),
                        channels=int(frame.num_channels),
                        samples_per_channel=int(frame.samples_per_channel),
                        encoding="pcm_s16le",
                        data=bytes(frame.data),
                        timestamp_ns=int(getattr(event, "timestamp_us", 0)) * 1_000,
                        sequence=sequence,
                    ),
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - the room went away
            log.debug("the audio stream ended", exc_info=True)
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()

    async def _capture(self, chunk: AudioChunk) -> None:
        rtc = _rtc()
        fmt = (chunk.sample_rate_hz, chunk.channels)
        if self._source is None:
            self._source = rtc.AudioSource(chunk.sample_rate_hz, chunk.channels)
            track = rtc.LocalAudioTrack.create_audio_track("asimov-sdk-speaker", self._source)
            await self._room.local_participant.publish_track(
                track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            )
            self._source_format = fmt
        elif self._source_format != fmt:
            raise ValueError(
                f"this session's speaker track is {self._source_format} (rate, channels); "
                f"a chunk of {fmt} cannot ride it. Resample before playing, or reconnect."
            )
        await self._source.capture_frame(
            rtc.AudioFrame(
                data=chunk.data,
                sample_rate=chunk.sample_rate_hz,
                num_channels=chunk.channels,
                samples_per_channel=chunk.samples_per_channel,
            )
        )

    # ── bookkeeping ──────────────────────────────────────────────────────────
    def _add_track(self, name: str) -> None:
        with self._cv:
            tracks = set(self._tracks)
        tracks.add(name)
        self._set_tracks(tracks)

    def _set_tracks(self, tracks: set[str]) -> None:
        with self._cv:
            if tracks == self._tracks:
                return
            self._tracks = tracks
            self._cv.notify_all()
        snapshot = frozenset(tracks)
        for cb in tuple(self._track_cbs):
            try:
                cb(snapshot)
            except Exception:  # pragma: no cover
                log.exception("a track subscriber raised")

    @staticmethod
    def _fan(callbacks: list[Any], item: Any) -> None:
        for cb in tuple(callbacks):
            try:
                cb(item)
            except Exception:  # one bad subscriber must not stop the stream
                log.exception("a media subscriber raised")
