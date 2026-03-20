"""Google Sheets client: open by ID, read/write tabs (leads, inboxes, domains, campaign, costs)."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List

_log = logging.getLogger("kan_core.google_sheets_client")

def _get_credentials():
    import google.oauth2.service_account  # type: ignore
    raw = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON", "").strip()
    if raw:
        try:
            info = json.loads(raw)
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            return google.oauth2.service_account.Credentials.from_service_account_info(
                info,
                scopes=scopes,
            )
        except Exception as e:
            _log.warning("GOOGLE_SHEETS_CREDENTIALS_JSON invalid: %s", e)
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if path and os.path.isfile(path):
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        return google.oauth2.service_account.Credentials.from_service_account_file(
            path,
            scopes=scopes,
        )
    return None


def get_client():
    """Return gspread Client or None if credentials missing."""
    creds = _get_credentials()
    if not creds:
        return None
    try:
        import gspread  # type: ignore
        return gspread.authorize(creds)
    except Exception as e:
        _log.warning("gspread authorize failed: %s", e)
        return None


def open_spreadsheet(spreadsheet_id: str):
    """Open spreadsheet by ID. Raises if client or sheet not found."""
    client = get_client()
    if not client:
        raise RuntimeError("Google Sheets credentials missing (GOOGLE_SHEETS_CREDENTIALS_JSON or GOOGLE_APPLICATION_CREDENTIALS)")
    return client.open_by_key(spreadsheet_id)


def read_tab(spreadsheet_id: str, tab_name: str) -> List[Dict[str, Any]]:
    """Read a worksheet by name; return list of dicts (first row = keys). Empty if tab missing."""
    try:
        book = open_spreadsheet(spreadsheet_id)
        sheet = book.worksheet(tab_name)
    except Exception as e:
        _log.debug("Sheet tab %r not found or error: %s", tab_name, e)
        return []
    rows = sheet.get_all_records(default_blank="")
    return [dict(r) for r in rows]


def read_all_tabs(spreadsheet_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """Read leads, inboxes, domains, campaign, costs. Missing tab = []."""
    names = ["leads", "inboxes", "domains", "campaign", "costs"]
    out: Dict[str, List[Dict[str, Any]]] = {}
    for name in names:
        out[name] = read_tab(spreadsheet_id, name)
    return out


def append_rows(spreadsheet_id: str, tab_name: str, rows: List[List[Any]]) -> bool:
    """Append rows to worksheet. Returns True on success."""
    try:
        book = open_spreadsheet(spreadsheet_id)
        sheet = book.worksheet(tab_name)
        sheet.append_rows(rows, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        _log.warning("append_rows %r failed: %s", tab_name, e)
        return False


def ensure_worksheet_with_headers(
    spreadsheet_id: str,
    tab_name: str,
    headers: List[str],
) -> bool:
    """Ensure worksheet exists and first row is headers. Creates tab if missing."""
    try:
        import gspread  # type: ignore
        book = open_spreadsheet(spreadsheet_id)
        try:
            sheet = book.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            sheet = book.add_worksheet(title=tab_name, rows=1000, cols=len(headers))
        row1 = sheet.row_values(1) if sheet.row_values(1) else []
        if row1 != headers:
            sheet.update("A1", [headers], value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        _log.warning("ensure_worksheet_with_headers %r failed: %s", tab_name, e)
        return False


def update_cells(spreadsheet_id: str, tab_name: str, updates: List[Dict[str, Any]]) -> bool:
    """Updates: list of {row_index (1-based), col_index (1-based), value}. Returns True on success."""
    if not updates:
        return True
    try:
        book = open_spreadsheet(spreadsheet_id)
        sheet = book.worksheet(tab_name)
        for u in updates:
            row = int(u.get("row_index", 0))
            col = int(u.get("col_index", 0))
            val = u.get("value")
            if row and col:
                sheet.update_cell(row, col, val)
        return True
    except Exception as e:
        _log.warning("update_cells %r failed: %s", tab_name, e)
        return False


def batch_update_by_header(
    spreadsheet_id: str,
    tab_name: str,
    key_column: str,
    updates_by_key: Dict[str, Dict[str, Any]],
) -> bool:
    """Update rows by key (e.g. lead_id). updates_by_key: {key: {column_name: value}}."""
    try:
        book = open_spreadsheet(spreadsheet_id)
        sheet = book.worksheet(tab_name)
        all_values = sheet.get_all_values()
        headers = list(all_values[0]) if all_values else []
        key_col_idx = headers.index(key_column) + 1 if key_column in headers else 0
        if not key_col_idx:
            return False
        key_to_row: Dict[str, int] = {}
        for i, row in enumerate(all_values, start=1):
            if i == 1:
                continue
            if key_col_idx <= len(row):
                key_to_row[str(row[key_col_idx - 1]).strip()] = i
        batch_requests: List[Dict[str, Any]] = []
        for key, col_updates in updates_by_key.items():
            row_idx = key_to_row.get(key)
            if not row_idx:
                continue
            row_requests = _build_row_batch_requests(headers, row_idx, col_updates)
            batch_requests.extend(row_requests)
        if not batch_requests:
            return True
        sheet.batch_update(batch_requests, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        _log.warning("batch_update_by_header %r failed: %s", tab_name, e)
        return False


def _build_row_batch_requests(
    headers: List[str],
    row_idx: int,
    col_updates: Dict[str, Any],
) -> List[Dict[str, Any]]:
    indexed_updates = sorted(
        (headers.index(col_name) + 1, value)
        for col_name, value in col_updates.items()
        if col_name in headers
    )
    if not indexed_updates:
        return []

    grouped: List[List[tuple[int, Any]]] = []
    current_group: List[tuple[int, Any]] = []
    for col_idx, value in indexed_updates:
        if not current_group or col_idx == current_group[-1][0] + 1:
            current_group.append((col_idx, value))
            continue
        grouped.append(current_group)
        current_group = [(col_idx, value)]
    if current_group:
        grouped.append(current_group)

    requests: List[Dict[str, Any]] = []
    for group in grouped:
        start_col = group[0][0]
        end_col = group[-1][0]
        start_ref = f"{_column_letter(start_col)}{row_idx}"
        end_ref = f"{_column_letter(end_col)}{row_idx}"
        range_ref = start_ref if start_col == end_col else f"{start_ref}:{end_ref}"
        requests.append(
            {
                "range": range_ref,
                "values": [[_sheet_value(value) for _, value in group]],
            }
        )
    return requests


def _sheet_value(value: Any) -> Any:
    if value is None:
        return ""
    return value


def _column_letter(col_idx: int) -> str:
    letters = []
    n = max(1, int(col_idx))
    while n:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))
