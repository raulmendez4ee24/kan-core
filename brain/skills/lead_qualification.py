SKILL = {
    "name": "lead_qualification",
    "trigger_keywords": ["lead", "calificar", "calificacion", "prospecto", "prospect", "score", "puntuacion", "qualify", "oportunidad", "opportunity"],
    "integrations": ["hubspot", "salesforce", "pipedrive"],
    "description": "Califica automáticamente leads con base en criterios BANT o personalizados.",
    "instructions": (
        "Para calificar un lead usa action_type=integration, integration=hubspot|salesforce|pipedrive. "
        "Acción: qualify_lead. "
        "arguments debe incluir: contact_id o email del lead, y los criterios disponibles: "
        "budget (presupuesto disponible), authority (es decisor: true/false), "
        "need (necesidad identificada), timeline (tiempo de decisión en días). "
        "El sistema calculará un score BANT del 0 al 100 y actualizará el CRM con el resultado."
    ),
    "example_action": {
        "contract_version": "1.0",
        "action_type": "integration",
        "action": "qualify_lead",
        "integration": "hubspot",
        "arguments": {
            "email": "prospecto@empresa.com",
            "budget": 50000,
            "authority": True,
            "need": "Automatizar procesos de ventas",
            "timeline": 30,
        },
        "rationale": "Calificar el lead antes de asignarlo al equipo comercial.",
    },
}
