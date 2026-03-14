#!/usr/bin/env python3
"""One-shot real voice call test — Twilio + ElevenLabs + Deepgram.

Flow:
  1. ElevenLabs synthesises the greeting to MP3 bytes.
  2. A minimal HTTP server (this process, background thread) serves the audio
     and the Twilio TwiML document.
  3. An ngrok tunnel exposes the local server to the public internet so
     Twilio can reach it.
  4. Twilio dials TEST_PHONE_NUMBER, plays the ElevenLabs greeting, then
     records up to 10 seconds of response.
  5. When the recording is ready, Twilio POSTs the RecordingUrl to /done.
  6. The script downloads the recording and transcribes it with Deepgram.
  7. The transcript is printed; the call is ended.

Required env vars:
  TWILIO_ACCOUNT_SID      — Twilio account SID
  TWILIO_AUTH_TOKEN       — Twilio auth token
  TWILIO_PHONE_NUMBER     — caller ID (Twilio number in E.164 format)
  TEST_PHONE_NUMBER       — destination (your own phone)
  ELEVENLABS_API_KEY      — ElevenLabs key
  DEEPGRAM_API_KEY        — Deepgram key

Optional:
  ELEVENLABS_VOICE_ID     — overrides default voice (Adam)
  ELEVENLABS_MODEL_ID     — overrides default model (eleven_multilingual_v2)
  TEST_CALL_PORT          — local HTTP server port (default 7654)
  TEST_CALL_TIMEOUT       — seconds to wait for recording callback (default 45)
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

import httpx

# ---------------------------------------------------------------------------
# Load .env / .env.secrets
# ---------------------------------------------------------------------------

def _load_env() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in (".env.secrets", ".env"):
        f = root / name
        if not f.exists():
            continue
        with f.open() as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v


_load_env()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_PORT   = int(os.getenv("TEST_CALL_PORT", "7654"))
_TIMEOUT = int(os.getenv("TEST_CALL_TIMEOUT", "45"))
_VOICE_ID = (os.getenv("ELEVENLABS_VOICE_ID") or "21m00Tcm4TlvDq8ikWAM").strip()  # Rachel (default, works on most plans)
_MODEL_ID = (os.getenv("ELEVENLABS_MODEL_ID") or "eleven_multilingual_v2").strip()

_GREETING = (
    "Hola, soy Aria de KAN Logic, estoy probando el sistema de voz. "
    "¿Me escuchas bien?"
)

# Shared state (written by HTTP handler thread, read by main async thread)
_recording_url: Optional[str] = None
_recording_event = threading.Event()
_audio_bytes: bytes = b""
_public_url: str = ""


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_G = "\033[92m"; _Y = "\033[93m"; _B = "\033[94m"; _C = "\033[96m"; _R = "\033[0m"; _BOLD = "\033[1m"

def _log(symbol: str, colour: str, msg: str) -> None:
    print(f"{colour}{_BOLD}{symbol}{_R}  {msg}", flush=True)

ok    = lambda m: _log("✓", _G, m)
info  = lambda m: _log("→", _Y, m)
step  = lambda n, m: print(f"\n{_BOLD}{_B}[{n}]{_R}  {m}", flush=True)
err   = lambda m: (_log("✗", "\033[91m", m), sys.exit(1))


# ---------------------------------------------------------------------------
# Mini HTTP server (runs in a daemon thread)
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence default access logs
        pass

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/audio":
            # Serve the ElevenLabs MP3
            self._send(200, "audio/mpeg", _audio_bytes)
        elif path == "/twiml":
            # TwiML: play greeting → record 10 s → POST result to /done
            xml = textwrap.dedent(f"""\
                <?xml version="1.0" encoding="UTF-8"?>
                <Response>
                  <Play>{_public_url}/audio</Play>
                  <Record maxLength="10" playBeep="false" action="{_public_url}/done" method="POST"/>
                </Response>
            """).encode()
            self._send(200, "text/xml", xml)
        else:
            # Hangup for any other GET (e.g. Twilio retrying after /done fails)
            self._send(200, "text/xml", b"<?xml version='1.0'?><Response><Hangup/></Response>")

    def do_POST(self) -> None:
        global _recording_url
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode(errors="replace")

        if path == "/done":
            params = dict(urllib.parse.parse_qsl(body))
            rec_url = params.get("RecordingUrl", "")
            call_status = params.get("CallStatus", "")
            info(f"Twilio callback: CallStatus={call_status!r}  RecordingUrl={rec_url!r}")
            if rec_url:
                _recording_url = rec_url
                _recording_event.set()
            # Instruct Twilio to hang up
            self._send(200, "text/xml", b"<?xml version='1.0'?><Response><Hangup/></Response>")
        else:
            self._send(204, "text/plain", b"")


def _start_server() -> None:
    server = HTTPServer(("0.0.0.0", _PORT), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    info(f"Local HTTP server listening on :{_PORT}")


# ---------------------------------------------------------------------------
# ngrok helpers
# ---------------------------------------------------------------------------

def _ngrok_running_url() -> Optional[str]:
    """Return the first https tunnel URL if ngrok is already running."""
    try:
        resp = httpx.get("http://localhost:4040/api/tunnels", timeout=2.0)
        tunnels = resp.json().get("tunnels", [])
        for t in tunnels:
            url = t.get("public_url", "")
            if url.startswith("https"):
                return url
    except Exception:
        pass
    return None


def _start_ngrok(port: int) -> str:
    """Start ngrok in background and return the public https URL."""
    info("Starting ngrok tunnel…")
    proc = subprocess.Popen(
        ["ngrok", "http", str(port), "--log=stdout", "--log-format=json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        time.sleep(0.8)
        url = _ngrok_running_url()
        if url:
            return url

    proc.terminate()
    err("ngrok did not start within 15 s. Install ngrok and try again, or set TWILIO_TWIML_BASE_URL to a live public URL.")


def _get_public_url() -> str:
    """Return a public URL pointing to the local HTTP server."""
    # If there's already a live public base URL (e.g. Railway deployment), use it.
    base = (os.getenv("TWILIO_TWIML_BASE_URL") or "").rstrip("/")
    if base:
        info(f"Using TWILIO_TWIML_BASE_URL={base}")
        return base

    # Check if ngrok is already running on the correct port
    url = _ngrok_running_url()
    if url:
        info(f"Using existing ngrok tunnel: {url}")
        return url

    # Start a fresh ngrok tunnel
    return _start_ngrok(_PORT)


# ---------------------------------------------------------------------------
# ElevenLabs TTS
# ---------------------------------------------------------------------------

async def _synthesise(text: str) -> bytes:
    api_key = (os.getenv("ELEVENLABS_API_KEY") or "").strip()
    if not api_key:
        err("ELEVENLABS_API_KEY not set")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{_VOICE_ID}"
    payload = {
        "text": text,
        "model_id": _MODEL_ID,
        "voice_settings": {
            "stability": float(os.getenv("ELEVENLABS_STABILITY", "0.65")),
            "similarity_boost": float(os.getenv("ELEVENLABS_SIMILARITY", "0.82")),
        },
    }
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            err(f"ElevenLabs error {resp.status_code}: {resp.text[:200]}")
        return resp.content


# ---------------------------------------------------------------------------
# Twilio — dial + hang up
# ---------------------------------------------------------------------------

async def _dial(to: str, twiml_url: str) -> str:
    sid   = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.getenv("TWILIO_AUTH_TOKEN")  or "").strip()
    frm   = (os.getenv("TWILIO_PHONE_NUMBER") or "").strip()
    if not sid or not token or not frm:
        err("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER not set")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            auth=(sid, token),
            data={"To": to, "From": frm, "Url": twiml_url},
        )
        if resp.status_code not in (200, 201):
            err(f"Twilio dial error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return str(data.get("sid", ""))


async def _hangup(call_sid: str) -> None:
    sid   = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.getenv("TWILIO_AUTH_TOKEN")  or "").strip()
    url   = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls/{call_sid}.json"
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(url, auth=(sid, token), data={"Status": "completed"})


# ---------------------------------------------------------------------------
# Deepgram transcription
# ---------------------------------------------------------------------------

async def _download_recording(recording_url: str) -> bytes:
    """Download Twilio recording. Twilio may return 404 briefly; retry."""
    sid   = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.getenv("TWILIO_AUTH_TOKEN")  or "").strip()
    # Append .mp3 so Twilio returns audio/mpeg directly
    mp3_url = recording_url.rstrip("/") + ".mp3"
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for attempt in range(8):
            resp = await client.get(mp3_url, auth=(sid, token))
            if resp.status_code == 200:
                return resp.content
            await asyncio.sleep(2.0)
    err(f"Could not download recording after retries: {mp3_url}")


async def _transcribe(audio: bytes) -> str:
    api_key = (os.getenv("DEEPGRAM_API_KEY") or "").strip()
    if not api_key:
        err("DEEPGRAM_API_KEY not set")
    url = "https://api.deepgram.com/v1/listen"
    params = {"model": "nova-2", "smart_format": "true", "language": "es"}
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/mpeg",
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, params=params, headers=headers, content=audio)
        if resp.status_code != 200:
            err(f"Deepgram error {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
    try:
        return str(data["results"]["channels"][0]["alternatives"][0]["transcript"])
    except (KeyError, IndexError):
        return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    global _audio_bytes, _public_url

    to_number = (os.getenv("TEST_PHONE_NUMBER") or "").strip()
    if not to_number:
        err("TEST_PHONE_NUMBER not set")

    # ------------------------------------------------------------------
    step(1, "Synthesising greeting with ElevenLabs…")
    info(f"Script: {_GREETING!r}")
    _audio_bytes = await _synthesise(_GREETING)
    ok(f"ElevenLabs → {len(_audio_bytes):,} bytes of MP3")

    # ------------------------------------------------------------------
    step(2, "Starting local HTTP server + ngrok tunnel…")
    _start_server()
    _public_url = _get_public_url()
    ok(f"Public URL: {_public_url}")

    twiml_url = f"{_public_url}/twiml"
    info(f"TwiML URL : {twiml_url}")
    info(f"Audio URL : {_public_url}/audio")

    # ------------------------------------------------------------------
    step(3, f"Dialling {to_number} via Twilio…")
    call_sid = await _dial(to_number, twiml_url)
    ok(f"Call SID: {call_sid}")
    info("Waiting for the call to connect and recording to complete…")

    # ------------------------------------------------------------------
    step(4, f"Waiting up to {_TIMEOUT}s for Twilio /done callback…")
    got_callback = _recording_event.wait(timeout=_TIMEOUT)

    if not got_callback or not _recording_url:
        info("No callback received — polling Twilio recordings API as fallback…")
        # Try polling the recordings API
        sid   = (os.getenv("TWILIO_ACCOUNT_SID") or "").strip()
        token = (os.getenv("TWILIO_AUTH_TOKEN")  or "").strip()
        rec_api = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Recordings.json?CallSid={call_sid}"
        deadline = time.monotonic() + 30.0
        async with httpx.AsyncClient(timeout=15.0) as client:
            while time.monotonic() < deadline:
                resp = await client.get(rec_api, auth=(sid, token))
                recs = resp.json().get("recordings", [])
                if recs:
                    _recording_url = (
                        f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
                        f"/Recordings/{recs[0]['sid']}"
                    )
                    ok(f"Found recording via polling: {_recording_url}")
                    break
                await asyncio.sleep(2.0)

    if not _recording_url:
        err("No recording found. The call may not have connected or the callee didn't speak.")

    # ------------------------------------------------------------------
    step(5, "Downloading recording from Twilio…")
    recording_audio = await _download_recording(_recording_url)
    ok(f"Recording downloaded: {len(recording_audio):,} bytes")

    # ------------------------------------------------------------------
    step(6, "Transcribing with Deepgram…")
    transcript = await _transcribe(recording_audio)

    # ------------------------------------------------------------------
    step(7, "Hanging up…")
    await _hangup(call_sid)
    ok("Call ended")

    # ------------------------------------------------------------------
    print(f"\n{'━' * 60}")
    print(f"{_BOLD}{_G}TRANSCRIPT:{_R}")
    print(f"  {transcript if transcript else '(silence / no speech detected)'}")
    print(f"{'━' * 60}\n")

    if not transcript:
        info("Deepgram returned an empty transcript — check recording quality.")
        sys.exit(0)

    ok("Done.")


if __name__ == "__main__":
    asyncio.run(main())
