import base64
import io
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_OPENAI_AUDIO_FORMATS = {"wav", "mp3"}


def detect_audio_format(filename: str, content_type: Optional[str] = None) -> str:
    """Detect audio format from filename and content type. Returns a
    short token suitable for OpenAI's `input_audio.format` (e.g. "wav",
    "mp3") or an empty string if unknown.
    """
    name_map = {
        "wav": "wav", "wave": "wav", "x-wav": "wav",
        "mp3": "mp3", "mpeg": "mp3",
        "m4a": "m4a", "mp4": "m4a", "aac": "aac", "x-aac": "aac",
        "flac": "flac", "x-flac": "flac",
        "ogg": "ogg", "oga": "ogg",
        "webm": "webm",
    }
    mime_map = {
        "audio/wav": "wav", "audio/wave": "wav", "audio/x-wav": "wav",
        "audio/mpeg": "mp3", "audio/mp3": "mp3",
        "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/m4a": "m4a",
        "audio/aac": "aac", "audio/x-aac": "aac",
        "audio/flac": "flac", "audio/x-flac": "flac",
        "audio/ogg": "ogg",
        "audio/webm": "webm",
    }
    if content_type:
        ct = str(content_type).split(";", 1)[0].strip().lower()
        if ct in mime_map:
            return mime_map[ct]
    if filename:
        ext = os.path.splitext(str(filename).strip().lower())[1].lstrip(".")
        if ext in name_map:
            return name_map[ext]
    return "wav"


def _convert_to_wav_with_pyav(audio_bytes: bytes, src_format: str) -> Optional[bytes]:
    """Convert any audio container/codec to a 16-bit PCM mono WAV using PyAV.
    PyAV is bundled with faster-whisper, so it's already installed in the
    runtime. Returns None if conversion fails or PyAV isn't available.
    """
    try:
        import av
        import numpy as np
    except ImportError:
        return None

    try:
        src_buf = io.BytesIO(audio_bytes)
        with av.open(src_buf, mode="r", metadata_errors="ignore") as container:
            if not container.streams.audio:
                return None
            stream = container.streams.audio[0]
            rate = 16000
            channels = 1
            resampler = av.audio.resampler.AudioResampler(
                format="s16",
                layout="mono",
                rate=rate,
            )
            frames_out = []
            for frame in container.decode(stream):
                frame = resampler.resample(frame)
                if isinstance(frame, list):
                    for f in frame:
                        if f is not None:
                            frames_out.append(f.to_ndarray())
                elif frame is not None:
                    frames_out.append(frame.to_ndarray())
            if not frames_out:
                return None
            pcm = np.concatenate(frames_out, axis=0)
            if pcm.size == 0:
                logger.debug("PyAV audio conversion produced 0 PCM samples")
                return None
            # pcm is int16 mono (n, 1) or (n,). Ensure 1D.
            if pcm.ndim > 1:
                pcm = pcm.reshape(-1)
        wav = _wrap_wav_bytes(pcm.tobytes(), sample_rate=rate, channels=channels, sample_width=2)
        dur = pcm.size / float(rate * channels * 2)
        logger.info(
            f"audio prepare (pyav): src='{src_format}' out='wav' "
            f"bytes={len(wav)} dur={dur:.2f}s rate={rate} ch={channels}"
        )
        return wav
    except Exception as e:
        logger.debug(f"PyAV audio conversion failed: {e}")
        return None


def _convert_to_wav_with_ffmpeg(audio_bytes: bytes, src_format: str) -> Optional[bytes]:
    """Fallback: shell out to system ffmpeg to transcode any audio to 16kHz
    mono 16-bit PCM wav. Returns None if ffmpeg is missing or fails.
    """
    import subprocess
    import tempfile

    try:
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as src:
            src.write(audio_bytes)
            src_path = src.name
        dst_path = src_path + ".wav"
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel", "error",
                    "-i", src_path,
                    "-ar", "16000",
                    "-ac", "1",
                    "-f", "wav",
                    dst_path,
                ],
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="replace")[:200]
                logger.debug(f"ffmpeg transcode failed for '{src_format}': {err}")
                return None
            with open(dst_path, "rb") as f:
                wav = f.read()
            dur = (len(wav) - 44) / 32000.0
            logger.info(
                f"audio prepare (ffmpeg): src='{src_format}' out='wav' "
                f"bytes={len(wav)} dur={dur:.2f}s rate=16000 ch=1"
            )
            return wav
        finally:
            for p in (src_path, dst_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    except FileNotFoundError:
        logger.debug("ffmpeg binary not found in PATH")
        return None
    except subprocess.TimeoutExpired:
        logger.debug(f"ffmpeg transcode timed out for '{src_format}'")
        return None
    except Exception as e:
        logger.debug(f"ffmpeg transcode raised: {e}")
        return None


def _wrap_wav_bytes(pcm_bytes: bytes, sample_rate: int, channels: int, sample_width: int) -> bytes:
    """Wrap raw PCM bytes in a minimal RIFF/WAVE container."""
    import struct
    byte_rate = sample_rate * channels * sample_width
    block_align = channels * sample_width
    data_size = len(pcm_bytes)
    fmt_chunk = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,             # fmt chunk size
        1,              # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        sample_width * 8,
    )
    data_chunk = struct.pack("<4sI", b"data", data_size) + pcm_bytes
    riff = struct.pack("<4sI", b"RIFF", 4 + len(fmt_chunk) + len(data_chunk)) + b"WAVE"
    return riff + fmt_chunk + data_chunk


def prepare_audio_for_provider(
    audio_bytes: bytes,
    filename: str = "",
    content_type: Optional[str] = None,
) -> Tuple[bytes, str]:
    """Prepare audio data for an OpenAI-compatible `input_audio` content part.

    OpenAI's chat completions API only accepts `wav` or `mp3` as the audio
    format. Browsers record in `webm/opus` so we transcode to wav using PyAV
    (which is bundled with faster-whisper). Returns (data, format) where
    `data` is raw bytes (NOT base64-encoded) and `format` is one of
    "wav" or "mp3". If the source is already wav/mp3, returns it unchanged.
    """
    fmt = detect_audio_format(filename, content_type)
    if fmt in _OPENAI_AUDIO_FORMATS:
        logger.info(
            f"audio prepare: src='{fmt}' out='{fmt}' "
            f"bytes={len(audio_bytes)} (passthrough)"
        )
        return audio_bytes, fmt
    # Need to convert. Try PyAV first, then ffmpeg.
    converted = _convert_to_wav_with_pyav(audio_bytes, fmt)
    if converted is not None:
        return converted, "wav"
    converted = _convert_to_wav_with_ffmpeg(audio_bytes, fmt)
    if converted is not None:
        return converted, "wav"
    # Both backends failed. Do NOT pass the source bytes through — the
    # upstream provider will reject them ("invalid audio format" for any
    # non-wav/mp3 source). Signal the upper layer so it doesn't try to embed
    # raw bytes.
    logger.warning(
        f"Could not transcode audio from '{fmt}' to wav "
        "(tried PyAV and ffmpeg). Returning None."
    )
    return None, None


def audio_to_data_url(
    audio_bytes: bytes,
    filename: str = "",
    content_type: Optional[str] = None,
) -> str:
    """Convert audio bytes to a base64 data URL string."""
    b64 = base64.b64encode(audio_bytes).decode("ascii")
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if not ct:
        ext = os.path.splitext(filename or "")[1].lstrip(".").lower()
        ct = {
            "wav": "audio/wav", "mp3": "audio/mpeg", "mpeg": "audio/mpeg",
            "m4a": "audio/m4a", "mp4": "audio/mp4", "aac": "audio/aac",
            "flac": "audio/flac", "ogg": "audio/ogg", "webm": "audio/webm",
        }.get(ext, "audio/wav")
    return f"data:{ct};base64,{b64}"
