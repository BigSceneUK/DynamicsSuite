import xml.etree.ElementTree as ET
import re
from typing import Optional, List, Dict

class QueryConverter:
    """Utility to convert between FetchXML and OData expressions."""
    
    # Mapping FetchXML operators to OData equivalents
    FETCH_TO_ODATA_OPS = {
        "eq": "eq",
        "neq": "ne",
        "ne": "ne",
        "gt": "gt",
        "ge": "ge",
        "lt": "lt",
        "le": "le",
        "null": "eq null",
        "not-null": "ne null",
        "like": "contains",      # Special handling needed
        "not-like": "not contains", # Special handling needed
        "in": "in",              # OData 4.0 support
        "between": None,         # No direct OData equivalent for 'between'
    }

    # Reverse mapping for OData to FetchXML
    ODATA_TO_FETCH_OPS = {
        "eq": "eq",
        "ne": "ne",
        "gt": "gt",
        "ge": "ge",
        "lt": "lt",
        "le": "le",
        "contains": "like",
        "startswith": "like",
        "endswith": "like",
    }

    @staticmethod
    def fetch_xml_to_odata(fetch_xml: str) -> str:
        """
        Converts a FetchXML string to an OData $filter expression.
        Returns empty string if conversion is not possible or FetchXML is invalid.
        """
        try:
            if not fetch_xml or not fetch_xml.strip():
                return ""
            
            # Clean up XML string (remove potential leading/trailing whitespace or BOM)
            fetch_xml = fetch_xml.strip()
            if fetch_xml.startswith('\ufeff'):
                fetch_xml = fetch_xml[1:]
                
            root = ET.fromstring(fetch_xml)
        except Exception as e:
            return f"Error parsing FetchXML: {str(e)}"

        entity = root.find("entity")
        if entity is None:
            # Maybe it's a raw <entity> snippet
            if root.tag == "entity":
                entity = root
            # Maybe it's a raw <filter> snippet
            elif root.tag == "filter":
                return QueryConverter._parse_fetch_filter(root)
            else:
                return "No <entity> or <filter> found in FetchXML."

        filters = entity.findall("filter")
        if not filters:
            return ""

        parts = []
        for filter_node in filters:
            expr = QueryConverter._parse_fetch_filter(filter_node)
            if expr:
                parts.append(f"({expr})" if len(filters) > 1 else expr)

        return " and ".join(parts)

    @staticmethod
    def _parse_fetch_filter(filter_node: ET.Element) -> str:
        filter_type = filter_node.get("type", "and").lower()
        parts = []

        # Parse conditions
        for cond in filter_node.findall("condition"):
            attr = cond.get("attribute")
            op = cond.get("operator", "eq")
            
            # Values can be in 'value' attribute or nested <value> tags
            vals = []
            val_attr = cond.get("value")
            if val_attr:
                vals.append(val_attr)
            for v_tag in cond.findall("value"):
                if v_tag.text:
                    vals.append(v_tag.text)
            
            val = vals[0] if vals else None
            
            # Handle special cases
            if op == "like" and val:
                val_clean = val.replace("%", "")
                parts.append(f"contains({attr}, '{val_clean}')")
            elif op == "not-like" and val:
                val_clean = val.replace("%", "")
                parts.append(f"not contains({attr}, '{val_clean}')")
            elif op == "null":
                parts.append(f"{attr} eq null")
            elif op == "not-null":
                parts.append(f"{attr} ne null")
            elif op == "in" and vals:
                formatted_vals = []
                for v in vals:
                    # Basic type guessing
                    if v.isdigit() or v.lower() in ('true', 'false'):
                        formatted_vals.append(v)
                    else:
                        formatted_vals.append(f"'{v}'")
                parts.append(f"{attr} in ({', '.join(formatted_vals)})")
            else:
                odata_op = QueryConverter.FETCH_TO_ODATA_OPS.get(op, op)
                if val is not None:
                    if not val.isdigit() and val.lower() not in ("true", "false"):
                        val = f"'{val}'"
                    parts.append(f"{attr} {odata_op} {val}")
                else:
                    parts.append(f"{attr} {odata_op}")

        # Parse nested filters
        for nested in filter_node.findall("filter"):
            nested_expr = QueryConverter._parse_fetch_filter(nested)
            if nested_expr:
                parts.append(f"({nested_expr})")

        join_str = f" {filter_type} "
        return join_str.join(parts) if parts else ""

    @staticmethod
    def odata_to_fetch_xml(entity_name: str, odata_expression: str) -> str:
        """
        Converts an OData $filter expression to FetchXML.
        This is a heuristic-based converter for common patterns.
        """
        if not odata_expression or not odata_expression.strip():
            return f'<fetch>\n  <entity name="{entity_name}">\n  </entity>\n</fetch>'

        # Normalize and split by logic
        # Note: This doesn't handle nested parentheses well yet, but works for flat expressions
        
        fetch_root = ET.Element("fetch")
        entity_node = ET.SubElement(fetch_root, "entity", name=entity_name)
        filter_node = ET.SubElement(entity_node, "filter", type="and")

        # Simple split by 'and' for now. For 'or' we would need a proper parser.
        # We'll use a regex to find logical blocks but keep it simple.
        
        # Basic cleanup
        expr = odata_expression.strip()
        
        # Improved regex for OData parsing:
        # 1. Null check: attr eq null
        # 2. String functions: contains(attr, 'val')
        # 3. Basic triplet: attr eq val (handles quotes and unquoted values like @{variables(...)})
        # Note: We use a balanced-parentheses heuristic for Power Automate expressions
        pattern = (
            r"([\w/]+)\s+(eq|ne)\s+null|"
            r"(contains|startswith|endswith)\(([\w/]+),\s+['\"](.*?)['\"]\)|"
            r"([\w/]+)\s+(eq|ne|gt|ge|lt|le)\s+((?:'[^']*'|\"[^\"]*\"|@\{.*?\}|@[\w.]+\((?:[^()]*|\([^()]*\))*\)|[^\s\)]+))"
        )
        
        matches = re.finditer(pattern, expr, re.IGNORECASE)
        found = False
        for m in matches:
            found = True
            groups = m.groups()
            
            if groups[0]: # Null check (Group 1: attr, Group 2: op)
                attr, op = groups[0], groups[1]
                f_op = "null" if op.lower() == "eq" else "not-null"
                ET.SubElement(filter_node, "condition", attribute=attr, operator=f_op)
            elif groups[2]: # String function (Group 3: func, Group 4: attr, Group 5: val)
                func, attr, val = groups[2].lower(), groups[3], groups[4]
                f_val = val
                if func == "contains": f_val = f"%{val}%"
                elif func == "startswith": f_val = f"{val}%"
                elif func == "endswith": f_val = f"%{val}"
                ET.SubElement(filter_node, "condition", attribute=attr, operator="like", value=f_val)
            elif groups[5]: # Basic triplet (Group 6: attr, Group 7: op, Group 8: val)
                attr, op, val = groups[5], groups[6], groups[7]
                # Strip quotes if present
                if len(val) >= 2:
                    if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
                        val = val[1:-1]
                ET.SubElement(filter_node, "condition", attribute=attr, operator=op.lower(), value=val)

        if not found:
             # Fallback: if no structured matches, just put the whole thing in a comment
             filter_node.append(ET.Comment(f" Could not parse expression: {expr} "))

        from xml.dom import minidom
        rough_string = ET.tostring(fetch_root, "utf-8")
        reparsed = minidom.parseString(rough_string)
        xml_str = reparsed.toprettyxml(indent="  ")
        if xml_str.startswith("<?xml"):
            xml_str = xml_str.split("?>", 1)[-1].strip()
        return xml_str
