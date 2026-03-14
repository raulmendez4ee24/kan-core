SKILL = {
    "name": "shopify_ops",
    "trigger_keywords": ["shopify", "orden", "order", "producto", "product", "inventario", "inventory", "tienda", "store", "pedido"],
    "integrations": ["shopify"],
    "description": "Gestiona órdenes, productos e inventario en Shopify.",
    "instructions": (
        "Para operaciones en Shopify usa action_type=integration, integration=shopify. "
        "Acciones disponibles: "
        "get_order (arguments: order_id), "
        "list_orders (arguments: status=open|closed|any, limit), "
        "get_product (arguments: product_id), "
        "update_inventory (arguments: inventory_item_id, location_id, available), "
        "create_refund (arguments: order_id, reason). "
        "Siempre incluye los IDs exactos de Shopify."
    ),
    "example_action": {
        "contract_version": "1.0",
        "action_type": "integration",
        "action": "list_orders",
        "integration": "shopify",
        "arguments": {"status": "open", "limit": 10},
        "rationale": "Listar las últimas órdenes abiertas en Shopify.",
    },
}
