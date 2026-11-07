from __future__ import annotations

import logging
from pathlib import Path
import msal

CACHE_PATH = Path(__file__).resolve().parent.parent / ".msal_token_cache.json"


class GraphAuth:
    """
    MSAL-based authentication for interactive user credentials.

    Azure CLI login is intentionally NOT supported — certain Dataverse and Graph
    delegated permissions only work with interactive user tokens.
    """

    def __init__(self, client_id: str, tenant_id: str = "common",
                 token_cache: msal.TokenCache = None,
                 persist_cache: bool = False):
        self.client_id = client_id
        self.tenant_id = tenant_id or "common"
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.persist_cache = persist_cache
        self._cache = token_cache or (msal.SerializableTokenCache() if persist_cache else msal.TokenCache())

        if self.persist_cache and CACHE_PATH.is_file():
            try:
                if hasattr(self._cache, "deserialize"):
                    self._cache.deserialize(CACHE_PATH.read_text(encoding="utf-8"))
            except Exception as exc:
                logging.warning("Failed to deserialize token cache from %s: %s", CACHE_PATH, exc)

        self._active_account: dict | None = None

        self.app = msal.PublicClientApplication(
            client_id=self.client_id,
            authority=self.authority,
            token_cache=self._cache,
        )

    def _save_cache(self) -> None:
        """Persist updated token cache to disk only if persist_cache is True."""
        if not self.persist_cache:
            return
        try:
            if hasattr(self._cache, "has_state_changed") and getattr(self._cache, "has_state_changed", False):
                CACHE_PATH.write_text(self._cache.serialize(), encoding="utf-8")
                self._cache.has_state_changed = False
        except Exception as exc:
            logging.warning("Failed to persist token cache to %s: %s", CACHE_PATH, exc)

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
            self._save_cache()
            return result

        error = result.get("error_description") or result.get("error", "Unknown error")
        raise Exception(f"Interactive login failed: {error}")

    def logout(self) -> None:
        """Remove all cached accounts and tokens."""
        for account in self.app.get_accounts():
            self.app.remove_account(account)
        self._save_cache()
        if CACHE_PATH.is_file():
            try:
                CACHE_PATH.unlink(missing_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Multi-account management
    # ------------------------------------------------------------------

    def get_accounts(self) -> list[dict]:
        """Return all accounts present in the token cache."""
        return self.app.get_accounts()

    def get_current_account(self) -> dict | None:
        """Return the currently active account, or the first account, or None."""
        if self._active_account:
            return self._active_account
        accounts = self.app.get_accounts()
        if accounts:
            self._active_account = accounts[0]
            return accounts[0]
        return None

    def set_active_account(self, username_or_id: str) -> bool:
        """Set the active account by username or home_account_id."""
        for acc in self.app.get_accounts():
            if acc.get("username", "").lower() == username_or_id.lower() or acc.get("home_account_id") == username_or_id:
                self._active_account = acc
                return True
        return False

    def resolve_account(self, account: str | dict | None = None) -> dict | None:
        """Resolve an account dict from an explicit username/dict or the active account."""
        if isinstance(account, dict):
            return account
        if isinstance(account, str) and account:
            for acc in self.app.get_accounts():
                if acc.get("username", "").lower() == account.lower() or acc.get("home_account_id") == account:
                    return acc
        return self.get_current_account()

    def login_additional_account(self, scopes: list[str]) -> dict:
        """
        Interactively sign in with an additional account (e.g. C-Account, second tenant).
        Adds the account to the shared cache without logging out existing accounts.
        """
        result = self.app.acquire_token_interactive(scopes=scopes, prompt="select_account")
        if "access_token" in result:
            self._save_cache()
            accounts = self.app.get_accounts()
            if accounts:
                self._active_account = accounts[-1]
            return result
        error = result.get("error_description") or result.get("error", "Login failed")
        raise Exception(f"Failed to add account: {error}")

    def remove_account(self, username_or_id: str) -> None:
        """Remove a single account from the cache."""
        target = None
        for acc in self.app.get_accounts():
            if acc.get("username", "").lower() == username_or_id.lower() or acc.get("home_account_id") == username_or_id:
                target = acc
                break
        if target:
            self.app.remove_account(target)
            if self._active_account and (self._active_account.get("home_account_id") == target.get("home_account_id")):
                remaining = self.app.get_accounts()
                self._active_account = remaining[0] if remaining else None
            self._save_cache()

    # ------------------------------------------------------------------
    # Token acquisition helpers (for plugins)
    # ------------------------------------------------------------------

    def get_token(self, resource_url: str, account: str | dict | None = None) -> str:
        """
        Return a bearer token string for the given resource URL.
        Optionally takes a specific account to use.
        """
        scope = f"{resource_url.rstrip('/')}/.default"
        acc = self.resolve_account(account)
        if not acc:
            raise Exception("No signed-in account. Please log in again.")

        result = self.app.acquire_token_silent([scope], account=acc)
        if result and "access_token" in result:
            self._save_cache()
            return result["access_token"]

        error = (result or {}).get("error_description", "Silent token acquisition failed")
        raise Exception(f"Could not acquire token for {resource_url}: {error}")

    def acquire_token_for_resource(self, resource_url: str, account: str | dict | None = None) -> str:
        """
        Acquire a bearer token for a specific resource (e.g. Dataverse).
        Tries silent acquisition first; if not consented yet, opens browser popup for incremental consent.
        """
        scope = f"{resource_url.rstrip('/')}/.default"
        acc = self.resolve_account(account)
        if not acc:
            raise Exception("No signed-in account. Please log in first.")

        result = self.app.acquire_token_silent([scope], account=acc)
        if result and "access_token" in result:
            self._save_cache()
            return result["access_token"]

        # Silent failed — open interactive popup for incremental consent with the specific account
        result = self.app.acquire_token_interactive(scopes=[scope], account=acc)
        if result and "access_token" in result:
            self._save_cache()
            return result["access_token"]

        error = (result or {}).get("error_description", "Token acquisition failed")
        raise Exception(f"Could not acquire token for {resource_url}: {error}")

    def acquire_token_silent(self, scopes: list[str], account: str | dict | None = None) -> dict | None:
        """
        Attempt silent acquisition for specific scopes and account.
        """
        acc = self.resolve_account(account)
        if not acc:
            return None
        return self.app.acquire_token_silent(scopes, account=acc)
