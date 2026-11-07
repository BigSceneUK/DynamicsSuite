from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("service_accounts")

SERVICE_ACCOUNTS_FILE = Path(__file__).resolve().parent.parent / ".service_accounts.json"


def load_service_accounts() -> Dict[str, Dict[str, Any]]:
    """Load all configured Service Principals from .service_accounts.json."""
    if not SERVICE_ACCOUNTS_FILE.is_file():
        return {}
    try:
        content = SERVICE_ACCOUNTS_FILE.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("Failed to load %s: %s", SERVICE_ACCOUNTS_FILE, exc)
    return {}


def save_service_account(
    name: str,
    url: str,
    tenant_id: str,
    client_id: str,
    client_secret: str,
    description: str = "",
) -> None:
    """Save or update a named Service Principal profile."""
    accounts = load_service_accounts()
    clean_name = name.strip().lower()
    accounts[clean_name] = {
        "name": clean_name,
        "url": url.strip().rstrip("/"),
        "tenant_id": tenant_id.strip(),
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
        "description": description.strip(),
    }
    SERVICE_ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2), encoding="utf-8")


def delete_service_account(name: str) -> bool:
    """Delete a named Service Principal profile."""
    accounts = load_service_accounts()
    clean_name = name.strip().lower()
    if clean_name in accounts:
        del accounts[clean_name]
        SERVICE_ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2), encoding="utf-8")
        return True
    return False


def get_service_account(name: str) -> Optional[Dict[str, Any]]:
    """Get a specific Service Principal profile by name."""
    accounts = load_service_accounts()
    return accounts.get(name.strip().lower())

