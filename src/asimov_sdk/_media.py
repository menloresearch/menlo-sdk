"""Camera frames, microphone audio and speaker output, typed.

The robot's edge owns the sensors; the SDK owns the shape a script sees. A transport
that carries a stream delivers it through ``subscribe_frames`` / ``subscribe_audio`` and
accepts ``play_audio``; one that does not raises :class:`UnsupportedError` from
:meth:`Camera.latest` and friends with the reason in the message. Check
``robot.has("camera")`` first when a script should degrade instead of fail.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from asimov_sdk._errors import UnsupportedError, WaitTimeoutError

if TYPE_CHECKING:
    from asimov_sdk.transport.base import Transport


def _numpy() -> Any:
    try:
        import numpy
    except ImportError as exc:  # pragma: no cover - environment
        raise ImportError("to_numpy() needs numpy: pip install numpy") from exc
    return numpy


ImageEncoding = Literal["rgb8", "bgr8", "gray8", "yuv420", "jpeg", "h264", "unknown"]
AudioEncoding = Literal["pcm_s16le", "pcm_f32le", "opus", "unknown"]


@dataclass(frozen=True, slots=True)
class Frame:
    """One complete camera frame. ``data`` is the raw or encoded bytes as the edge sent them;
    ``stride_bytes`` is 0 for encoded frames. Decode with your imaging library of choice."""

    width: int
    height: int
    encoding: ImageEncoding
    data: bytes
    stride_bytes: int = 0
    key_frame: bool = True
    frame_id: str = "camera"
    timestamp_ns: int = 0  # edge clock at capture; 0 when the transport did not carry one
    sequence: int = 0

    received_at: float = field(default_factory=time.monotonic)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def age_s(self) -> float:
        return time.monotonic() - self.received_at

    def to_numpy(self) -> Any:
        """Raw encodings as an ``ndarray`` of shape (height, width[, channels]), zero-copy where
        the stride allows. Encoded frames (jpeg, h264) raise ``ValueError``: decode them with
        your imaging library. Needs ``numpy`` installed."""
        channels = {"rgb8": 3, "bgr8": 3, "gray8": 1, "yuv420": None}.get(self.encoding)
        if channels is None:
            raise ValueError(f"{self.encoding} frames are not a pixel array; decode them first")
        np = _numpy()
        row = self.stride_bytes or self.width * channels
        arr = np.frombuffer(self.data, dtype=np.uint8)[: row * self.height].reshape(
            self.height, row
        )
        arr = arr[:, : self.width * channels]
        return arr.reshape(self.height, self.width, channels) if channels > 1 else arr


@dataclass(frozen=True, slots=True)
class AudioChunk:
    """One block of audio, microphone in or speaker out."""

    sample_rate_hz: int
    channels: int
    samples_per_channel: int
    encoding: AudioEncoding
    data: bytes
    stream_id: str = "microphone"
    timestamp_ns: int = 0
    sequence: int = 0

    received_at: float = field(default_factory=time.monotonic)

    @property
    def duration_s(self) -> float:
        return self.samples_per_channel / self.sample_rate_hz if self.sample_rate_hz else 0.0

    def to_numpy(self) -> Any:
        """PCM as an ``ndarray`` of shape (samples, channels); int16 or float32 by encoding.
        Encoded audio (opus) raises ``ValueError``. Needs ``numpy`` installed."""
        dtype = {"pcm_s16le": "<i2", "pcm_f32le": "<f4"}.get(self.encoding)
        if dtype is None:
            raise ValueError(f"{self.encoding} audio is not a sample array; decode it first")
        np = _numpy()
        return np.frombuffer(self.data, dtype=dtype).reshape(-1, self.channels)


class _Stream[T]:
    """Latest-value store with a condition variable; the base of Camera and Microphone."""

    def __init__(self, capability: str, transport: Transport) -> None:
        self._capability = capability
        self._tx = transport
        self._latest: T | None = None
        self._count = 0
        self._cv = threading.Condition()
        self._subscribers: list[Callable[[T], None]] = []
        self._attached = False

    def _require(self) -> None:
        if self._capability not in self._tx.capabilities:
            raise UnsupportedError(self._capability, self._tx.kind)

    def _attach(self) -> None:
        raise NotImplementedError

    def _ensure(self) -> None:
        self._require()
        if not self._attached:
            self._attach()
            self._attached = True

    def _on_item(self, item: T) -> None:
        with self._cv:
            self._latest = item
            self._count += 1
            self._cv.notify_all()
        for cb in tuple(self._subscribers):
            cb(item)

    def latest(self) -> T | None:
        """The most recent item, or ``None`` when nothing has arrived. Never blocks."""
        self._ensure()
        return self._latest

    def subscribe(self, callback: Callable[[T], None]) -> None:
        """Call ``callback`` on the transport's reader thread for every item. Keep it short."""
        self._ensure()
        self._subscribers.append(callback)

    def _wait_past(self, seen: int, timeout: float) -> T | None:
        """Block until more than ``seen`` items have arrived; the newest, or None on timeout."""
        with self._cv:
            if not self._cv.wait_for(lambda: self._count > seen, timeout):
                return None
            return self._latest

    def stream(self, *, timeout: float = 5.0) -> Iterator[T]:
        """Yield items as they arrive. Raises :class:`WaitTimeoutError` when ``timeout`` seconds
        pass without one — a stream that has gone quiet is a fact, not an idle loop."""
        self._ensure()
        seen = self._count
        while True:
            item = self._wait_past(seen, timeout)
            if item is None:
                raise WaitTimeoutError(f"no {self._capability} data for {timeout:.1f}s", last=None)
            seen = self._count
            yield item


class Camera(_Stream[Frame]):
    """``robot.camera``: the robot's camera as :class:`Frame` objects."""

    def _attach(self) -> None:
        self._tx.subscribe_frames(self._on_item)

    def frames(self, *, timeout: float = 5.0) -> Iterator[Frame]:
        """Frames as they arrive; see :meth:`_Stream.stream`."""
        return self.stream(timeout=timeout)


class Microphone(_Stream[AudioChunk]):
    """``robot.microphone``: the robot's microphone as :class:`AudioChunk` objects.

    Unlike frames, audio must not skip: :meth:`chunks` hands out every chunk in order from a
    bounded queue and counts what a slow consumer lost in :attr:`dropped`."""

    QUEUE = 256  # chunks (2.5 s of 10 ms audio)

    def __init__(self, capability: str, transport: Transport) -> None:
        super().__init__(capability, transport)
        self._queue: deque[AudioChunk] = deque(maxlen=self.QUEUE)
        self.dropped = 0

    def _attach(self) -> None:
        self._tx.subscribe_audio(self._on_item)

    def _on_item(self, item: AudioChunk) -> None:
        with self._cv:
            if len(self._queue) == self._queue.maxlen:
                self.dropped += 1
            self._queue.append(item)
        super()._on_item(item)

    def chunks(self, *, timeout: float = 5.0) -> Iterator[AudioChunk]:
        """Every chunk, in order. Raises :class:`WaitTimeoutError` after ``timeout`` seconds
        without audio."""
        self._ensure()
        while True:
            with self._cv:
                if not self._cv.wait_for(lambda: bool(self._queue), timeout):
                    raise WaitTimeoutError(f"no microphone audio for {timeout:.1f}s", last=None)
                item = self._queue.popleft()
            yield item


class Speaker:
    """``robot.speaker``: play audio on the robot."""

    def __init__(self, transport: Transport) -> None:
        self._tx = transport

    def play(self, chunk: AudioChunk) -> None:
        """Send one chunk to the robot's speaker. Raises :class:`UnsupportedError` when this
        transport does not carry audio to the robot."""
        if "speaker" not in self._tx.capabilities:
            raise UnsupportedError("speaker", self._tx.kind)
        self._tx.play_audio(chunk)

    def play_pcm(
        self, pcm_s16le: bytes, *, sample_rate_hz: int = 16_000, channels: int = 1
    ) -> None:
        """Convenience for raw 16-bit little-endian PCM."""
        frame_bytes = 2 * channels
        self.play(
            AudioChunk(
                sample_rate_hz=sample_rate_hz,
                channels=channels,
                samples_per_channel=len(pcm_s16le) // frame_bytes,
                encoding="pcm_s16le",
                data=pcm_s16le,
                stream_id="speaker",
                timestamp_ns=time.time_ns(),
            )
        )
