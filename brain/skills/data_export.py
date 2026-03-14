SKILL = {
    "name": "data_export",
    "trigger_keywords": ["exportar", "export", "csv", "excel", "reporte", "report", "sheets", "google sheets", "datos", "data"],
    "integrations": ["google_sheets", "airtable"],
    "description": "Exporta datos a CSV, Google Sheets o Airtable.",
    "instructions": (
        "Para exportar datos usa action_type=integration, integration=google_sheets|airtable. "
        "Acciones: append_rows (agrega filas), create_sheet (nueva hoja), export_csv. "
        "arguments para append_rows: spreadsheet_id, sheet_name, rows (lista de listas o dicts). "
        "Para CSV local usa action=export_csv con arguments: filename, data (lista de dicts)."
    ),
    "example_action": {
        "contract_version": "1.0",
        "action_type": "integration",
        "action": "append_rows",
        "integration": "google_sheets",
        "arguments": {
            "spreadsheet_id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
            "sheet_name": "Ventas",
            "rows": [["2026-02-21", "Cliente A", "$5000"]],
        },
        "rationale": "Registrar la venta en el Google Sheet de reporte mensual.",
    },
}
