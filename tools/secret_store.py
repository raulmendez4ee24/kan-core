from typing import Optional

from models import ApiIntegration
from tools.vault_client import read_secret, vault_enabled, write_secret


def _vault_path(*, client_id: str, integration_name: str) -> str:
    return f"clients/{client_id}/integrations/{integration_name}/api-key"


async def store_integration_secret(
    integration: ApiIntegration,
    *,
    client_id: str,
    integration_name: str,
    secret: str,
) -> str:
    if vault_enabled():
        path = _vault_path(client_id=client_id, integration_name=integration_name)
        if await write_secret(path, secret):
            metadata = dict(integration.integration_metadata or {})
            metadata["secret_backend"] = "vault"
            metadata["secret_ref"] = path
            integration.integration_metadata = metadata
            integration.secret_encrypted = None
            return "vault"
    integration.set_secret(secret)
    metadata = dict(integration.integration_metadata or {})
    metadata["secret_backend"] = "database_encrypted"
    integration.integration_metadata = metadata
    return "database_encrypted"


async def load_integration_secret(
    integration: ApiIntegration,
    *,
    client_id: str,
) -> Optional[str]:
    metadata = integration.integration_metadata or {}
    if metadata.get("secret_backend") == "vault":
        ref = metadata.get("secret_ref")
        if isinstance(ref, str) and ref.strip():
            secret = await read_secret(ref)
            if secret:
                return secret
        path = _vault_path(client_id=client_id, integration_name=integration.name)
        return await read_secret(path)
    return integration.get_secret()
