# Browser Agent Runtime

This module requires Playwright browser binaries on the desktop host.

Install once on the host machine:

```bash
python -m playwright install chromium
```

Required environment variables are loaded by `desktop_agent/config.py`:

- `DESKTOP_AGENT_BROWSER_ENABLED`
- `DESKTOP_AGENT_BROWSER_HEADLESS`
- `DESKTOP_AGENT_BROWSER_CHANNEL`
- `DESKTOP_AGENT_BROWSER_USER_DATA_DIR`
- `DESKTOP_AGENT_BROWSER_DOMAIN_ALLOWLIST_JSON`
- `DESKTOP_AGENT_BROWSER_DEFAULT_TIMEOUT_MS`
