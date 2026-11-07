from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import networkx as nx
import requests
from mcp_server.auth import DynamicsAuthProvider

logger = logging.getLogger("mcp_schema_tools")


class SchemaTools:
    """Table relationship graph and component explorer tools."""

    def __init__(self, auth: DynamicsAuthProvider) -> None:
        self.auth = auth
        self._graph_cache: Dict[str, nx.MultiGraph] = {}

    def _headers(self, environment: Optional[str] = None, account: Optional[str] = None) -> Dict[str, str]:
        token = self.auth.get_token(environment, account=account)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "OData-MaxVersion": "4.0",
            "OData-Version": "4.0",
            "Prefer": 'odata.include-annotations="*"',
        }

    def _get(
        self,
        endpoint: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        env = self.auth.get_environment(environment)
        url = f"{env.url}/api/data/v9.2/{endpoint}" if not endpoint.startswith("http") else endpoint
        res = requests.get(url, headers=self._headers(environment, account=account), params=params, timeout=60)
        res.raise_for_status()
        return res.json()

    def _build_graph(self, environment: Optional[str] = None, account: Optional[str] = None) -> nx.MultiGraph:
        env_name = self.auth.get_environment(environment).name
        if env_name in self._graph_cache:
            return self._graph_cache[env_name]

        # Fetch One-To-Many & Many-To-Many
        o2m_url = "RelationshipDefinitions/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata?$select=SchemaName,ReferencingEntity,ReferencingAttribute,ReferencedEntity,ReferencedAttribute"
        m2m_url = "RelationshipDefinitions/Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata?$select=SchemaName,Entity1LogicalName,Entity1IntersectAttribute,Entity2LogicalName,Entity2IntersectAttribute,IntersectEntityName"

        G = nx.MultiGraph()

        try:
            res_o2m = self._get(o2m_url, environment=environment, account=account)
            for rel in res_o2m.get("value", []):
                src = rel.get("ReferencingEntity")
                tgt = rel.get("ReferencedEntity")
                if src and tgt:
                    G.add_edge(
                        src,
                        tgt,
                        schema_name=rel.get("SchemaName"),
                        rel_type="1:N",
                        referencing_attribute=rel.get("ReferencingAttribute"),
                        referenced_attribute=rel.get("ReferencedAttribute"),
                    )
        except Exception as exc:
            logger.warning("Failed fetching 1:N relationships: %s", exc)

        try:
            res_m2m = self._get(m2m_url, environment=environment, account=account)
            for rel in res_m2m.get("value", []):
                e1 = rel.get("Entity1LogicalName")
                e2 = rel.get("Entity2LogicalName")
                if e1 and e2:
                    G.add_edge(
                        e1,
                        e2,
                        schema_name=rel.get("SchemaName"),
                        rel_type="N:N",
                        intersect_entity=rel.get("IntersectEntityName"),
                    )
        except Exception as exc:
            logger.warning("Failed fetching N:N relationships: %s", exc)

        self._graph_cache[env_name] = G
        return G

    def find_relationship_path(
        self,
        source_entity: str,
        target_entity: str,
        max_hops: int = 3,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Find navigation paths and foreign keys between two Dataverse entities."""
        src = source_entity.strip().lower()
        tgt = target_entity.strip().lower()

        G = self._build_graph(environment=environment, account=account)
        if src not in G or tgt not in G:
            return {
                "error": f"One or both entities not found in relationships ({src}: {src in G}, {tgt}: {tgt in G}).",
                "source": src,
                "target": tgt,
            }

        paths_found = []
        try:
            for path in nx.all_simple_edge_paths(G, src, tgt, cutoff=max_hops):
                steps = []
                for u, v, k in path:
                    edge_data = G.get_edge_data(u, v, k)
                    steps.append({
                        "from_entity": u,
                        "to_entity": v,
                        "schema_name": edge_data.get("schema_name"),
                        "rel_type": edge_data.get("rel_type"),
                        "lookup_attribute": edge_data.get("referencing_attribute"),
                        "primary_key": edge_data.get("referenced_attribute"),
                        "intersect_entity": edge_data.get("intersect_entity"),
                    })
                paths_found.append(steps)
                if len(paths_found) >= 10:
                    break
        except Exception as exc:
            logger.warning("Error finding paths: %s", exc)

        return {
            "source_entity": src,
            "target_entity": tgt,
            "total_paths": len(paths_found),
            "paths": paths_found,
        }

    def get_table_components(
        self,
        entity_logical_name: str,
        environment: Optional[str] = None,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieve all forms, saved views, and business rules associated with an entity."""
        entity = entity_logical_name.strip().lower()
        res_forms: List[Dict[str, Any]] = []
        res_views: List[Dict[str, Any]] = []
        res_rules: List[Dict[str, Any]] = []

        # 1. Forms
        try:
            url = f"systemforms?$filter=objecttypecode eq '{entity}' and iscustomizable/Value eq true&$select=formid,name,type,formactivationstate&$orderby=name"
            r = self._get(url, environment=environment, account=account)
            type_names = {1: "Dashboard", 2: "Main", 5: "Mobile Express", 6: "Quick View", 7: "Quick Create"}
            for item in r.get("value", []):
                res_forms.append({
                    "form_id": item.get("formid"),
                    "name": item.get("name"),
                    "type": type_names.get(item.get("type"), f"Type {item.get('type')}"),
                })
        except Exception as exc:
            logger.warning("Failed fetching forms: %s", exc)

        # 2. Saved Views
        try:
            url = f"savedqueries?$filter=returnedtypecode eq '{entity}' and iscustomizable/Value eq true&$select=savedqueryid,name,querytype,isdefault&$orderby=name"
            r = self._get(url, environment=environment, account=account)
            for item in r.get("value", []):
                res_views.append({
                    "view_id": item.get("savedqueryid"),
                    "name": item.get("name"),
                    "is_default": item.get("isdefault", False),
                })
        except Exception as exc:
            logger.warning("Failed fetching views: %s", exc)

        # 3. Workflows & Business Rules
        try:
            url = f"workflows?$filter=primaryentity eq '{entity}' and category eq 2 and statecode eq 1&$select=workflowid,name,description&$orderby=name"
            r = self._get(url, environment=environment, account=account)
            for item in r.get("value", []):
                res_rules.append({
                    "rule_id": item.get("workflowid"),
                    "name": item.get("name"),
                    "description": item.get("description"),
                })
        except Exception as exc:
            logger.warning("Failed fetching business rules: %s", exc)

        return {
            "entity": entity,
            "total_forms": len(res_forms),
            "forms": res_forms,
            "total_views": len(res_views),
            "views": res_views,
            "total_business_rules": len(res_rules),
            "business_rules": res_rules,
        }

