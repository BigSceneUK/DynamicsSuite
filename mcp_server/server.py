#!/usr/bin/env python3
"""
Dynamics 365 Multi-Environment MCP Server
Exposes tools for AI assistants to inspect Dynamics 365 Cases, perform deep security audits,
trace automation dependencies, map table relationships, and diagnose Dataverse systems across
Dev, UAT, Prod, or Target environments using shared user sessions or Service Accounts.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer
from mcp_server.auth import DynamicsAuthProvider
from mcp_server.dataverse_client import DataverseCaseClient
from mcp_server.tools.security_tools import SecurityTools
from mcp_server.tools.schema_tools import SchemaTools
from mcp_server.tools.dependency_tools import DependencyTools
from mcp_server.tools.portal_tools import PortalTools

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("mcp_dynamics_server")

# Initialize MCP Server
app = MCPServer("dynamics-365-suite-assistant")

auth_provider: Optional[DynamicsAuthProvider] = None
dataverse_client: Optional[DataverseCaseClient] = None
security_tools: Optional[SecurityTools] = None
schema_tools: Optional[SchemaTools] = None
dependency_tools: Optional[DependencyTools] = None
portal_tools: Optional[PortalTools] = None
active_context: Any = None


def set_active_context(ctx: Any) -> None:
    """Set the active AppContext for in-memory direct execution (SSE plugin mode)."""
    global active_context, auth_provider, dataverse_client, security_tools, schema_tools, dependency_tools, portal_tools
    active_context = ctx
    if auth_provider is not None and hasattr(auth_provider, "set_active_context"):
        auth_provider.set_active_context(ctx)


def get_auth() -> DynamicsAuthProvider:
    global auth_provider
    if auth_provider is None:
        auth_provider = DynamicsAuthProvider()
        if active_context is not None and hasattr(auth_provider, "set_active_context"):
            auth_provider.set_active_context(active_context)
    return auth_provider


def get_client() -> DataverseCaseClient:
    global dataverse_client
    if dataverse_client is None:
        dataverse_client = DataverseCaseClient(auth_provider=get_auth())
    return dataverse_client


def get_security() -> SecurityTools:
    global security_tools
    if security_tools is None:
        security_tools = SecurityTools(auth=get_auth())
    return security_tools


def get_schema() -> SchemaTools:
    global schema_tools
    if schema_tools is None:
        schema_tools = SchemaTools(auth=get_auth())
    return schema_tools


def get_dependency() -> DependencyTools:
    global dependency_tools
    if dependency_tools is None:
        dependency_tools = DependencyTools(auth=get_auth())
    return dependency_tools


def get_portal() -> PortalTools:
    global portal_tools
    if portal_tools is None:
        portal_tools = PortalTools(auth=get_auth())
    return portal_tools


# ------------------------------------------------------------------------------
# 1. Environment & Account Identity Tools
# ------------------------------------------------------------------------------

@app.tool()
def get_active_session() -> str:
    """Check the live active Dynamics 365 session: returns whether a user is logged in via the DynamicsSuite UI, the environment name, organization URL, user email, and workspace directory."""
    try:
        session_info = get_client().get_active_session()
        return json.dumps(session_info, indent=2)
    except Exception as exc:
        logger.exception("Error in get_active_session")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def list_environments() -> str:
    """List all configured Dynamics 365 environments (e.g. 'dev', 'uat', 'prod', or active session) and their authentication mode."""
    try:
        envs = get_client().list_environments()
        return json.dumps(envs, indent=2)
    except Exception as exc:
        logger.exception("Error in list_environments")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def list_accounts() -> str:
    """List all connected user accounts (e.g. Standard User, C-Account / Cloud Admin, Service Account) stored in the shared DynamicsSuite session."""
    try:
        accs = get_client().list_accounts()
        return json.dumps(accs, indent=2)
    except Exception as exc:
        logger.exception("Error in list_accounts")
        return json.dumps({"error": str(exc)}, indent=2)


# ------------------------------------------------------------------------------
# 2. Case & Customer Correspondence Tools
# ------------------------------------------------------------------------------

@app.tool()
def get_case(
    identifier: str,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Fetch complete details of a Dynamics 365 case by ticket number (e.g. 'CAS-12345-XXXX') or GUID.

    Args:
        identifier: The case ticket number (e.g. 'CAS-01001-A1B2') or case GUID.
        environment: Optional environment name (e.g. 'dev', 'uat', 'target'). Defaults to primary environment.
        account: Optional account username or type ('c_account', 'standard_user', 'service_account'). Defaults to active account.
    """
    try:
        res = get_client().get_case(identifier, environment=environment, account=account)
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in get_case")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def search_cases(
    query: Optional[str] = None,
    status: Optional[str] = None,
    customer_name: Optional[str] = None,
    limit: int = 10,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Search Dynamics 365 cases by keyword, status ('Active', 'Resolved', 'Cancelled'), or customer name.

    Args:
        query: Search string to match in case title or ticket number.
        status: Filter by status ('Active', 'Resolved', 'Cancelled').
        customer_name: Partial name of customer (Account or Contact) to filter by.
        limit: Maximum number of cases to return (default 10, max 50).
        environment: Optional environment name (e.g. 'dev', 'uat'). Defaults to primary.
        account: Optional account username or type ('c_account', 'standard_user', 'service_account').
    """
    try:
        res = get_client().search_cases(
            query=query,
            status=status,
            customer_name=customer_name,
            limit=limit,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in search_cases")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def get_case_timeline(
    case_identifier: str,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Retrieve the full chronological activity timeline for a case, including Notes (annotations), Emails, Tasks, and Case Resolutions.

    Args:
        case_identifier: The ticket number or GUID of the case.
        environment: Optional environment name (e.g. 'dev', 'uat'). Defaults to primary.
        account: Optional account username or type ('c_account', 'standard_user', 'service_account').
    """
    try:
        res = get_client().get_case_timeline(case_identifier, environment=environment, account=account)
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in get_case_timeline")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def add_case_note(
    case_identifier: str,
    title: str,
    note_text: str,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Add a new timeline note (annotation) to an existing Dynamics 365 case.

    Args:
        case_identifier: The ticket number or GUID of the case.
        title: Short title/subject of the note.
        note_text: The full text content of the note.
        environment: Optional environment name (e.g. 'dev', 'uat'). Defaults to primary.
        account: Optional account username or type ('c_account', 'standard_user', 'service_account').
    """
    try:
        res = get_client().add_case_note(
            case_identifier=case_identifier,
            title=title,
            note_text=note_text,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in add_case_note")
        return json.dumps({"error": str(exc)}, indent=2)


# ------------------------------------------------------------------------------
# 3. Dataverse Query, Diagnostics & Metadata Tools
# ------------------------------------------------------------------------------

@app.tool()
def query_dataverse_table(
    table_name: str,
    filter_expr: Optional[str] = None,
    select_fields: Optional[str] = None,
    top: int = 10,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Execute an OData query on any Dataverse table (e.g. 'contacts', 'accounts', 'systemusers', 'incidents').

    Args:
        table_name: The entity set name in Dataverse (e.g., 'incidents', 'accounts', 'contacts').
        filter_expr: OData filter expression (e.g., "statecode eq 0").
        select_fields: Comma-separated list of attributes (e.g., "name,telephone1,emailaddress1").
        top: Number of records to return (default 10, max 100).
        environment: Optional environment name (e.g. 'dev', 'uat'). Defaults to primary.
        account: Optional account username or type ('c_account', 'standard_user', 'service_account').
    """
    try:
        res = get_client().query_table(
            table_name=table_name,
            filter_expr=filter_expr,
            select_fields=select_fields,
            top=top,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in query_dataverse_table")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def get_entity_metadata(
    entity_logical_name: str,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Fetch schema definition, display names, primary attributes, and field types for a Dataverse entity.

    Args:
        entity_logical_name: The entity logical name (e.g. 'contact', 'account', 'incident').
        environment: Optional environment name (e.g. 'dev', 'uat'). Defaults to primary.
        account: Optional account username or type.
    """
    try:
        res = get_client().get_entity_metadata(
            entity_logical_name=entity_logical_name,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in get_entity_metadata")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def execute_fetchxml(
    entity_set_name: str,
    fetch_xml: str,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Execute a raw FetchXML query directly against Dataverse and return formatted results.

    Args:
        entity_set_name: The entity set name (e.g. 'incidents', 'accounts', 'contacts').
        fetch_xml: The complete FetchXML query string.
        environment: Optional environment name (e.g. 'dev', 'uat'). Defaults to primary.
        account: Optional account username or type.
    """
    try:
        res = get_client().execute_fetchxml(
            entity_set_name=entity_set_name,
            fetch_xml=fetch_xml,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in execute_fetchxml")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def diagnose_record(
    entity_set_name: str,
    record_id: str,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Perform a comprehensive diagnostic trace on a Dataverse record: current values, field change audit history, related asynchronous system jobs, and plugin trace logs.

    Args:
        entity_set_name: The entity set name (e.g. 'incidents', 'accounts', 'contacts').
        record_id: The GUID of the record to diagnose.
        environment: Optional environment name (e.g. 'dev', 'uat'). Defaults to primary.
        account: Optional account username or type.
    """
    try:
        res = get_client().diagnose_record(
            entity_set_name=entity_set_name,
            record_id=record_id,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in diagnose_record")
        return json.dumps({"error": str(exc)}, indent=2)


# ------------------------------------------------------------------------------
# 4. Security & Access Inspection Tools
# ------------------------------------------------------------------------------

@app.tool()
def list_user_roles(
    user_email_or_id: str,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """List all direct security roles, teams, and team-inherited security roles assigned to a user in Dataverse.

    Args:
        user_email_or_id: Full name, email address, or systemuserid GUID of the user.
        environment: Optional environment name (e.g. 'dev', 'uat').
        account: Optional account to execute query with.
    """
    try:
        res = get_security().list_user_roles(
            user_email_or_id=user_email_or_id,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in list_user_roles")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def check_user_access(
    user_email_or_id: str,
    entity_logical_name: str,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Check effective entity-level privileges (Read, Write, Create, Delete, Append, AppendTo, Assign, Share) and depth for a user on a given Dataverse table.

    Args:
        user_email_or_id: User's email, username, or systemuserid GUID.
        entity_logical_name: Logical name of the table to check (e.g. 'contact', 'account', 'incident').
        environment: Optional environment name.
        account: Optional account to execute query with.
    """
    try:
        res = get_security().check_user_access(
            user_email_or_id=user_email_or_id,
            entity_logical_name=entity_logical_name,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in check_user_access")
        return json.dumps({"error": str(exc)}, indent=2)


# ------------------------------------------------------------------------------
# 5. Schema & Relationship Graph Tools
# ------------------------------------------------------------------------------

@app.tool()
def find_relationship_path(
    source_entity: str,
    target_entity: str,
    max_hops: int = 3,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Calculate relationship paths and foreign key navigation steps between two Dataverse tables using the entity relationship graph.

    Args:
        source_entity: Starting table logical name (e.g. 'incident', 'contact').
        target_entity: Destination table logical name (e.g. 'systemuser', 'account').
        max_hops: Maximum number of relationship hops (default 3).
        environment: Optional environment name.
        account: Optional account to execute query with.
    """
    try:
        res = get_schema().find_relationship_path(
            source_entity=source_entity,
            target_entity=target_entity,
            max_hops=max_hops,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in find_relationship_path")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def get_table_components(
    entity_logical_name: str,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Retrieve all Model-Driven App forms, saved system views, and active business rules associated with an entity.

    Args:
        entity_logical_name: Table logical name (e.g. 'contact', 'incident').
        environment: Optional environment name.
        account: Optional account to execute query with.
    """
    try:
        res = get_schema().get_table_components(
            entity_logical_name=entity_logical_name,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in get_table_components")
        return json.dumps({"error": str(exc)}, indent=2)


# ------------------------------------------------------------------------------
# 6. Automation Dependency & Flow Trigger Tools
# ------------------------------------------------------------------------------

@app.tool()
def list_entity_triggers(
    entity_logical_name: str,
    message: Optional[str] = None,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """List all Power Automate Cloud Flows that trigger on Create, Update, or Delete on an entity, including filter attributes and conditions.

    Args:
        entity_logical_name: Table logical name (e.g. 'account', 'incident', 'contact').
        message: Optional filter for event type ('Create', 'Update', 'Delete').
        environment: Optional environment name.
        account: Optional account to execute query with.
    """
    try:
        res = get_dependency().list_entity_triggers(
            entity_logical_name=entity_logical_name,
            message=message,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in list_entity_triggers")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def get_failed_flow_runs(
    environment: Optional[str] = None,
    time_window_hours: int = 24,
    account: Optional[str] = None,
) -> str:
    """Query recent failed Cloud Flow sessions and failed asynchronous system jobs in the environment.

    Args:
        environment: Optional environment name.
        time_window_hours: Search window in hours (default 24).
        account: Optional account to execute query with.
    """
    try:
        res = get_dependency().get_failed_flow_runs(
            environment=environment,
            time_window_hours=time_window_hours,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in get_failed_flow_runs")
        return json.dumps({"error": str(exc)}, indent=2)


# ------------------------------------------------------------------------------
# 7. Power Pages & Portal Security Tools
# ------------------------------------------------------------------------------

@app.tool()
def audit_portal_webapi(
    website_id: Optional[str] = None,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Audit Power Pages Web API site settings, exposed tables, and flag wildcard field exposure security risks.

    Args:
        website_id: Optional website GUID to restrict audit to a specific portal.
        environment: Optional environment name.
        account: Optional account to execute query with.
    """
    try:
        res = get_portal().audit_portal_webapi(
            website_id=website_id,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in audit_portal_webapi")
        return json.dumps({"error": str(exc)}, indent=2)


@app.tool()
def get_portal_page_hierarchy(
    webpage_id_or_url: str,
    environment: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """Inspect the complete component hierarchy of a Power Pages web page: page templates, basic forms, and multistep forms.

    Args:
        webpage_id_or_url: The partial URL (e.g. '/profile', '/apply') or webpage GUID.
        environment: Optional environment name.
        account: Optional account to execute query with.
    """
    try:
        res = get_portal().get_portal_page_hierarchy(
            webpage_id_or_url=webpage_id_or_url,
            environment=environment,
            account=account,
        )
        return json.dumps(res, indent=2)
    except Exception as exc:
        logger.exception("Error in get_portal_page_hierarchy")
        return json.dumps({"error": str(exc)}, indent=2)


# ------------------------------------------------------------------------------
# 8. Dynamic Plugin Tools Auto-Discovery Hook
# ------------------------------------------------------------------------------

try:
    from mcp_server.plugin_discovery import load_dynamic_plugin_tools
    load_dynamic_plugin_tools(app, get_auth())
except Exception as _exc:
    logger.warning("Could not run dynamic plugin auto-discovery: %s", _exc)


# ------------------------------------------------------------------------------
# Main Entry Point
# ------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Dynamics 365 Architecture & System Diagnostics MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Local IPC / SSE service port (default: from MCP_IPC_PORT or 58999)",
    )
    args = parser.parse_args()

    if args.port:
        os.environ["MCP_IPC_PORT"] = str(args.port)

    if args.transport == "sse":
        port = args.port or int(os.environ.get("MCP_IPC_PORT", 58999))
        app.run(transport="sse", port=port)
    else:
        app.run(transport="stdio")


if __name__ == "__main__":
    main()
