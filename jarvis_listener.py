from __future__ import annotations

# ============================================================
# PERMISOS macOS — LEER ANTES DE ARRANCAR
# ============================================================
# Si el script no detecta el micrófono:
#   1. Ir a: Ajustes del Sistema → Privacidad y Seguridad → Micrófono
#   2. Activar acceso para "Terminal" y/o "Visual Studio Code"
#   3. Si usas iTerm2 o cualquier terminal alternativa, habilitarla también
#   4. Reiniciar la terminal después de cambiar los permisos
# ============================================================

import asyncio
import audioop
import gc
import logging
import os
import re
import signal
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

from brain.jarvis_ui_state import publish_state
from brain.voice_engine import speak_text
from config.env_loader import is_placeholder as _env_is_placeholder
from config.env_loader import load_environment
from desktop_agent.screen_capture import take_screenshot
from tools.whisper_client import transcribe_audio_bytes

logger = logging.getLogger("kan_core.jarvis_listener")
_SCENE_PROMPT = (
    "Describe de forma breve y natural que aplicaciones tengo abiertas y en que estoy trabajando. "
    "No digas 'veo una imagen'. Habla como asistente contextual: "
    "ejemplo 'Parece que estas analizando el mercado' o "
    "'Veo que tienes VS Code abierto en el archivo de trading'."
)
# Todas las transcripciones fonéticas que Whisper puede generar para "k'an":
# kan / can / k'an (formas base)
# kam / cam  — Whisper confunde n/m al final de sílaba
# khan / kahn / cahn — transliteraciones comunes
# caan / kaan — vocal larga
# kon / com / kom — vocal cambiada (acento fuerte)
# kem / ken — variante vocal central
# qan / qaan — otra transliteración
_WAKE_PREFIXES = (
    "kan", "can", "k'an",
    "kam", "cam",
    "khan", "kahn", "cahn",
    "caan", "kaan",
    "kon", "kom", "com",
    "kem", "ken",
    "qan",
)
_INPUT_MUTE_EVENT = asyncio.Event()
_LAST_TTS_TEXT = ""
_LAST_TTS_TS = 0.0

def _is_placeholder(value: str) -> bool:
    return _env_is_placeholder(value)


load_environment(context="jarvis_listener")


def _normalize_text(text: str) -> str:
    raw = str(text or "").strip().lower()
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    for token in ("'", "\u2019", "`", '"'):
        normalized = normalized.replace(token, "")
    return " ".join(normalized.split())


def _load_wake_aliases() -> tuple[str, ...]:
    raw = str(os.getenv("JARVIS_WAKE_ALIASES") or "").strip()
    if not raw:
        return _WAKE_PREFIXES
    aliases = []
    for token in raw.split(","):
        norm = _normalize_text(token)
        if norm and norm not in aliases:
            aliases.append(norm)
    if not aliases:
        return _WAKE_PREFIXES
    return tuple(aliases)


# Cache calculado una sola vez al importar — evita re-parsear en cada ciclo de escucha.
_WAKE_ALIASES: tuple[str, ...] = _load_wake_aliases()


def _is_wake_word(text: str) -> bool:
    probe = _normalize_text(text)
    if not probe:
        return False
    tokens = probe.split()
    if not tokens:
        return False
    first = tokens[0]
    return any(first.startswith(prefix) for prefix in _WAKE_ALIASES)


def contains_wake_word(text: str, wake_word: str = "k'an") -> bool:
    _ = wake_word
    return _is_wake_word(text)


def _extract_inline_command(wake_text: str) -> str:
    """
    Si el usuario dijo 'k'an <comando>' en una sola frase, extrae el
    comando para evitar una segunda escucha (Fase 2 innecesaria).
    Retorna "" si el wake phrase solo contiene el wake word.
    """
    probe = _normalize_text(wake_text)
    if not probe:
        return ""
    for prefix in _WAKE_ALIASES:
        if probe.startswith(prefix):
            rest = probe[len(prefix):].strip()
            # Quitar relleno inicial sin significado
            for filler in ("por favor", "please", "tu", "oye"):
                if rest.startswith(filler + " "):
                    rest = rest[len(filler):].strip()
            # Mínimo 4 caracteres = comando real, no ruido
            if len(rest) >= 4:
                return rest
    return ""


def is_scene_query_command(text: str) -> bool:
    probe = _normalize_text(text)
    if not probe:
        return False
    patterns = (
        "que estoy haciendo",
        "que hago",
        "what am i doing",
    )
    return any(p in probe for p in patterns)


def _import_speechrecognition():
    try:
        import speech_recognition as sr  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "SpeechRecognition no está instalado o falta backend de micrófono (PyAudio)."
        ) from exc
    return sr


def _list_audio_devices() -> list[dict]:
    """Devuelve lista de dispositivos de entrada disponibles via PyAudio."""
    devices: list[dict] = []
    try:
        import pyaudio  # type: ignore

        pa = pyaudio.PyAudio()
        count = pa.get_device_count()
        for i in range(count):
            info = pa.get_device_info_by_index(i)
            if int(info.get("maxInputChannels", 0)) > 0:
                devices.append(
                    {
                        "index": i,
                        "name": str(info.get("name", "")),
                        "channels": int(info.get("maxInputChannels", 0)),
                        "sample_rate": int(info.get("defaultSampleRate", 0)),
                    }
                )
        pa.terminate()
    except Exception as exc:
        logger.warning("No se pudo enumerar dispositivos de audio: %s", exc)
    return devices


def _validate_microphone() -> int:
    """
    Valida que exista al menos un micrófono de entrada disponible.
    Imprime la lista completa en el log para diagnóstico.
    Retorna el device_index seleccionado.
    Lanza RuntimeError si no hay micrófonos disponibles.
    """
    devices = _list_audio_devices()
    if not devices:
        raise RuntimeError(
            "No se detectó ningún micrófono de entrada.\n"
            "Verifica los permisos de macOS:\n"
            "  Ajustes del Sistema → Privacidad y Seguridad → Micrófono\n"
            "  Activa acceso para Terminal / VS Code y reinicia la terminal."
        )

    logger.info("=== Dispositivos de entrada disponibles ===")
    for d in devices:
        logger.info(
            "  [%d] %s  (canales=%d, sampleRate=%d Hz)",
            d["index"],
            d["name"],
            d["channels"],
            d["sample_rate"],
        )
    logger.info("===========================================")

    device_index_env = str(os.getenv("JARVIS_MICROPHONE_INDEX", "")).strip()
    if device_index_env.isdigit():
        idx = int(device_index_env)
        match = next((d for d in devices if d["index"] == idx), None)
        if match:
            logger.info("Micrófono seleccionado por JARVIS_MICROPHONE_INDEX=%d: %s", idx, match["name"])
            return idx
        logger.warning(
            "JARVIS_MICROPHONE_INDEX=%d no encontrado; usando micrófono por defecto.", idx
        )

    preferred_name = str(os.getenv("JARVIS_MICROPHONE_NAME_CONTAINS", "")).strip().lower()
    if preferred_name:
        by_name = next((d for d in devices if preferred_name in d["name"].lower()), None)
        if by_name:
            logger.info(
                "Micrófono seleccionado por JARVIS_MICROPHONE_NAME_CONTAINS=%r: [%d] %s",
                preferred_name,
                by_name["index"],
                by_name["name"],
            )
            return int(by_name["index"])

    # Heurística estable para macOS: preferir micrófono interno de Mac.
    internal_keywords = ("macbook", "internal", "built-in")
    internal = next(
        (d for d in devices if any(token in d["name"].lower() for token in internal_keywords)),
        None,
    )
    if internal:
        logger.info(
            "Micrófono seleccionado por heurística interna: [%d] %s",
            internal["index"],
            internal["name"],
        )
        return int(internal["index"])

    fallback = devices[0]
    logger.info("Usando primer micrófono disponible: [%d] %s", fallback["index"], fallback["name"])
    return int(fallback["index"])


async def _record_phrase(
    recognizer: Any,
    microphone: Any,
    *,
    timeout: float,
    phrase_time_limit: float,
):
    per_listen_ambient_s = float(os.getenv("JARVIS_PER_LISTEN_AMBIENT_SEC", "0.0"))

    def _capture():
        with microphone as source:
            if per_listen_ambient_s > 0:
                recognizer.adjust_for_ambient_noise(source, duration=per_listen_ambient_s)
            try:
                return recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=phrase_time_limit,
                )
            except Exception as exc:
                if exc.__class__.__name__ == "WaitTimeoutError":
                    raise TimeoutError("wait timeout on microphone listen") from exc
                raise

    return await asyncio.to_thread(_capture)


async def _probe_microphone_stream(microphone: Any) -> int:
    """Prueba mínima de captura para validar permiso y stream de micrófono."""

    def _probe() -> int:
        with microphone as source:
            chunk = int(getattr(source, "CHUNK", 1024))
            data = source.stream.read(chunk)
            if not data:
                return 0
            return int(audioop.rms(data, 2))

    return await asyncio.to_thread(_probe)


async def _calibrate_noise(recognizer: Any, microphone: Any, duration: float) -> None:
    def _calibrate():
        with microphone as source:
            recognizer.adjust_for_ambient_noise(source, duration=duration)

    await asyncio.to_thread(_calibrate)


async def _play_listening_beep() -> None:
    default_sound = "/System/Library/Sounds/Ping.aiff"
    sound_path = str(os.getenv("JARVIS_BEEP_PATH", default_sound)).strip() or default_sound
    if not Path(sound_path).exists():
        logger.debug("beep file not found: %s", sound_path)
        return
    try:
        proc = await asyncio.create_subprocess_exec(
            "afplay",
            sound_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception as exc:
        logger.debug("afplay beep failed: %s", exc)


def _extract_response_text(payload: dict) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    text = str(block["text"]).strip()
                    if text:
                        return text
    return ""


async def _describe_scene_with_vision(
    image_base64: str,
    *,
    mime_type: str = "image/png",
) -> Optional[str]:
    api_key = str(os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        logger.warning("OPENAI_API_KEY missing; cannot run scene vision")
        return None
    model = str(os.getenv("JARVIS_SCENE_VISION_MODEL", "gpt-4o")).strip() or "gpt-4o"
    max_tokens = int(os.getenv("JARVIS_SCENE_MAX_TOKENS", "100"))
    timeout_s = float(os.getenv("JARVIS_SCENE_VISION_TIMEOUT", "2.8"))
    base_url = str(os.getenv("OPENAI_API_BASE") or "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/responses"
    payload = {
        "model": model,
        "max_output_tokens": max_tokens,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _SCENE_PROMPT},
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime_type};base64,{image_base64}",
                    },
                ],
            }
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            text = _extract_response_text(resp.json())
            return text or None
    except Exception as exc:
        logger.warning("scene vision request failed: %s", exc)
        return None
    finally:
        payload = {}
        headers = {}


async def _handle_scene_query() -> str:
    total_budget_s = float(os.getenv("JARVIS_SCENE_TOTAL_TIMEOUT", "4.0"))
    capture_timeout_s = float(os.getenv("JARVIS_SCENE_CAPTURE_TIMEOUT", "1.2"))
    max_width = int(os.getenv("JARVIS_SCENE_SCREENSHOT_MAX_WIDTH", "2560"))
    shot: dict | None = None
    image_base64 = ""
    try:
        shot = await asyncio.wait_for(
            asyncio.to_thread(take_screenshot, max_width),
            timeout=capture_timeout_s,
        )
        image_base64 = str(shot.get("image_base64") or "")
        mime_type = str(shot.get("mime_type") or "image/png")
        if not image_base64:
            return "No pude capturar la pantalla en este momento."
        remaining = max(0.4, total_budget_s - capture_timeout_s)
        text = await asyncio.wait_for(
            _describe_scene_with_vision(image_base64, mime_type=mime_type),
            timeout=remaining,
        )
        return text or "No pude interpretar la escena actual con claridad."
    except asyncio.TimeoutError:
        return "Lo siento, la conexión con mi motor de visión es lenta ahora mismo."
    except Exception as exc:
        logger.warning("scene query failed: %s", exc)
        return "No pude analizar la pantalla en este momento."
    finally:
        if isinstance(shot, dict):
            shot.clear()
        shot = None
        image_base64 = ""
        gc.collect()


async def _speak_and_wait(text: str, *, post_silence_s: float = 0.8) -> None:
    """
    Habla el texto y luego espera `post_silence_s` segundos.
    El silencio post-TTS evita que el siguiente ciclo de escucha
    capture el eco de las bocinas como wake word.
    """
    global _LAST_TTS_TEXT
    global _LAST_TTS_TS
    _INPUT_MUTE_EVENT.set()
    try:
        _LAST_TTS_TEXT = _normalize_text(text)
        _LAST_TTS_TS = time.monotonic()
        await speak_text(text)
        await asyncio.sleep(post_silence_s)
    finally:
        _INPUT_MUTE_EVENT.clear()


def _is_echo_transcript(text: str) -> bool:
    probe = _normalize_text(text)
    if not probe:
        return False
    if not _LAST_TTS_TEXT:
        return False
    age = time.monotonic() - _LAST_TTS_TS
    window_s = float(os.getenv("JARVIS_ECHO_WINDOW_SEC", "2.0"))
    if age > max(0.1, window_s):
        return False
    if probe == _LAST_TTS_TEXT:
        return True
    probe_tokens = set(probe.split())
    tts_tokens = set(_LAST_TTS_TEXT.split())
    if not probe_tokens or not tts_tokens:
        return False
    overlap = len(probe_tokens & tts_tokens)
    ratio = overlap / max(1, min(len(probe_tokens), len(tts_tokens)))
    return ratio >= float(os.getenv("JARVIS_ECHO_TOKEN_OVERLAP", "0.7"))


def _is_stop_command(text: str) -> bool:
    """Detecta intención de cancelar/detener la tarea activa."""
    probe = _normalize_text(text)
    if not probe:
        return False
    stops = (
        "para", "detente", "stop", "cancela", "cancel", "aborta",
        "para todo", "detente ya", "cancela todo", "para por favor",
    )
    return any(s in probe for s in stops)


def _is_status_request(text: str) -> bool:
    """Detecta pregunta de estado sobre la última tarea."""
    probe = _normalize_text(text)
    if not probe:
        return False
    patterns = (
        "como vas", "como vamos", "que paso", "que paso con",
        "terminaste", "ya acabaste", "listo", "status", "estado",
        "que hiciste", "dame un reporte", "reporte", "resume",
        "ya termino", "como quedo", "resultado",
    )
    return any(p in probe for p in patterns)


def _is_voice_help_request(text: str) -> bool:
    """Detecta petición de lista de comandos disponibles."""
    probe = _normalize_text(text)
    return any(p in probe for p in ("que puedes hacer", "ayuda", "help", "comandos", "capacidades"))


def _looks_like_open_app_intent(text: str) -> bool:
    probe = _normalize_text(text)
    if not probe:
        return False
    open_app_patterns = (
        "abre ",
        "abrir ",
        "open ",
        "launch ",
        "inicia ",
        "ejecuta ",
        "run ",
    )
    return any(probe.startswith(pattern) for pattern in open_app_patterns)


def _build_agent_voice_reply(result: dict, _goal: str = "") -> str:
    """
    Convierte el dict de resultado del AutonomousAgent en una respuesta
    verbal estilo Jarvis: concisa, profesional, con contexto del objetivo.
    """
    status = str(result.get("status") or "unknown").lower()
    summary = str(result.get("summary") or "").strip()
    error = str(result.get("error") or "").strip()

    # Extraer info útil de results anidados
    results_list = result.get("results") or []
    if isinstance(results_list, list):
        for r in results_list:
            if not isinstance(r, dict):
                continue
            inner = r.get("result") or {}
            if isinstance(inner, dict):
                if not summary:
                    summary = str(inner.get("summary") or "").strip()
                if not error:
                    error = str(inner.get("error") or "").strip()

    if status in ("completed", "success", "ok"):
        if summary:
            return f"Misión cumplida, señor. {summary}"
        return "Tarea completada, señor."

    if status in ("failed", "error"):
        if error and len(error) < 150:
            # Clean technical jargon from errors before vocalizing
            clean_err = re.sub(r"(traceback|error|exception|file|line \d+)", "", error, flags=re.IGNORECASE)
            clean_err = " ".join(clean_err.split())[:120]
            if clean_err:
                return f"Disculpe señor, encontré un obstáculo: {clean_err}."
        return "Lo siento señor, la tarea no pudo completarse. Revise el log para más detalles."

    if status == "blocked":
        return "La tarea requiere aprobación manual, señor. Revise el panel de control."

    if status == "dry_run":
        return "Simulación completada, señor. Sin cambios aplicados al sistema."

    if status in ("queued", "pending"):
        return "La tarea está en cola, señor. Le notificaré cuando concluya."

    return f"Proceso finalizado con estado: {status}, señor."


@dataclass
class _AgentBridge:
    client_id: str
    execution_mode: str
    max_iterations: int
    _running_task: Any = field(default=None, repr=False)

    async def stop(self) -> None:
        if self._running_task is not None and not self._running_task.done():
            self._running_task.cancel()

    def cancel_current(self) -> bool:
        """Cancel the running goal task if any. Returns True if a task was cancelled."""
        if self._running_task is not None and not self._running_task.done():
            self._running_task.cancel()
            return True
        return False

    async def run_goal(self, goal: str) -> dict:
        from brain.agent_bridge import AgentBridge
        bridge = AgentBridge(client_id=self.client_id, max_iterations=self.max_iterations)
        coro = bridge.run(str(goal or "").strip())
        self._running_task = asyncio.ensure_future(coro)
        try:
            return await self._running_task
        except asyncio.CancelledError:
            return {"status": "cancelled", "goal": goal}
        finally:
            self._running_task = None


class JarvisListener:
    def __init__(self) -> None:
        self._wake_word = str(os.getenv("JARVIS_WAKE_WORD", "k'an")).strip() or "k'an"
        self._wake_timeout = float(os.getenv("JARVIS_WAKE_TIMEOUT", "8"))
        # 6s: tiempo para capturar "k'an <comando>" inline en una sola frase
        self._wake_phrase_seconds = float(os.getenv("JARVIS_WAKE_PHRASE_SECONDS", "6.0"))
        self._command_timeout = float(os.getenv("JARVIS_COMMAND_TIMEOUT", "12"))
        self._command_phrase_seconds = float(os.getenv("JARVIS_COMMAND_PHRASE_SECONDS", "15"))
        self._ambient_seconds = float(os.getenv("JARVIS_AMBIENT_SECONDS", "0.8"))
        self._energy_threshold = float(os.getenv("JARVIS_ENERGY_THRESHOLD", "200"))
        self._dynamic_energy = os.getenv("JARVIS_DYNAMIC_ENERGY", "true").lower() not in {"0", "false", "no"}
        # 1.0s: pausa más generosa antes de cortar la frase (evita cortar a la mitad)
        self._pause_threshold = float(os.getenv("JARVIS_PAUSE_THRESHOLD", "1.0"))
        self._non_speaking_duration = float(os.getenv("JARVIS_NON_SPEAKING_DURATION", "0.4"))
        self._post_speak_silence = float(os.getenv("JARVIS_POST_SPEAK_SILENCE", "0.8"))
        self._whisper_language = str(os.getenv("WHISPER_LANGUAGE", "")).strip() or None
        client_id = str(os.getenv("JARVIS_CLIENT_ID", "")).strip()
        self._agent_enabled = bool(client_id)
        self._agent = _AgentBridge(
            client_id=client_id,
            execution_mode=str(os.getenv("JARVIS_EXECUTION_MODE", "autonomous")).strip() or "autonomous",
            max_iterations=int(os.getenv("JARVIS_MAX_ITERATIONS", "10")),
        )
        self._stop = asyncio.Event()
        self._scene_query_lock = asyncio.Lock()
        # Persistent memory for status queries ("como vas?", "terminaste?")
        self._last_task_result: Optional[dict] = None
        self._last_task_goal: str = ""
        self._agent_busy: bool = False

    async def start(self) -> None:
        sr = _import_speechrecognition()
        audio_reinit_backoff_s = float(os.getenv("JARVIS_AUDIO_REINIT_BACKOFF_SEC", "0.6"))

        # --- Validación de hardware de audio ---
        configured_index = _validate_microphone()
        devices_by_index = {int(d["index"]): str(d["name"]) for d in _list_audio_devices()}
        selected_mic_name = devices_by_index.get(configured_index, "")
        is_macbook_mic = "macbook" in selected_mic_name.lower() or "built-in" in selected_mic_name.lower()

        def _build_audio_stack() -> tuple[Any, Any]:
            recognizer = sr.Recognizer()
            recognizer.energy_threshold = self._energy_threshold
            recognizer.dynamic_energy_threshold = self._dynamic_energy
            recognizer.pause_threshold = self._pause_threshold
            recognizer.non_speaking_duration = self._non_speaking_duration
            if is_macbook_mic and not os.getenv("JARVIS_DYNAMIC_ENERGY_DAMPING"):
                setattr(recognizer, "dynamic_energy_adjustment_damping", 0.10)
            if is_macbook_mic and not os.getenv("JARVIS_DYNAMIC_ENERGY_RATIO"):
                # 1.4 = más sensible que el default 1.7 — capta voz más suave
                setattr(recognizer, "dynamic_energy_ratio", 1.4)
            logger.info(
                "Reconocedor configurado: energy_threshold=%.0f, dynamic_energy_threshold=%s, pause_threshold=%.2f, non_speaking_duration=%.2f",
                self._energy_threshold,
                self._dynamic_energy,
                self._pause_threshold,
                self._non_speaking_duration,
            )
            mic = sr.Microphone(device_index=configured_index)
            return recognizer, mic

        recognizer, microphone = _build_audio_stack()
        try:
            probe_rms = await _probe_microphone_stream(microphone)
            logger.info("Probe de micrófono OK (rms=%d)", probe_rms)
        except Exception as exc:
            logger.exception("No se pudo abrir stream de micrófono: %s", exc)
            raise RuntimeError(
                "Micrófono no disponible para el daemon. "
                "Concede permiso a Python en Privacidad > Micrófono."
            ) from exc

        if self._agent_enabled:
            await self._agent.start()
        else:
            logger.warning("JARVIS_CLIENT_ID no configurado: listener activo en modo voz/vision.")

        # Calibrar ruido ambiente inicial (adapta energy_threshold al entorno)
        logger.info("Calibrando ruido ambiente (%.1fs)...", self._ambient_seconds)
        await _calibrate_noise(recognizer, microphone, self._ambient_seconds)
        logger.info(
            "Calibración completa. energy_threshold ajustado a: %.0f",
            recognizer.energy_threshold,
        )

        # --- Confirmación de voz: si NO escuchas esto, hay un error en el voice_engine ---
        logger.info("Jarvis listener activo. Wake word: %s", self._wake_word)
        await _speak_and_wait(
            "Sistemas en línea, señor. Estoy escuchando.",
            post_silence_s=self._post_speak_silence,
        )
        publish_state("standby", source="voice", text="listener_ready", level=0.15)

        while not self._stop.is_set():
            try:
                # Don't capture audio while TTS is playing — prevents echo feedback.
                if _INPUT_MUTE_EVENT.is_set():
                    await asyncio.sleep(0.1)
                    continue

                # ── FASE 1: detectar wake word ──────────────────────────────
                wake_audio = await _record_phrase(
                    recognizer,
                    microphone,
                    timeout=self._wake_timeout,
                    phrase_time_limit=self._wake_phrase_seconds,
                )
                wake_text = await transcribe_audio_bytes(
                    wake_audio.get_wav_data(),
                    filename="wake.wav",
                    content_type="audio/wav",
                    hint_language=self._whisper_language,
                )
                if _is_echo_transcript(wake_text):
                    continue
                if not wake_text or not contains_wake_word(wake_text, self._wake_word):
                    continue

                logger.info("Wake word detectada: %s", wake_text)
                publish_state("listening", source="voice", text=wake_text[:180], level=0.65)

                # ── Detección de comando inline ──────────────────────────────
                # Si el usuario dijo "k'an <comando>" en una sola frase,
                # el comando ya está en wake_text — no hace falta escuchar otra vez.
                inline_cmd = _extract_inline_command(wake_text)
                if inline_cmd:
                    command_text = inline_cmd
                    logger.info("Comando inline: %r", command_text[:180])
                    await _play_listening_beep()
                else:
                    # ── FASE 2: wake word solo — esperar el comando ───────────
                    await _play_listening_beep()
                    try:
                        command_audio = await _record_phrase(
                            recognizer,
                            microphone,
                            timeout=self._command_timeout,
                            phrase_time_limit=self._command_phrase_seconds,
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        await _speak_and_wait(
                            "No escuché ningún comando, señor.",
                            post_silence_s=self._post_speak_silence,
                        )
                        continue
                    command_text = await transcribe_audio_bytes(
                        command_audio.get_wav_data(),
                        filename="command.wav",
                        content_type="audio/wav",
                        hint_language=self._whisper_language,
                    )
                    if _is_echo_transcript(command_text):
                        continue
                    if not command_text:
                        await _speak_and_wait(
                            "No escuché ningún comando, señor. Diga el wake word e intente de nuevo.",
                            post_silence_s=self._post_speak_silence,
                        )
                        continue

                logger.info("Comando de voz: %r", command_text[:180] if command_text else "")

                # ── FASE 3: enrutamiento de comandos ────────────────────────

                # 3a. Detener/cancelar tarea activa
                if _is_stop_command(command_text):
                    if self._agent_busy:
                        cancelled = self._agent.cancel_current()
                        if cancelled:
                            await _speak_and_wait(
                                "Deteniendo la tarea actual, señor.",
                                post_silence_s=self._post_speak_silence,
                            )
                        else:
                            await _speak_and_wait(
                                "No hay ninguna tarea activa en este momento, señor.",
                                post_silence_s=self._post_speak_silence,
                            )
                    else:
                        await _speak_and_wait(
                            "Todo tranquilo, señor. No hay tareas en ejecución.",
                            post_silence_s=self._post_speak_silence,
                        )
                    continue

                # 3b. Consulta de estado de última tarea
                if _is_status_request(command_text):
                    if self._agent_busy:
                        await _speak_and_wait(
                            f"Aún trabajando en eso, señor. El objetivo era: {self._last_task_goal[:80]}",
                            post_silence_s=self._post_speak_silence,
                        )
                    elif self._last_task_result is not None:
                        reply = _build_agent_voice_reply(self._last_task_result, self._last_task_goal)
                        await _speak_and_wait(
                            f"El reporte de la última tarea: {reply}",
                            post_silence_s=self._post_speak_silence,
                        )
                    else:
                        await _speak_and_wait(
                            "Sin tareas previas en esta sesión, señor.",
                            post_silence_s=self._post_speak_silence,
                        )
                    continue

                # 3c. Consulta de ayuda / capacidades
                if _is_voice_help_request(command_text):
                    await _speak_and_wait(
                        "Puedo navegar el web, abrir aplicaciones, tomar capturas de pantalla, "
                        "describir lo que ve en su pantalla, ejecutar tareas autónomas y reportarle los resultados. "
                        "Solo diga el wake word seguido de su instrucción, señor.",
                        post_silence_s=self._post_speak_silence,
                    )
                    continue

                # 3d. Consulta de escena visual
                if is_scene_query_command(command_text):
                    publish_state("thinking", source="vision", text=command_text[:180], level=0.55)
                    async with self._scene_query_lock:
                        scene_text = await _handle_scene_query()
                    await _speak_and_wait(scene_text, post_silence_s=self._post_speak_silence)
                    continue

                # 3e. Agente no configurado
                if not self._agent_enabled:
                    if _looks_like_open_app_intent(command_text):
                        await _speak_and_wait(
                            "Señor, necesito que configure JARVIS_CLIENT_ID en el archivo de entorno "
                            "para acceder a mi núcleo de ejecución.",
                            post_silence_s=self._post_speak_silence,
                        )
                    else:
                        await _speak_and_wait(
                            "Escuché su instrucción, señor, pero mi módulo de autonomía está desactivado. "
                            "Configure JARVIS_CLIENT_ID para habilitarlo.",
                            post_silence_s=self._post_speak_silence,
                        )
                    continue

                # 3f. Ejecutar con el AutonomousAgent ────────────────────────
                # Confirmación inmediata antes de empezar (Jarvis siempre acusa recibo)
                await _speak_and_wait(
                    "Entendido, señor. Procesando ahora.",
                    post_silence_s=0.3,  # silencio corto — ya vuelve a escuchar
                )

                self._agent_busy = True
                self._last_task_goal = command_text
                self._last_task_result = None
                try:
                    publish_state("thinking", source="voice", text=command_text[:220], level=0.7)
                    logger.info(
                        "[CORE] Input recibido vía Voz -> [BRIDGE] Iniciando ejecución con ID: %s",
                        self._agent.client_id or "None",
                    )
                    logger.info("Ejecutando goal: %r", command_text[:120])
                    result = await self._agent.run_goal(command_text)
                    self._last_task_result = result
                    status = str(result.get("status") or "unknown")
                    logger.info(
                        "AutonomousAgent finalizado: status=%s | goal=%r | resultado=%s",
                        status,
                        command_text[:80],
                        str(result)[:400],
                    )

                    # Vocalizar resultado — siempre, sin importar éxito o fallo
                    voice_reply = _build_agent_voice_reply(result, command_text)
                    await _speak_and_wait(voice_reply, post_silence_s=self._post_speak_silence)
                except asyncio.CancelledError:
                    await _speak_and_wait(
                        "Tarea cancelada por su instrucción, señor.",
                        post_silence_s=self._post_speak_silence,
                    )
                except Exception as agent_exc:
                    logger.exception("Error en AutonomousAgent: %s", agent_exc)
                    await _speak_and_wait(
                        "Ocurrió un error interno, señor. Revise el log para más detalles.",
                        post_silence_s=self._post_speak_silence,
                    )
                finally:
                    self._agent_busy = False
                    publish_state("standby", source="voice", text="", level=0.15)

            except (asyncio.TimeoutError, TimeoutError):
                # Timeout esperando wake word — completamente normal.
                continue
            except Exception as exc:
                exc_text = str(exc).lower()
                logger.exception("Jarvis listener error: %s", exc)
                if any(token in exc_text for token in ("microphone", "audio", "paerror", "stream", "input")):
                    logger.warning("Error de audio detectado; reinicializando stack de micrófono.")
                    await asyncio.sleep(audio_reinit_backoff_s)
                    try:
                        recognizer, microphone = _build_audio_stack()
                        await _calibrate_noise(recognizer, microphone, self._ambient_seconds)
                    except Exception as rebuild_exc:
                        logger.exception("Falló reinicialización de micrófono: %s", rebuild_exc)
                        await asyncio.sleep(audio_reinit_backoff_s)
                    continue
                await asyncio.sleep(0.4)

    async def stop(self) -> None:
        self._stop.set()
        if self._agent_enabled:
            await self._agent.stop()


async def _amain() -> None:
    load_environment(
        strict=True,
        required_all=("JARVIS_CLIENT_ID",),
        context="jarvis_listener",
    )
    logging.basicConfig(
        level=os.getenv("JARVIS_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    listener = JarvisListener()
    loop = asyncio.get_running_loop()

    def _signal_handler() -> None:
        listener._stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        await listener.start()
    finally:
        await listener.stop()


if __name__ == "__main__":
    asyncio.run(_amain())
