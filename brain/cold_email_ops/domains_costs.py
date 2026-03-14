"""Fase 5: Domains & costs — list unpaid/overdue from costs tab, domains with DNS/SPF/DKIM issues."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def run(
    data: Dict[str, List[Dict[str, Any]]],
    cosas_por_pagar_configurar: List[Dict[str, Any]],
    log: List[str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Append unpaid/overdue cost rows and domains with missing DNS/SPF/DKIM to cosas_por_pagar_configurar."""
    costs = data.get("costs", [])
    domains = data.get("domains", [])

    for row in costs:
        status = (str(row.get("status") or "")).strip().lower()
        if status in ("unpaid", "pending", ""):
            cosas_por_pagar_configurar.append({
                "type": "cost",
                "item": row.get("item"),
                "vendor": row.get("vendor"),
                "cost": row.get("cost"),
                "due_date": row.get("due_date"),
                "notes": row.get("notes"),
            })
    for row in domains:
        domain = row.get("domain") or ""
        dns = (str(row.get("dns_status") or "")).strip().lower()
        spf = (str(row.get("spf_status") or "")).strip().lower()
        dkim = (str(row.get("dkim_status") or "")).strip().lower()
        if dns != "ok" or spf != "ok" or dkim != "ok":
            cosas_por_pagar_configurar.append({
                "type": "domain_config",
                "domain": domain,
                "dns_status": dns or "missing",
                "spf_status": spf or "missing",
                "dkim_status": dkim or "missing",
            })
    log.append(f"Domains/costs: {len(cosas_por_pagar_configurar)} items to pay or configure")
    return cosas_por_pagar_configurar, log
