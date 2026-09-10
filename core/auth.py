from __future__ import annotations

import msal
import logging


class GraphAuth:
    """
    MSAL-based authentication for interactive user credentials.

    Azure CLI login is intentionally NOT supported — certain Dataverse and Graph
    delegated permissions only work with interactive user tokens.
    """

    def __init__(self, client_id: str, tenant_id: str = "common",
                 token_cache: msal.SerializableTokenCache = None):
        self.client_id = client_id
        self.tenant_id = tenant_id or "common"
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self._cache = token_cache or msal.SerializableTokenCache()

        self.app = msal.PublicClientApplication(
            client_id=self.client_id,
            authority=self.authority,
            token_cache=self._cache,
        )

    # ------------------------------------------------------------------
    # Login / Logout
    # ------------------------------------------------------------------

    def login(self, scopes: list[str], force_select_account: bool = False) -> dict:
        """
        Acquire a token interactively (browser popup).

        Returns the full MSAL result dict containing 'access_token'.
        Raises Exception on failure.
        """
        result = None

        if not force_select_account:
            accounts = self.app.get_accounts()
            if accounts:
                try:
                    result = self.app.acquire_token_silent(scopes, account=accounts[0])
                except Exception as exc:
                    logging.warning("Silent token acquisition failed: %s", exc)

        if not result:
            prompt = "select_account" if force_select_account else "select_account"
            result = self.app.acquire_token_interactive(scopes=scopes, prompt=prompt)

        if "access_token" in result:
            return result

        error = result.get("error_description") or result.get("error", "Unknown error")
        raise Exception(f"Interactive login failed: {error}")

    def logout(self) -> None:
        """Remove all cached accounts and tokens."""
        for account in self.app.get_accounts():
            self.app.remove_account(account)

    # ------------------------------------------------------------------
    # Token acquisition helpers (for plugins)
    # ------------------------------------------------------------------

    def get_token(self, resource_url: str) -> str:
        """
        Return a bearer token string for the given resource URL.

        Attempts silent acquisition first.  If that fails, raises an exception
        (the user needs to re-login — don't attempt interactive here so that
        plugins don't unexpectedly open browser tabs).
        """
        scope = f"{resource_url.rstrip('/')}/.default"
        accounts = self.app.get_accounts()
        if not accounts:
            raise Exception("No signed-in account. Please log in again.")

        result = self.app.acquire_token_silent([scope], account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

        error = (result or {}).get("error_description", "Silent token acquisition failed")
        raise Exception(f"Could not acquire token for {resource_url}: {error}")

    def acquire_token_for_resource(self, resource_url: str) -> str:
        """
        Acquire a bearer token for a specific resource (e.g. Dataverse).

        Tries silent acquisition first; if the resource has not been consented
        yet it falls back to an interactive browser popup so that the user can
        grant incremental consent.  Call this AFTER the initial Microsoft
        login — not as part of the first login call, because mixing
        `.default` with resource-specific scopes causes AADSTS70011.
        """
        scope = f"{resource_url.rstrip('/')}/.default"
        accounts = self.app.get_accounts()
        if not accounts:
            raise Exception("No signed-in account. Please log in first.")

        result = self.app.acquire_token_silent([scope], account=accounts[0])
        if result and "access_token" in result:
            return result["access_token"]

        # Silent failed — open a browser popup for incremental consent.
        result = self.app.acquire_token_interactive(scopes=[scope])
        if result and "access_token" in result:
            return result["access_token"]

        error = (result or {}).get("error_description", "Token acquisition failed")
        raise Exception(f"Could not acquire token for {resource_url}: {error}")

    def acquire_token_silent(self, scopes: list[str]) -> dict | None:
        """
        Attempt silent acquisition for specific scopes.

        Returns the full MSAL result dict or None if not available.
        """
        accounts = self.app.get_accounts()
        if not accounts:
            return None
        return self.app.acquire_token_silent(scopes, account=accounts[0])

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    def get_current_account(self) -> dict | None:
        """Return the first cached MSAL account dict, or None."""
        accounts = self.app.get_accounts()
        return accounts[0] if accounts else None
