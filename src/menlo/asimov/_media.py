"""Camera frames, microphone audio and speaker output, typed.

The robot's edge owns the sensors; the SDK owns the shape a script sees. A transport
that carries a stream delivers it through ``subscribe_frames`` / ``subscribe_audio`` and
accepts ``play_audio``; one that does not raises :class:`UnsupportedError` from
:meth:`Camera.latest` and friends with the reason in the message. Check
``robot.has("camera")`` first when a script should degrade instead of fail.
"""

from __future__ import annotations

import contextlib
import io
import math
import threading
import time
import wave
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from menlo.asimov._errors import UnsupportedError, WaitTimeoutError

if TYPE_CHECKING:
    import os

    from menlo.asimov.transport.base import Transport


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
    """One complete camera frame. ``data`` is the raw or encoded bytes as Asimov Edge sent them;
    ``stride_bytes`` is 0 for encoded frames. :meth:`to_numpy` for pixels, :meth:`to_jpeg`
    for bytes to hand on; anything else, decode with your imaging library of choice."""

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

    def to_jpeg(self, quality: int = 85) -> bytes:
        """This frame as JPEG bytes, what a vision model or an HTTP upload wants. A frame
        Asimov Edge already sent as JPEG is returned as it came; ``rgb8``, ``bgr8`` and
        ``gray8`` are encoded with Pillow, which the SDK does not depend on (``ImportError``
        naming it when absent). ``ValueError`` for an encoding that is not pixels (h264)."""
        if not 1 <= quality <= 100:
            raise ValueError(f"quality is a JPEG quality from 1 to 100, got {quality!r}")
        if self.encoding == "jpeg":
            return self.data
        if self.encoding == "gray8":
            row = self.stride_bytes or self.width
            pixels = b"".join(self.data[y * row : y * row + self.width] for y in range(self.height))
            mode: str = "L"
        else:
            pixels, mode = _rgb_bytes(self), "RGB"  # ValueError for h264/yuv420/unknown
        image = _pillow().frombytes(mode, (self.width, self.height), pixels)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=quality)
        return out.getvalue()


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

    def __init__(self, capability: str, transport: Transport | Callable[[], Transport]) -> None:
        self._capability = capability
        # A Robot bound to a ConnectionConfig swaps transports between connects, so the
        # stream asks for the current one each time instead of holding a reference.
        self._get_tx: Callable[[], Transport] = (
            transport if callable(transport) else (lambda: transport)
        )
        self._latest: T | None = None
        self._count = 0
        self._cv = threading.Condition()
        self._subscribers: list[Callable[[T], None]] = []
        self._attached = False

    @property
    def _tx(self) -> Transport:
        return self._get_tx()

    def _rebind(self) -> None:
        """The Robot switched transports: attach again on first use, forget the old items."""
        with self._cv:
            self._attached = False
        self._reset()

    def _reset(self) -> None:
        """A new session begins: forget the items the previous one delivered. The
        subscription to the transport stays, so a reopen does not attach twice."""
        with self._cv:
            self._latest = None

    def _require(self) -> None:
        if self._capability not in self._tx.capabilities:
            raise UnsupportedError(self._capability, self._tx.kind)

    def _attach(self, tx: Transport) -> None:
        raise NotImplementedError

    def _sink(self, tx: Transport) -> Callable[[T], None]:
        """The callback handed to ``tx``: it delivers only while ``tx`` is still the Robot's
        transport, so a transport this stream has left cannot feed the next session."""

        def deliver(item: T) -> None:
            if self._get_tx() is tx:
                self._on_item(item)

        return deliver

    def _ensure(self) -> None:
        self._require()
        tx = self._tx
        with self._cv:  # two first callers must not both attach: every item would arrive twice
            if self._attached:
                return
            self._attach(tx)
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
        pass without one: a stream that has gone quiet is a fact, not an idle loop."""
        self._ensure()
        seen = self._count
        while True:
            self._ensure()  # a generator held across a reconnect attaches to the new transport
            item = self._wait_past(seen, timeout)
            if item is None:
                raise WaitTimeoutError(f"no {self._capability} data for {timeout:.1f}s", last=None)
            seen = self._count
            yield item


class Camera(_Stream[Frame]):
    """``robot.camera``: the robot's camera as :class:`Frame` objects.

    Latest-wins: a slow consumer sees the newest frame, not a backlog. For audio, which
    must not skip, see :class:`Microphone`.
    """

    def __init__(
        self,
        capability: str,
        transport: Transport | Callable[[], Transport],
        microphone: Microphone | None = None,
    ) -> None:
        super().__init__(capability, transport)
        self._mic = microphone  # only capture_clip() needs it

    def _attach(self, tx: Transport) -> None:
        tx.subscribe_frames(self._sink(tx))

    def frames(self, *, timeout: float = 5.0) -> Iterator[Frame]:
        """Frames as they arrive; see :meth:`_Stream.stream`."""
        return self.stream(timeout=timeout)

    def photo(self, *, timeout: float = 5.0) -> Frame:
        """ONE fresh frame: the next one to arrive, never a cached sample from before the
        call. Raises :class:`WaitTimeoutError` when the camera says nothing for ``timeout``
        seconds, and :class:`UnsupportedError` when this transport carries no video."""
        self._ensure()
        frame = self._wait_past(self._count, timeout)
        if frame is None:
            raise WaitTimeoutError(f"no camera frame for {timeout:.1f}s", last=None)
        return frame

    def capture_clip(self, seconds: float, *, audio: bool = True) -> Clip:
        """Record ``seconds`` of video (and, by default, microphone audio) into a
        :class:`Clip` held in memory.

        Blocks for ``seconds``. With ``audio=True`` on a transport that carries no
        microphone this raises :class:`UnsupportedError` rather than return a silent clip;
        pass ``audio=False`` when a video-only room is expected. Raises
        :class:`WaitTimeoutError` when not one frame arrived in that time: an empty clip
        is a dead camera, not a short recording.
        """
        if not (math.isfinite(seconds) and seconds > 0):
            raise ValueError(f"seconds must be a positive finite number, got {seconds!r}")
        self._ensure()
        mic = self._mic if audio else None
        if audio and (mic is None or "microphone" not in self._tx.capabilities):
            raise UnsupportedError("microphone", self._tx.kind)
        frames: list[Frame] = []
        chunks: list[AudioChunk] = []
        if mic is not None:
            mic._ensure()
            mic._subscribers.append(chunks.append)
        self._subscribers.append(frames.append)
        started_at = time.time()
        try:
            time.sleep(seconds)
        finally:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(frames.append)
            if mic is not None:
                with contextlib.suppress(ValueError):
                    mic._subscribers.remove(chunks.append)
        if not frames:
            raise WaitTimeoutError(
                f"the camera sent nothing during the {seconds:.1f}s clip", last=None
            )
        return Clip(frames=tuple(frames), audio=tuple(chunks), started_at=started_at)


class Microphone(_Stream[AudioChunk]):
    """``robot.microphone``: the robot's microphone as :class:`AudioChunk` objects.

    Unlike frames, audio must not skip: :meth:`chunks` hands out every chunk in order from a
    bounded queue and counts what a slow consumer lost in :attr:`dropped`."""

    QUEUE = 256  # chunks (2.5 s of 10 ms audio)

    def __init__(self, capability: str, transport: Transport | Callable[[], Transport]) -> None:
        super().__init__(capability, transport)
        self._queue: deque[AudioChunk] = deque(maxlen=self.QUEUE)
        self.dropped = 0

    def _attach(self, tx: Transport) -> None:
        tx.subscribe_audio(self._sink(tx))

    def _reset(self) -> None:
        super()._reset()
        with self._cv:  # the previous session's audio must not replay into the next one
            self._queue.clear()
            self.dropped = 0

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
            self._ensure()  # a generator held across a reconnect attaches to the new transport
            with self._cv:
                if not self._cv.wait_for(lambda: bool(self._queue), timeout):
                    raise WaitTimeoutError(f"no microphone audio for {timeout:.1f}s", last=None)
                item = self._queue.popleft()
            yield item


class Speaker:
    """``robot.speaker``: play audio on the robot."""

    def __init__(self, transport: Transport | Callable[[], Transport]) -> None:
        self._get_tx: Callable[[], Transport] = (
            transport if callable(transport) else (lambda: transport)
        )

    @property
    def _tx(self) -> Transport:
        return self._get_tx()

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


@dataclass(frozen=True, slots=True)
class Clip:
    """What :meth:`Camera.capture_clip` recorded: the frames and the audio blocks, in
    arrival order, plus the wall clock the recording started at.

    Everything here is in memory and lossless: the frames are the pixels the transport
    delivered. Exporting to JPEG or MP4 needs an imaging library the SDK does not depend
    on (``Pillow``, ``opencv-python``); :meth:`save_wav` and :meth:`frames_as_numpy` are
    the exports that need nothing beyond the standard library and numpy.
    """

    frames: tuple[Frame, ...]
    audio: tuple[AudioChunk, ...]
    started_at: float  # time.time() when the recording began

    @property
    def duration_s(self) -> float:
        """Seconds of audio when there is any, else the span the frames cover."""
        if self.audio:
            return sum(c.duration_s for c in self.audio)
        if len(self.frames) < 2:
            return 0.0
        return self.frames[-1].received_at - self.frames[0].received_at

    @property
    def fps(self) -> float:
        """Measured frame rate over the clip; 0.0 when it is too short to measure."""
        span = (
            self.frames[-1].received_at - self.frames[0].received_at
            if len(self.frames) > 1
            else 0.0
        )
        return (len(self.frames) - 1) / span if span > 0 else 0.0

    def save_wav(self, path: str | os.PathLike[str]) -> Path:
        """Write the audio to a RIFF/WAVE file (stdlib ``wave``). Raises ``ValueError``
        when the clip has no audio, or carries anything but ``pcm_s16le``: a WAV file of
        opus frames would be silence-shaped noise."""
        if not self.audio:
            raise ValueError("this clip has no audio; capture it with audio=True")
        bad = {c.encoding for c in self.audio} - {"pcm_s16le"}
        if bad:
            raise ValueError(f"save_wav writes pcm_s16le; this clip carries {sorted(bad)}")
        rates = {(c.sample_rate_hz, c.channels) for c in self.audio}
        if len(rates) != 1:
            raise ValueError(f"the clip's audio changes format mid-way: {sorted(rates)}")
        rate, channels = rates.pop()
        out = Path(path)
        with wave.open(str(out), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(2)  # pcm_s16le
            wav.setframerate(rate)
            wav.writeframes(b"".join(c.data for c in self.audio))
        return out

    def frames_as_numpy(self) -> Any:
        """The clip as one ``ndarray`` of shape (frames, height, width, channels). Every
        frame must be a raw pixel array of the same shape; see :meth:`Frame.to_numpy`.
        Needs ``numpy`` installed."""
        if not self.frames:
            raise ValueError("this clip has no frames")
        np = _numpy()
        return np.stack([f.to_numpy() for f in self.frames])

    def save_frames(
        self, directory: str | os.PathLike[str], *, prefix: str = "frame", quality: int = 90
    ) -> list[Path]:
        """Write every frame as a JPEG into ``directory``. Needs ``Pillow``; raises
        ``ImportError`` naming it when it is absent. The SDK takes no imaging dependency."""
        image = _pillow()
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for i, frame in enumerate(self.frames):
            img = image.frombytes("RGB", (frame.width, frame.height), bytes(_rgb_bytes(frame)))
            target = out / f"{prefix}_{i:05d}.jpg"
            img.save(target, quality=quality)
            written.append(target)
        return written

    def save_mp4(self, path: str | os.PathLike[str], *, fps: float | None = None) -> Path:
        """Write the video to an MP4. Needs ``opencv-python`` and ``numpy``; raises
        ``ImportError`` naming them when either is absent. Audio is not muxed in; pair it
        with :meth:`save_wav`."""
        cv2 = _cv2()
        np = _numpy()
        if not self.frames:
            raise ValueError("this clip has no frames")
        rate = fps if fps is not None else (self.fps or 30.0)
        first = self.frames[0]
        out = Path(path)
        writer = cv2.VideoWriter(
            str(out), cv2.VideoWriter_fourcc(*"mp4v"), rate, (first.width, first.height)
        )
        try:
            for frame in self.frames:
                rgb = np.frombuffer(_rgb_bytes(frame), dtype=np.uint8).reshape(
                    frame.height, frame.width, 3
                )
                writer.write(rgb[:, :, ::-1])  # OpenCV writes BGR
        finally:
            writer.release()
        return out


def _rgb_bytes(frame: Frame) -> bytes:
    """A frame's pixels as tightly packed RGB. Raises ``ValueError`` for an encoding that
    is not a raw RGB array: an encoded frame has to be decoded first."""
    if frame.encoding not in ("rgb8", "bgr8"):
        raise ValueError(
            f"{frame.encoding} frames are not raw RGB; decode them with your imaging library"
        )
    row = frame.width * 3
    if frame.stride_bytes and frame.stride_bytes != row:
        data = b"".join(
            frame.data[y * frame.stride_bytes : y * frame.stride_bytes + row]
            for y in range(frame.height)
        )
    else:
        data = frame.data[: row * frame.height]
    if frame.encoding != "bgr8":
        return data
    # Swap R and B *within each pixel*. Reversing the whole buffer (`data[::-1]`) only
    # looks right on a single pixel: it also reverses pixel and row order, so the image
    # comes out mirrored and upside down.
    buf = bytearray(data)
    buf[0::3], buf[2::3] = buf[2::3], buf[0::3]
    return bytes(buf)


def _pillow() -> Any:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - environment
        raise ImportError(
            "JPEG export needs Pillow, which the SDK does not depend on: pip install Pillow"
        ) from exc
    return Image


def _cv2() -> Any:
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - environment
        raise ImportError(
            "MP4 export needs OpenCV, which the SDK does not depend on: pip install opencv-python"
        ) from exc
    return cv2
