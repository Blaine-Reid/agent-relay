"""Sogni STT client.

Verified live against the real local sogni-voice instance this session:

  POST /transcribe   (Hapi, multipart/form-data)
    fields: file (required), engine ("qwen3" — Nova's primary STT; Parakeet
            is the "auto"-detect default but has no way to force a language
            and mis-transcribes English as Cyrillic, per sogni-voice's own
            .env comments — always request qwen3 explicitly), language
    auth:   Authorization: Bearer <AUTH_API_KEY>  (or X-API-Key header)
    response: {"success": true, "transcript": "...", "engine", "language", ...}

The G2 mic delivers raw PCM (16kHz s16le mono, no container) via
`event.audioEvent.audioPcm` — wrapped into a WAV file here since the server
validates the upload's file extension/header bytes.
"""

from __future__ import annotations

import io
import wave

import aiohttp


def _pcm_to_wav(pcm: bytes, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)  # s16le
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buf.getvalue()


class SogniSttClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        path: str = "/transcribe",
        engine: str = "qwen3",
        language: str = "en",
    ):
        self._url = base_url.rstrip("/") + path
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._engine = engine
        self._language = language

    async def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        wav_bytes = _pcm_to_wav(pcm, sample_rate)
        form = aiohttp.FormData()
        form.add_field(
            "file", wav_bytes, filename="audio.wav", content_type="audio/wav"
        )
        form.add_field("engine", self._engine)
        form.add_field("language", self._language)
        async with aiohttp.ClientSession() as http:
            async with http.post(self._url, data=form, headers=self._headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
        return data["transcript"]
