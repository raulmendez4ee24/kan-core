SKILL = {
    "name": "calendar_schedule",
    "trigger_keywords": ["agenda", "calendario", "cita", "reunión", "reunion", "recordatorio", "schedule", "calendar", "meeting", "appointment"],
    "integrations": ["google_calendar", "outlook", "calendly"],
    "description": "Agenda citas, reuniones y recordatorios en Google Calendar, Outlook o Calendly.",
    "instructions": (
        "Para agendar o gestionar eventos usa action_type=integration, integration=google_calendar|outlook|calendly. "
        "Acciones: create_event, list_events, delete_event, create_reminder. "
        "arguments para create_event: title, start_datetime (ISO 8601), end_datetime (ISO 8601), "
        "attendees (lista de emails, opcional), description (opcional). "
        "Usa zona horaria del usuario si se conoce."
    ),
    "example_action": {
        "contract_version": "1.0",
        "action_type": "integration",
        "action": "create_event",
        "integration": "google_calendar",
        "arguments": {
            "title": "Llamada de seguimiento",
            "start_datetime": "2026-02-25T10:00:00-06:00",
            "end_datetime": "2026-02-25T10:30:00-06:00",
            "attendees": ["cliente@ejemplo.com"],
        },
        "rationale": "Agendar llamada de seguimiento con el cliente.",
    },
}
