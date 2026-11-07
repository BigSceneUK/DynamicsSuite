from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional
import urllib.parse

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from core.context import AppContext

logger = logging.getLogger("mcp_server_plugin.service")

# Global singleton so Streamlit re-renders share the same server instance
_SERVER_INSTANCE: Optional[EmbeddedMCPServer] = None
_SERVER_LOCK = threading.Lock()


class EmbeddedMCPServer:
    """
    Manages the embedded MCP & in-memory IPC server in a background daemon thread.
    Zero tokens or session secrets are ever written to disk.
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[uvicorn.Server] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._port: int = 58999
        self._ctx: Optional[AppContext] = None
        self._start_time: Optional[float] = None
        self._lock = threading.RLock()

    def set_context(self, ctx: AppContext) -> None:
        """Update the active in-memory session context."""
        with self._lock:
            self._ctx = ctx
            try:
                # Also inject context directly into mcp_server.server
                import mcp_server.server as srv
                if hasattr(srv, "set_active_context"):
                    srv.set_active_context(ctx)
            except Exception as exc:
                logger.debug("Could not notify mcp_server.server of context: %s", exc)

    def is_running(self) -> bool:
        """Check if background uvicorn server thread is alive and serving."""
        return (
            self._thread is not None
            and self._thread.is_alive()
            and self._server is not None
            and getattr(self._server, "started", False)
            and not getattr(self._server, "should_exit", False)
        )

    def get_port(self) -> int:
        return self._port

    def get_status(self) -> Dict[str, Any]:
        """Return real-time diagnostic status of the embedded service."""
        running = self.is_running()
        uptime_seconds = int(time.time() - self._start_time) if (running and self._start_time) else 0

        user_email = None
        user_name = None
        org_url = None
        env_name = None
        if self._ctx:
            user_email = self._ctx.user_email
            user_name = self._ctx.user_display_name
            org_url = self._ctx.org_url
            env_name = self._ctx.environment_name

        return {
            "status": "running" if running else "stopped",
            "is_running": running,
            "port": self._port,
            "uptime_seconds": uptime_seconds,
            "user_email": user_email,
            "user_display_name": user_name,
            "org_url": org_url,
            "environment_name": env_name,
            "storage_mode": "in_memory_only",
        }

    def start(self, port: int, ctx: AppContext) -> bool:
        """Start the MCP and in-memory IPC server on 127.0.0.1:<port>."""
        with self._lock:
            self._ctx = ctx

            # If already running on the same port, keep it alive and update context
            if self.is_running():
                if self._port == port:
                    self.set_context(ctx)
                    return True
                # Different port: restart
                self._stop_internal()

            self._port = port
            self.set_context(ctx)

            try:
                # Import MCP Server instance
                from mcp_server.server import app as mcp_app
                # Build base Starlette SSE app
                starlette_app = mcp_app.sse_app(sse_path="/sse", message_path="/messages/")

                # Add IPC helper routes to the Starlette app
                self._register_ipc_routes(starlette_app)

                config = uvicorn.Config(
                    starlette_app,
                    host="127.0.0.1",
                    port=self._port,
                    log_level="warning",
                    access_log=False,
                )
                self._server = uvicorn.Server(config)

                # Launch in daemon thread
                def _run() -> None:
                    try:
                        self._loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(self._loop)
                        self._start_time = time.time()
                        self._loop.run_until_complete(self._server.serve())
                    except Exception as e:
                        logger.error("Error in MCP background server: %s", e)
                    finally:
                        self._start_time = None

                self._thread = threading.Thread(target=_run, name=f"MCPServerThread-{port}", daemon=True)
                self._thread.start()

                # Wait briefly for thread to bind and start
                for _ in range(30):
                    if self.is_running():
                        break
                    time.sleep(0.05)

                logger.info("Embedded MCP server started on http://127.0.0.1:%d", self._port)
                return True
            except Exception as exc:
                logger.exception("Failed to start embedded MCP server on port %d: %s", port, exc)
                return False

    def stop(self) -> bool:
        """Stop the background server thread."""
        with self._lock:
            return self._stop_internal()

    def _stop_internal(self) -> bool:
        if self._server:
            self._server.should_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

        self._server = None
        self._thread = None
        self._loop = None
        self._start_time = None
        logger.info("Embedded MCP server stopped")
        return True

    def _register_ipc_routes(self, app: Starlette) -> None:
        """Register custom IPC endpoints for stdio clients and health checks."""

        async def handle_status(request):
            return JSONResponse(self.get_status())

        async def handle_session(request):
            ctx = self._ctx
            session_data = {
                "is_logged_in": bool(ctx and ctx.org_url),
                "auth_mode": "in_memory_ipc",
                "environment_name": ctx.environment_name if ctx else "Unknown",
                "org_url": ctx.org_url if ctx else None,
                "user_email": ctx.user_email if ctx else None,
                "user_display_name": ctx.user_display_name if ctx else None,
                "tenant_id": ctx.tenant_id if ctx else None,
                "client_id": ctx.client_id if ctx else None,
                "port": self._port,
                "storage": "in_memory_only",
            }
            return JSONResponse(session_data)

        async def handle_token(request):
            if not self._ctx or not self._ctx.auth:
                return JSONResponse({"error": "No active user session in DynamicsSuite UI"}, status_code=401)

            resource = request.query_params.get("resource")
            account = request.query_params.get("account")

            if not resource:
                resource = self._ctx.org_url
            if not resource:
                return JSONResponse({"error": "Resource URL not specified and no default org URL available"}, status_code=400)

            try:
                # Retrieve token from in-memory MSAL cache
                token = self._ctx.auth.get_token(resource, account=account)
                return JSONResponse({
                    "access_token": token,
                    "resource": resource,
                    "account": account or self._ctx.user_email,
                })
            except Exception as exc:
                logger.warning("Token acquisition error in IPC endpoint: %s", exc)
                return JSONResponse({"error": str(exc)}, status_code=500)

        async def handle_accounts(request):
            if not self._ctx or not self._ctx.auth:
                return JSONResponse({"accounts": []})
            try:
                accs = self._ctx.auth.get_accounts()
                return JSONResponse({"accounts": accs})
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)

        app.routes.append(Route("/api/status", handle_status, methods=["GET"]))
        app.routes.append(Route("/api/session", handle_session, methods=["GET"]))
        app.routes.append(Route("/api/token", handle_token, methods=["GET"]))
        app.routes.append(Route("/api/accounts", handle_accounts, methods=["GET"]))


def get_embedded_mcp_server() -> EmbeddedMCPServer:
    """Return the global singleton instance of the EmbeddedMCPServer."""
    global _SERVER_INSTANCE
    with _SERVER_LOCK:
        if _SERVER_INSTANCE is None:
            _SERVER_INSTANCE = EmbeddedMCPServer()
        return _SERVER_INSTANCE
