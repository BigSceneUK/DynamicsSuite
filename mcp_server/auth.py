from __future__ import annotations

import json
import logging
import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import msal

logger = logging.getLogger("mcp_dynamics_auth")

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
SHARED_CACHE_PATH = Path(__file__).resolve().parent.parent / ".msal_token_cache.json"
SESSION_INFO_PATH = Path(__file__).resolve().parent.parent / ".dynamics_session.json"
SERVICE_ACCOUNTS_PATH = Path(__file__).resolve().parent.parent / ".service_accounts.json"

DEFAULT_PUBLIC_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"  # Azure CLI default
DEFAULT_IPC_PORT = 58999


@dataclass
class EnvironmentConfig:
    name: str
    url: str
    auth_type: str = "in_memory_ipc"  # 'in_memory_direct', 'in_memory_ipc', 'user_cache', or 'service_principal'
    tenant_id: str = "common"
    client_id: str = DEFAULT_PUBLIC_CLIENT_ID
    client_secret: Optional[str] = None


class DynamicsAuthProvider:
    """
    Manages multi-environment authentication for Dynamics 365 / Dataverse.

    Supports:
    1. In-memory direct AppContext (when running inside the DynamicsSuite UI plugin).
    2. Local IPC endpoint (http://127.0.0.1:<port>) with zero disk storage.
    3. Service Principal (client_id + client_secret) for target/service environments.
    4. Multi-environment configuration ('dev', 'uat', 'prod', 'target', etc.).
    """

    def __init__(self, config_dict: Optional[Dict[str, Any]] = None) -> None:
        self.raw_config = config_dict or self._load_config()
        self.environments: Dict[str, EnvironmentConfig] = {}
        self.default_env_name: str = "default"
        self._shared_cache: Optional[msal.SerializableTokenCache] = None
        self._active_ctx: Any = None
        self._parse_config()

    def set_active_context(self, ctx: Any) -> None:
        """Inject active in-memory AppContext from DynamicsSuite UI."""
        self._active_ctx = ctx
        self._parse_config()

    def _get_ipc_port(self) -> int:
        """Resolve the local IPC port from env, config, or default."""
        env_port = os.getenv("MCP_IPC_PORT")
        if env_port:
            try:
                return int(env_port)
            except ValueError:
                pass
        return int(self.raw_config.get("ipc_port", DEFAULT_IPC_PORT))

    def _query_ipc_endpoint(self, path: str, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """Query local IPC endpoint running inside DynamicsSuite UI plugin."""
        port = self._get_ipc_port()
        url = f"http://127.0.0.1:{port}{path}"
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "DynamicsSuite-MCP-Client"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        return None

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from config.json or environment variables / session info."""
        cfg: Dict[str, Any] = {}

        if CONFIG_PATH.is_file():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", CONFIG_PATH, exc)

        return cfg

    def _parse_config(self) -> None:
        """Parse raw config into structured EnvironmentConfig objects."""
        # 1. Check if multi-environment dict is defined in config.json
        if "environments" in self.raw_config and isinstance(self.raw_config["environments"], dict):
            for name, env_data in self.raw_config["environments"].items():
                if isinstance(env_data, dict) and env_data.get("url"):
                    self.environments[name] = EnvironmentConfig(
                        name=name,
                        url=str(env_data["url"]).rstrip("/"),
                        auth_type=env_data.get("auth_type", "user_cache"),
                        tenant_id=env_data.get("tenant_id", "common"),
                        client_id=env_data.get("client_id", DEFAULT_PUBLIC_CLIENT_ID),
                        client_secret=env_data.get("client_secret"),
                    )
            if self.raw_config.get("default_environment"):
                self.default_env_name = self.raw_config["default_environment"]
            elif self.environments:
                self.default_env_name = next(iter(self.environments.keys()))

        # 2. Check if a single flat environment is defined in config.json
        if not self.environments and self.raw_config.get("dataverse_url"):
            auth_type = "service_principal" if self.raw_config.get("client_secret") else "user_cache"
            self.environments["default"] = EnvironmentConfig(
                name="default",
                url=str(self.raw_config["dataverse_url"]).rstrip("/"),
                auth_type=auth_type,
                tenant_id=self.raw_config.get("tenant_id", "common"),
                client_id=self.raw_config.get("client_id", DEFAULT_PUBLIC_CLIENT_ID),
                client_secret=self.raw_config.get("client_secret"),
            )
            self.default_env_name = "default"

        # 3. Check environment variables
        env_url = os.getenv("DATAVERSE_URL")
        if env_url and "default" not in self.environments:
            secret = os.getenv("AZURE_CLIENT_SECRET")
            self.environments["default"] = EnvironmentConfig(
                name="default",
                url=env_url.rstrip("/"),
                auth_type="service_principal" if secret else "user_cache",
                tenant_id=os.getenv("AZURE_TENANT_ID", "common"),
                client_id=os.getenv("AZURE_CLIENT_ID", DEFAULT_PUBLIC_CLIENT_ID),
                client_secret=secret,
            )
            self.default_env_name = "default"

        # 4. Discover active session from in-memory context or local IPC plugin (Zero Disk Cache)
        if self._active_ctx and getattr(self._active_ctx, "org_url", None):
            self.environments["current_session"] = EnvironmentConfig(
                name="current_session",
                url=self._active_ctx.org_url.rstrip("/"),
                auth_type="in_memory_direct",
                tenant_id=getattr(self._active_ctx, "tenant_id", "common"),
                client_id=getattr(self._active_ctx, "client_id", DEFAULT_PUBLIC_CLIENT_ID),
            )
            self.default_env_name = "current_session"
        else:
            ipc_session = self._query_ipc_endpoint("/api/session")
            if ipc_session and ipc_session.get("is_logged_in") and ipc_session.get("org_url"):
                self.environments["current_session"] = EnvironmentConfig(
                    name="current_session",
                    url=str(ipc_session["org_url"]).rstrip("/"),
                    auth_type="in_memory_ipc",
                    tenant_id=ipc_session.get("tenant_id", "common"),
                    client_id=ipc_session.get("client_id", DEFAULT_PUBLIC_CLIENT_ID),
                )
                self.default_env_name = "current_session"
            elif SESSION_INFO_PATH.is_file():
                try:
                    session_data = json.loads(SESSION_INFO_PATH.read_text(encoding="utf-8"))
                    if session_data.get("org_url"):
                        self.environments["current_session"] = EnvironmentConfig(
                            name="current_session",
                            url=session_data["org_url"].rstrip("/"),
                            auth_type="user_cache",
                            tenant_id=session_data.get("tenant_id", "common"),
                            client_id=session_data.get("client_id", DEFAULT_PUBLIC_CLIENT_ID),
                        )
                        if not self.default_env_name or self.default_env_name == "default":
                            self.default_env_name = "current_session"
                except Exception as exc:
                    logger.warning("Failed to read session info: %s", exc)

        # 5. Load Service Principals from .service_accounts.json
        if SERVICE_ACCOUNTS_PATH.is_file():
            try:
                sa_data = json.loads(SERVICE_ACCOUNTS_PATH.read_text(encoding="utf-8"))
                if isinstance(sa_data, dict):
                    for name, item in sa_data.items():
                        if isinstance(item, dict) and item.get("url"):
                            self.environments[name] = EnvironmentConfig(
                                name=name,
                                url=str(item["url"]).rstrip("/"),
                                auth_type="service_principal",
                                tenant_id=item.get("tenant_id", "common"),
                                client_id=item.get("client_id", ""),
                                client_secret=item.get("client_secret", ""),
                            )
            except Exception as exc:
                logger.warning("Failed to load service accounts from %s: %s", SERVICE_ACCOUNTS_PATH, exc)

    def _get_shared_token_cache(self) -> msal.SerializableTokenCache:
        """Load or reload the shared MSAL token cache from DynamicsSuite."""
        cache = msal.SerializableTokenCache()
        if SHARED_CACHE_PATH.is_file():
            try:
                cache.deserialize(SHARED_CACHE_PATH.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to deserialize shared token cache: %s", exc)
        return cache

    def list_environments(self) -> List[Dict[str, Any]]:
        """Return a summary of all configured environments and their connection mode."""
        self._parse_config()
        session_data: Dict[str, Any] = {}
        if SESSION_INFO_PATH.is_file():
            try:
                session_data = json.loads(SESSION_INFO_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass

        result = []
        for name, env in self.environments.items():
            item: Dict[str, Any] = {
                "name": name,
                "url": env.url,
                "auth_type": env.auth_type,
                "is_default": (name == self.default_env_name),
            }
            if name == "current_session" and session_data:
                item["environment_display_name"] = session_data.get("environment_name")
                item["active_user_email"] = session_data.get("user_email")
            result.append(item)
        return result

    def get_active_session_info(self) -> Dict[str, Any]:
        """Dynamically check the active session from DynamicsSuite and return status."""
        self._parse_config()
        workspace_dir = str(Path(__file__).resolve().parent.parent)
        session_info: Dict[str, Any] = {
            "is_logged_in": False,
            "environment_name": None,
            "org_url": None,
            "user_email": None,
            "user_display_name": None,
            "workspace_path": workspace_dir,
            "auth_mode": None,
            "token_cache_present": SHARED_CACHE_PATH.is_file(),
            "configured_environments_count": len(self.environments),
            "ipc_port": self._get_ipc_port(),
        }

        # 1. Direct in-memory context (SSE Plugin mode)
        if self._active_ctx and getattr(self._active_ctx, "org_url", None):
            session_info.update({
                "is_logged_in": True,
                "auth_mode": "in_memory_direct",
                "environment_name": getattr(self._active_ctx, "environment_name", "Unknown"),
                "org_url": self._active_ctx.org_url,
                "user_email": getattr(self._active_ctx, "user_email", None),
                "user_display_name": getattr(self._active_ctx, "user_display_name", None),
                "tenant_id": getattr(self._active_ctx, "tenant_id", None),
                "client_id": getattr(self._active_ctx, "client_id", None),
            })
            session_info["available_environments"] = self.list_environments()
            return session_info

        # 2. Local IPC endpoint (Stdio mode)
        ipc_session = self._query_ipc_endpoint("/api/session")
        if ipc_session and ipc_session.get("is_logged_in"):
            session_info.update({
                "is_logged_in": True,
                "auth_mode": "in_memory_ipc",
                "environment_name": ipc_session.get("environment_name", "Unknown"),
                "org_url": ipc_session.get("org_url"),
                "user_email": ipc_session.get("user_email"),
                "user_display_name": ipc_session.get("user_display_name"),
                "tenant_id": ipc_session.get("tenant_id"),
                "client_id": ipc_session.get("client_id"),
            })
            session_info["available_environments"] = self.list_environments()
            return session_info

        # 3. Disk fallback (legacy/service principal)
        if SESSION_INFO_PATH.is_file():
            try:
                data = json.loads(SESSION_INFO_PATH.read_text(encoding="utf-8"))
                if data.get("org_url"):
                    session_info.update({
                        "is_logged_in": True,
                        "auth_mode": "user_cache",
                        "environment_name": data.get("environment_name", "Unknown"),
                        "org_url": data.get("org_url"),
                        "user_email": data.get("user_email"),
                        "user_display_name": data.get("user_display_name"),
                        "tenant_id": data.get("tenant_id"),
                        "client_id": data.get("client_id"),
                    })
            except Exception as e:
                logger.warning("Error reading active session: %s", e)

        session_info["available_environments"] = self.list_environments()
        return session_info

    def get_environment(self, env_name: Optional[str] = None) -> EnvironmentConfig:
        """Resolve environment by name or URL, or fall back to default."""
        self._parse_config()

        if not self.environments:
            raise ValueError(
                "No Dataverse environments configured. Please log into DynamicsSuite "
                "or configure environments in mcp_server/config.json."
            )

        if not env_name:
            if self.default_env_name in self.environments:
                return self.environments[self.default_env_name]
            return next(iter(self.environments.values()))

        # Match by name
        clean_name = env_name.strip().lower()
        for k, v in self.environments.items():
            if k.lower() == clean_name:
                return v

        # Match by URL or subdomain
        for v in self.environments.values():
            if clean_name in v.url.lower():
                return v

        # If user passed an explicit https:// url directly
        if env_name.startswith("http://") or env_name.startswith("https://"):
            return EnvironmentConfig(
                name="adhoc",
                url=env_name.rstrip("/"),
                auth_type="user_cache",
            )

        raise ValueError(
            f"Environment '{env_name}' not found. Available environments: {list(self.environments.keys())}"
        )

    def list_accounts(self) -> List[Dict[str, Any]]:
        """Return all user accounts and service principals with their recommended usage."""
        accounts = []
        active_email = None

        # 1. Direct in-memory AppContext
        if self._active_ctx and hasattr(self._active_ctx, "auth") and self._active_ctx.auth:
            accounts = self._active_ctx.auth.get_accounts()
            active_email = getattr(self._active_ctx, "user_email", None)
        else:
            # 2. Local IPC endpoint
            ipc_accounts = self._query_ipc_endpoint("/api/accounts")
            if ipc_accounts and "accounts" in ipc_accounts:
                accounts = ipc_accounts["accounts"]
                ipc_session = self._query_ipc_endpoint("/api/session")
                if ipc_session:
                    active_email = ipc_session.get("user_email")

        # 3. Disk fallback (if cache present)
        if not accounts and SHARED_CACHE_PATH.is_file():
            cache = self._get_shared_token_cache()
            app = msal.PublicClientApplication(
                client_id=DEFAULT_PUBLIC_CLIENT_ID,
                authority="https://login.microsoftonline.com/common",
                token_cache=cache,
            )
            accounts = app.get_accounts()
            if SESSION_INFO_PATH.is_file():
                try:
                    sdata = json.loads(SESSION_INFO_PATH.read_text(encoding="utf-8"))
                    active_email = sdata.get("user_email")
                except Exception:
                    pass

        result = []
        # 1. Human / Service identities from MSAL
        for a in accounts:
            u = a.get("username", "")
            u_low = u.lower()
            if "svc" in u_low or "service" in u_low:
                acc_type = "service_account"
                rec = "Automated deployments, solution imports, target environment transport, background service execution"
            elif (
                u_low.startswith("c-")
                or u_low.startswith("c_")
                or "admin" in u_low
                or (u_low.startswith("c") and "onmicrosoft.com" in u_low)
            ):
                acc_type = "c_account"
                rec = "Elevated Azure administration, Logic Apps, Function Apps, tenant management"
            else:
                acc_type = "standard_user"
                rec = "Dynamics 365 daily CRM operations, cases, reading forms, dev environment"

            result.append({
                "identifier": u,
                "name": a.get("name", "User"),
                "type": acc_type,
                "is_active": (u.lower() == active_email.lower()) if active_email else (len(result) == 0),
                "recommended_for": rec,
            })

        # 2. Service Principals from .service_accounts.json
        if SERVICE_ACCOUNTS_PATH.is_file():
            try:
                sa_data = json.loads(SERVICE_ACCOUNTS_PATH.read_text(encoding="utf-8"))
                if isinstance(sa_data, dict):
                    for s_name, s_item in sa_data.items():
                        result.append({
                            "identifier": s_name,
                            "name": s_item.get("description") or f"Service Account {s_name}",
                            "type": "service_principal",
                            "is_active": False,
                            "target_url": s_item.get("url"),
                            "recommended_for": "Automated deployments, solution import, target environment operations",
                        })
            except Exception:
                pass

        return result

    def get_token(self, env_name: Optional[str] = None, account: Optional[str] = None) -> str:
        """
        Acquire a Bearer token for the given environment (or default).
        Handles both Service Principal (secret) and Shared MSAL Cache (user or C-account).
        """
        # If account parameter matches a known Service Principal name, use that environment
        if account and account.strip().lower() in self.environments:
            candidate_env = self.environments[account.strip().lower()]
            if candidate_env.auth_type == "service_principal":
                env = candidate_env
            else:
                env = self.get_environment(env_name)
        else:
            env = self.get_environment(env_name)

        scope = [f"{env.url}/.default"]
        authority = f"https://login.microsoftonline.com/{env.tenant_id}"

        # -------------------------------------------------------------
        # 1. Service Principal (Client Secret / Service Account)
        # -------------------------------------------------------------
        if env.auth_type == "service_principal" or env.client_secret:
            if not env.client_secret:
                raise ValueError(
                    f"Environment '{env.name}' is set to service_principal but 'client_secret' is missing."
                )
            app = msal.ConfidentialClientApplication(
                client_id=env.client_id,
                client_credential=env.client_secret,
                authority=authority,
            )
            result = app.acquire_token_silent(scope, account=None)
            if not result:
                result = app.acquire_token_for_client(scopes=scope)

            if result and "access_token" in result:
                return result["access_token"]
            err = result.get("error_description") or result.get("error") if result else "Unknown"
            raise RuntimeError(f"Service Principal auth failed for {env.url}: {err}")

        # -------------------------------------------------------------
        # 2. In-Memory Direct Context (SSE / Plugin Mode)
        # -------------------------------------------------------------
        if self._active_ctx and hasattr(self._active_ctx, "auth") and self._active_ctx.auth:
            try:
                return self._active_ctx.auth.get_token(env.url, account=account)
            except Exception as direct_exc:
                logger.warning("Direct in-memory token retrieval failed: %s", direct_exc)

        # -------------------------------------------------------------
        # 3. Local In-Memory IPC Endpoint (Stdio Mode on http://127.0.0.1:<port>)
        # -------------------------------------------------------------
        ipc_port = self._get_ipc_port()
        query_params = urllib.parse.urlencode({"resource": env.url, "account": account or ""})
        ipc_token_resp = self._query_ipc_endpoint(f"/api/token?{query_params}")
        if ipc_token_resp and "access_token" in ipc_token_resp:
            return ipc_token_resp["access_token"]
        elif ipc_token_resp and "error" in ipc_token_resp:
            logger.warning("IPC token endpoint error: %s", ipc_token_resp["error"])

        # -------------------------------------------------------------
        # 4. Fallback: Shared MSAL Cache on Disk (if present)
        # -------------------------------------------------------------
        if SHARED_CACHE_PATH.is_file():
            cache = self._get_shared_token_cache()
            app = msal.PublicClientApplication(
                client_id=env.client_id,
                authority=authority,
                token_cache=cache,
            )

            accounts = app.get_accounts()
            if accounts:
                target_account = None
                if account:
                    acc_clean = account.strip().lower()
                    if acc_clean in ("service_account", "svc_account", "svc", "service"):
                        for a in accounts:
                            ul = a.get("username", "").lower()
                            if "svc" in ul or "service" in ul:
                                target_account = a
                                break
                    elif acc_clean in ("c_account", "c-account", "caccount", "admin"):
                        for a in accounts:
                            ul = a.get("username", "").lower()
                            if ("c-" in ul or "admin" in ul or (ul.startswith("c") and "onmicrosoft.com" in ul)) and "svc" not in ul:
                                target_account = a
                                break
                    elif acc_clean in ("standard_user", "user", "normal"):
                        for a in accounts:
                            ul = a.get("username", "").lower()
                            if not ul.startswith("c") and "svc" not in ul and "admin" not in ul:
                                target_account = a
                                break
                    else:
                        for a in accounts:
                            if acc_clean in a.get("username", "").lower() or acc_clean == a.get("home_account_id"):
                                target_account = a
                                break

                if not target_account:
                    active_email = None
                    if SESSION_INFO_PATH.is_file():
                        try:
                            sdata = json.loads(SESSION_INFO_PATH.read_text(encoding="utf-8"))
                            active_email = sdata.get("user_email")
                        except Exception:
                            pass
                    if active_email:
                        for a in accounts:
                            if a.get("username", "").lower() == active_email.lower():
                                target_account = a
                                break
                    if not target_account:
                        target_account = accounts[0]

                # Silent acquisition with cached refresh token
                result = app.acquire_token_silent(scope, account=target_account)
                if result and "access_token" in result:
                    return result["access_token"]

        # -------------------------------------------------------------
        # 5. No session found — prompt user to start UI
        # -------------------------------------------------------------
        raise RuntimeError(
            f"No active in-memory session found for '{env.name}'. "
            f"Please sign into DynamicsSuite in your browser and ensure the MCP Server plugin is running on port {ipc_port}, "
            "or configure a service_principal in mcp_server/config.json."
        )

