from __future__ import annotations

import asyncio
import io
import json
import subprocess
import urllib.error
import urllib.request
import uuid
import wave
from typing import Any

from fastapi import HTTPException

from .models import AudioChunk
from .profiles import MODEL_PROFILES
from .runtime import status_for


def normalize_audio_to_wav(input_bytes: bytes) -> bytes:
    command = [
        "ffmpeg",
        "-i",
        "pipe:0",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed on the server") from exc

    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail=f"Audio normalization failed: {result.stderr.decode(errors='ignore').strip()}",
        )

    if not result.stdout:
        raise HTTPException(status_code=400, detail="Audio normalization produced empty output")

    return result.stdout


def split_wav_chunks(
    wav_bytes: bytes,
    chunk_seconds: float = 25.0,
    overlap_seconds: float = 0.5,
) -> tuple[list[AudioChunk], int]:
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_reader:
        channels = wav_reader.getnchannels()
        sample_width = wav_reader.getsampwidth()
        sample_rate = wav_reader.getframerate()
        header_frame_count = wav_reader.getnframes()
        raw_frames = wav_reader.readframes(header_frame_count)

    frame_size = channels * sample_width
    frame_count = len(raw_frames) // frame_size
    frames_per_chunk = max(1, int(sample_rate * chunk_seconds))
    overlap_frames = max(0, int(sample_rate * overlap_seconds))
    step_frames = max(1, frames_per_chunk - overlap_frames)
    chunks: list[AudioChunk] = []

    for start_frame in range(0, frame_count, step_frames):
        end_frame = min(frame_count, start_frame + frames_per_chunk)
        start_byte = start_frame * frame_size
        end_byte = end_frame * frame_size
        chunk_frames = raw_frames[start_byte:end_byte]

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(sample_width)
            writer.setframerate(sample_rate)
            writer.writeframes(chunk_frames)
        chunks.append(
            AudioChunk(
                wav_bytes=buffer.getvalue(),
                start_frame=start_frame,
                prefix_skip_seconds=overlap_seconds if start_frame > 0 else 0.0,
            )
        )

        if end_frame >= frame_count:
            break

    return chunks, sample_rate


def to_segment_seconds(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric / 100.0 if numeric > 200 else numeric


def extract_segments(payload: Any) -> list[tuple[float, float, str]]:
    if not isinstance(payload, dict):
        return []

    raw_segments = payload.get("segments")
    if not isinstance(raw_segments, list):
        return []

    segments: list[tuple[float, float, str]] = []
    for segment in raw_segments:
        if not isinstance(segment, dict):
            continue

        text = str(segment.get("text", "")).strip()
        if not text:
            continue

        start_s = to_segment_seconds(segment.get("t0", 0))
        end_s = to_segment_seconds(segment.get("t1", 0))
        if end_s <= start_s:
            continue

        segments.append((start_s, end_s, text))

    return segments


def format_timestamp(seconds: float) -> str:
    whole = max(0, int(seconds))
    mins, secs = divmod(whole, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def extract_chunk_text(
    payload: Any,
    include_timecodes: bool,
    chunk_start_seconds: float,
    prefix_skip_seconds: float,
) -> str:
    segments = extract_segments(payload)
    if segments:
        threshold = max(0.0, prefix_skip_seconds - 0.05)
        kept_segments = [seg for seg in segments if seg[0] >= threshold]
        if not kept_segments:
            kept_segments = segments
        if include_timecodes:
            lines: list[str] = []
            for start_s, end_s, text in kept_segments:
                start = format_timestamp(chunk_start_seconds + start_s)
                end = format_timestamp(chunk_start_seconds + end_s)
                lines.append(f"[{start} - {end}] {text}")
            if lines:
                return "\n".join(lines)
        else:
            text_parts = [text for _, _, text in kept_segments]
            if text_parts:
                return " ".join(text_parts).strip()

    if isinstance(payload, dict):
        text = payload.get("text")
        if isinstance(text, str):
            return text.strip()

    if isinstance(payload, str):
        return payload.strip()

    return ""


def collapse_repetitions(text: str) -> str:
    tokens = text.split()
    if len(tokens) < 20:
        return text.strip()

    cleaned: list[str] = []
    current = ""
    run = 0
    for token in tokens:
        if token == current:
            run += 1
            if run <= 6:
                cleaned.append(token)
        else:
            current = token
            run = 1
            cleaned.append(token)

    return " ".join(cleaned).strip()


async def request_whisper_chunk(chunk_wav: bytes, language: str) -> Any:
    profile = MODEL_PROFILES.get("whisper-large-v3")
    if not profile:
        raise HTTPException(status_code=500, detail="Whisper model profile is not configured")

    if status_for(profile) != "running":
        raise HTTPException(status_code=409, detail="Audio server is not running. Start Whisper server first.")

    data = {
        "task": "transcribe",
        "temperature": "0.0",
    }
    if language in {"fr", "en"}:
        data["language"] = language

    boundary = f"----metallama-{uuid.uuid4().hex}"
    body = bytearray()
    for key, value in data.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(f"{value}\r\n".encode("utf-8"))

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="file"; filename="chunk.wav"\r\n')
    body.extend(b"Content-Type: audio/wav\r\n\r\n")
    body.extend(chunk_wav)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    def _send_request() -> tuple[int, str, str]:
        request = urllib.request.Request(
            url=f"http://127.0.0.1:{profile.port}/inference",
            data=bytes(body),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                payload = response.read().decode("utf-8", errors="ignore")
                return status, content_type, payload
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="ignore")
            raise HTTPException(
                status_code=502,
                detail=f"Whisper inference failed ({exc.code}): {payload[:400]}",
            ) from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"Cannot reach whisper server: {exc}") from exc

    status, content_type, payload = await asyncio.to_thread(_send_request)

    if status >= 400:
        raise HTTPException(status_code=502, detail=f"Whisper inference failed ({status})")

    if "json" in content_type:
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {"text": payload}
    return payload


def ndjson_line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=True) + "\n").encode("utf-8")
