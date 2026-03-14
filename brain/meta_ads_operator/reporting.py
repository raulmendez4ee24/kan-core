from __future__ import annotations

from typing import Iterable, List

from .models import EntityReport, MetaAdsOperatorReport, MetaAdsSnapshot


def build_executive_summary(
    snapshot: MetaAdsSnapshot,
    *,
    entity_reports: List[EntityReport],
    reasons: Iterable[str],
) -> str:
    top_flags: List[str] = []
    for report in entity_reports:
        for flag in report.flags:
            if flag not in top_flags:
                top_flags.append(flag)
    entities = len(entity_reports)
    parts = [
        f"Cuenta {snapshot.account_id or 'sin_id'} analizada para {snapshot.objective}.",
        f"Se revisaron {entities} entidades con foco en rentabilidad, calidad de trafico y seguimiento comercial.",
    ]
    if top_flags:
        parts.append(f"Hallazgos principales: {', '.join(top_flags[:4])}.")
    if reasons:
        parts.append(f"Accion dominante sugerida: {next(iter(reasons))}.")
    return " ".join(parts)


def build_report(
    snapshot: MetaAdsSnapshot,
    *,
    entity_reports: List[EntityReport],
    account_findings: list,
    account_actions: list,
    reasons: List[str],
    flags: List[str],
) -> MetaAdsOperatorReport:
    return MetaAdsOperatorReport(
        snapshot=snapshot,
        executive_summary=build_executive_summary(snapshot, entity_reports=entity_reports, reasons=reasons),
        entity_reports=entity_reports,
        account_findings=account_findings,
        account_actions=account_actions,
        reasons=reasons,
        flags=flags,
    )
