"""
NgrokManager — el servidor gestiona ngrok, no al revés.

Arranca ngrok como subproceso cuando el servidor inicia, obtiene la URL
pública automáticamente, y registra todos los webhooks (Telegram, Meta/WhatsApp).
Si ngrok muere, lo reinicia. El agente es completamente autónomo.

Env vars:
    ENABLE_NGROK_AUTO=true          — activa el auto-gestión de ngrok
    NGROK_PORT=8000                 — puerto local del servidor
    NGROK_AUTHTOKEN=<token>         — opcional, para dominios estáticos
    NGROK_STATIC_DOMAIN=<domain>    — opcional, dominio fijo (plan paid)
    NGROK_AUTO_REGISTER_TELEGRAM=true  — registra webhook de Telegram al arrancar
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Optional

import httpx
from integrations.telegram_admin import resolve_telegram_mode

logger = logging.getLogger("kan_core.ngrok_manager")

_NGROK_API = "http://localhost:4040/api"
_POLL_INTERVAL = 1.0   # segundos entre intentos de obtener la URL
_POLL_TIMEOUT = 15.0   # máximo tiempo esperando que ngrok arranque


class NgrokManager:
    """Gestiona el ciclo de vida completo de ngrok dentro del servidor."""

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._public_url: Optional[str] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._port = int(os.getenv("NGROK_PORT", "8000"))
        raw_auth = os.getenv("NGROK_AUTHTOKEN", "").strip()
        self._authtoken = raw_auth if raw_auth and "__IN_ENV_" not in raw_auth else None
        raw_domain = os.getenv("NGROK_STATIC_DOMAIN", "").strip()
        # Ignore placeholder values
        self._static_domain = raw_domain if raw_domain and "your-subdomain" not in raw_domain else None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> Optional[str]:
        """Arranca ngrok y devuelve la URL pública. Registra webhooks al final."""
        if self._is_running():
            logger.info("ngrok ya está corriendo, obteniendo URL...")
            self._public_url = await self._get_tunnel_url()
            if self._public_url:
                await self._register_all_webhooks()
            return self._public_url

        self._launch_process()
        self._public_url = await self._wait_for_tunnel()

        if not self._public_url:
            logger.error("ngrok no pudo obtener URL pública en %ss", _POLL_TIMEOUT)
            return None

        logger.info("ngrok activo: %s", self._public_url)
        await self._register_all_webhooks()
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        return self._public_url

    async def stop(self) -> None:
        """Detiene ngrok limpiamente."""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None
        self._public_url = None
        logger.info("ngrok detenido")

    @property
    def public_url(self) -> Optional[str]:
        return self._public_url

    # ------------------------------------------------------------------
    # Internal: process management
    # ------------------------------------------------------------------

    def _launch_process(self) -> None:
        cmd = ["ngrok", "http", str(self._port), "--log", "stdout"]
        if self._static_domain:
            cmd += ["--url", self._static_domain]
        elif self._authtoken:
            # authtoken is set via ngrok config, but we pass it inline if provided
            cmd += ["--authtoken", self._authtoken]

        logger.info("Iniciando ngrok: %s", " ".join(cmd))
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _is_running(self) -> bool:
        """Devuelve True si hay un proceso ngrok activo (nuestro o externo)."""
        if self._process and self._process.poll() is None:
            return True
        # Check for external ngrok (already running)
        try:
            import urllib.request
            urllib.request.urlopen(f"{_NGROK_API}/tunnels", timeout=1)
            return True
        except Exception:
            return False

    async def _wait_for_tunnel(self) -> Optional[str]:
        """Espera hasta que ngrok exponga la URL pública."""
        elapsed = 0.0
        while elapsed < _POLL_TIMEOUT:
            url = await self._get_tunnel_url()
            if url:
                return url
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL
        return None

    async def _get_tunnel_url(self) -> Optional[str]:
        """Llama a la API local de ngrok para obtener la URL https."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"{_NGROK_API}/tunnels")
                tunnels = r.json().get("tunnels", [])
                for t in tunnels:
                    url = t.get("public_url", "")
                    if url.startswith("https://"):
                        return url
        except Exception:
            pass
        return None

    async def _monitor_loop(self) -> None:
        """Monitorea ngrok cada 30s; lo reinicia si muere."""
        while True:
            await asyncio.sleep(30)
            if self._process and self._process.poll() is not None:
                logger.warning("ngrok murió (exit %s), reiniciando...", self._process.returncode)
                self._launch_process()
                new_url = await self._wait_for_tunnel()
                if new_url and new_url != self._public_url:
                    self._public_url = new_url
                    logger.info("ngrok reiniciado con nueva URL: %s", new_url)
                    await self._register_all_webhooks()

    # ------------------------------------------------------------------
    # Webhook registration
    # ------------------------------------------------------------------

    async def _register_all_webhooks(self) -> None:
        """Registra webhooks de todos los canales configurados."""
        if not self._public_url:
            return
        tasks = []
        register_telegram = os.getenv("ENABLE_NGROK_AUTO_REGISTER_TELEGRAM", "true").lower() not in {
            "0",
            "false",
            "no",
        }
        mode_state = resolve_telegram_mode()
        mode = str(mode_state.get("mode") or "off")
        if register_telegram and mode != "webhook":
            logger.info(
                "Omitiendo registro de webhook de Telegram desde ngrok_manager (mode=%s)",
                mode,
            )
            register_telegram = False
        if register_telegram:
            tasks.append(self._register_telegram())
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _register_telegram(self) -> None:
        """Registra el webhook de Telegram con la nueva URL pública."""
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            return

        secret = os.getenv("TELEGRAM_SECRET_TOKEN", "").strip() or None
        webhook_url = f"{self._public_url}/webhooks/telegram"

        # Update env var for reference
        os.environ["TELEGRAM_WEBHOOK_URL"] = webhook_url

        payload: dict = {
            "url": webhook_url,
            "allowed_updates": ["message", "channel_post", "callback_query"],
        }
        if secret:
            payload["secret_token"] = secret

        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                # Drop pending updates from previous sessions
                await c.post(
                    f"https://api.telegram.org/bot{token}/deleteWebhook",
                    json={"drop_pending_updates": True},
                )
                r = await c.post(
                    f"https://api.telegram.org/bot{token}/setWebhook",
                    json=payload,
                )
                result = r.json()
                if result.get("ok"):
                    logger.info("Telegram webhook registrado: %s", webhook_url)
                else:
                    logger.warning("Telegram webhook falló: %s", result)
        except Exception as exc:
            logger.error("Error registrando Telegram webhook: %s", exc)


# Singleton
_manager: Optional[NgrokManager] = None


def get_manager() -> NgrokManager:
    global _manager
    if _manager is None:
        _manager = NgrokManager()
    return _manager
