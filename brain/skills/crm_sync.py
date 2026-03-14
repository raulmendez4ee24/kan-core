SKILL = {
    "name": "crm_sync",
    "trigger_keywords": ["crm", "cliente", "contacto", "hubspot", "salesforce", "pipedrive", "contact", "lead"],
    "integrations": ["hubspot", "salesforce", "pipedrive"],
    "description": "Sincroniza y actualiza registros de CRM (HubSpot, Salesforce, Pipedrive).",
    "instructions": (
        "Para sincronizar o actualizar un contacto en CRM usa action_type=integration, "
        "integration=hubspot|salesforce|pipedrive. "
        "Acciones disponibles: create_contact, update_contact, get_contact, create_deal, update_deal. "
        "arguments debe incluir: email (obligatorio), name, phone, company, deal_stage según aplique."
    ),
    "example_action": {
        "contract_version": "1.0",
        "action_type": "integration",
        "action": "create_contact",
        "integration": "hubspot",
        "arguments": {"email": "user@example.com", "name": "Juan García", "phone": "+521234567890"},
        "rationale": "Crear contacto en HubSpot para el nuevo lead.",
    },
}
