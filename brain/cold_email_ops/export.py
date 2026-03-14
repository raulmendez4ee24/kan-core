"""Fase 8: Export — write cola_de_hoy to CSV/JSON/n8n payload; append to archivos_generados."""
from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, List, Tuple


def run(
    data: Dict[str, List[Dict[str, Any]]],
    cola_de_hoy: Dict[str, Any],
    export_format: str,
    archivos_generados: List[str],
    log: List[str],
) -> Tuple[List[str], List[str]]:
    """Export cola_de_hoy.leads to file (csv/json) or POST to n8n webhook (n8n)."""
    leads = cola_de_hoy.get("leads", [])
    if not leads:
        log.append("Export: no leads in cola, skip")
        return archivos_generados, log

    if export_format == "csv":
        path = "cold_email_cola.csv"
        try:
            headers = list(leads[0].keys()) if leads else []
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
                w.writeheader()
                w.writerows(leads)
            archivos_generados.append(path)
            log.append(f"Export: wrote {path}")
        except Exception as e:
            log.append(f"Export CSV failed: {e}")
    elif export_format == "json":
        path = "cold_email_cola.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"cola_de_hoy": cola_de_hoy, "leads": leads}, f, indent=2, ensure_ascii=False)
            archivos_generados.append(path)
            log.append(f"Export: wrote {path}")
        except Exception as e:
            log.append(f"Export JSON failed: {e}")
    elif export_format == "n8n":
        webhook = os.getenv("COLD_EMAIL_N8N_WEBHOOK_URL", "").strip()
        if webhook:
            try:
                import httpx
                r = httpx.post(webhook, json={"cola_de_hoy": cola_de_hoy, "leads": leads}, timeout=30.0)
                if r.is_success:
                    log.append("Export: sent to n8n webhook")
                else:
                    log.append(f"Export n8n: HTTP {r.status_code}")
            except Exception as e:
                log.append(f"Export n8n failed: {e}")
        else:
            path = "cold_email_cola_n8n.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"cola_de_hoy": cola_de_hoy, "leads": leads}, f, indent=2, ensure_ascii=False)
            archivos_generados.append(path)
            log.append(f"Export: no webhook URL, wrote {path}")
    return archivos_generados, log
