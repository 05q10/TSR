"""
tsr.py — Temporal Subgraph Retrieval (TSR) for Naval Digital Twin
==================================================================
Loads a naval knowledge-graph JSON, builds in-memory indexes, and exposes:

  Single-entity TSR
  -----------------
  tsr_location(entity_id, temporal_anchor)         → timeline list
  tsr(entity_id, temporal_anchor, query_type)      → timeline list (BFS)

  Fleet-level TSR
  ---------------
  tsr_mission_components_full(mission_id)          → hierarchy dict
  tsr_mo_inventory(mo_id)                          → list
  tsr_fleet_workshop_load()                        → sorted list
  tsr_fleet_workshop_ineffective(threshold=0.05)   → list
  tsr_fleet_in_workshop()                          → list
  tsr_fleet_degraded_systems()                     → list
  tsr_fleet_high_failure(min_failures=2)           → list
  tsr_fleet_redeployed()                           → list
  tsr_workshop_history(workshop_id)                → list
  tsr_ship_health(ship_id)                         → list

  LLM integration
  ---------------
  format_subgraph_for_llm(subgraph, query_type)    → str
  ask_llm(context_text, question)                  → str

  End-to-end pipeline
  -------------------
  query_pipeline(question, entity_id, query_type,
                 temporal_anchor, tsr_fn, **kwargs) → dict

Usage
-----
  from tsr import load_graph, query_pipeline
  load_graph("query_final.json")
  result = query_pipeline(
      question="Where was FS001 on April 20, 2026?",
      entity_id="ASSEMBLY_FS001",
      query_type="location",
      temporal_anchor="2026-04-20T00:00:00",
      tsr_fn="location",
  )
  print(result["llm_answer"])
"""

# ──────────────────────────────────────────────────────────────────────────────
# 1. Imports
# ──────────────────────────────────────────────────────────────────────────────

import json
import os
import re
from datetime import datetime

# Optional LLM client (Groq).  Import lazily so the module works without it.
try:
    from groq import Groq as _Groq
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False


# ──────────────────────────────────────────────────────────────────────────────
# 2. Global indexes (populated by load_graph)
# ──────────────────────────────────────────────────────────────────────────────

GRAPH      = {}          # raw JSON
NODE_INDEX = {}          # node_id → node dict
ALL_EDGES  = []          # flat list of all edges with edge_type attached
ADJ_OUT    = {}          # node_id → [outgoing edges]
ADJ_IN     = {}          # node_id → [incoming edges]


# ──────────────────────────────────────────────────────────────────────────────
# 3. Graph loading
# ──────────────────────────────────────────────────────────────────────────────

def load_graph(json_path: str) -> None:
    """
    Load the naval knowledge-graph JSON and build all in-memory indexes.
    Call this once before using any TSR function.
    """
    global GRAPH, NODE_INDEX, ALL_EDGES, ADJ_OUT, ADJ_IN

    with open(json_path, "r", encoding="utf-8") as f:
        GRAPH = json.load(f)

    # Node index
    NODE_INDEX = {}
    for node_type, type_data in GRAPH.get("node_types", {}).items():
        for node in type_data.get("nodes", []):
            NODE_INDEX[node["node_id"]] = node

    # Flat edge list with edge_type attached
    ALL_EDGES = []
    for edge_type, type_data in GRAPH.get("relationship_types", {}).items():
        for edge in type_data.get("edges", []):
            ALL_EDGES.append({
                "edge_type":  edge_type,
                "from":       edge["from"],
                "to":         edge["to"],
                "properties": edge.get("properties", {}),
            })

    # Adjacency indexes
    ADJ_OUT, ADJ_IN = {}, {}
    for edge in ALL_EDGES:
        ADJ_OUT.setdefault(edge["from"], []).append(edge)
        ADJ_IN .setdefault(edge["to"],   []).append(edge)

    print(f"[TSR] Loaded  nodes={len(NODE_INDEX)}  edges={len(ALL_EDGES)}")


# ──────────────────────────────────────────────────────────────────────────────
# 4. Low-level graph helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_node(node_id: str) -> dict | None:
    """Return the node dict for node_id, or None if not found."""
    return NODE_INDEX.get(node_id)


def get_node_type(node_id: str) -> str:
    """Return the type/label string of a node, or empty string."""
    node = NODE_INDEX.get(node_id)
    if not node:
        return ""
    return (
        node.get("node_type") or node.get("type") or
        node.get("label")     or node.get("kind") or ""
    )


def outgoing(node_id: str, edge_type: str | None = None) -> list:
    """All edges going OUT from node_id, optionally filtered by edge_type."""
    edges = ADJ_OUT.get(node_id, [])
    return [e for e in edges if e["edge_type"] == edge_type] if edge_type else edges


def incoming(node_id: str, edge_type: str | None = None) -> list:
    """All edges coming IN to node_id, optionally filtered by edge_type."""
    edges = ADJ_IN.get(node_id, [])
    return [e for e in edges if e["edge_type"] == edge_type] if edge_type else edges


# ──────────────────────────────────────────────────────────────────────────────
# 5. Datetime helpers
# ──────────────────────────────────────────────────────────────────────────────

def parse_dt(dt_str) -> datetime | None:
    """Parse an ISO-ish datetime string to a naïve datetime, or return None."""
    if not dt_str:
        return None
    try:
        return (
            datetime.fromisoformat(str(dt_str).replace("Z", ""))
            .replace(tzinfo=None)
        )
    except Exception:
        return None


def get_health_at(health_history: list, target_dt: datetime):
    """
    Return the most recent health value at or before target_dt from
    a health_history list.  Returns None if nothing qualifies.
    """
    if not health_history or not target_dt:
        return None
    best_val, best_time = None, None
    for entry in health_history:
        t = parse_dt(
            entry.get("timestamp") or entry.get("time") or
            entry.get("date")      or entry.get("recorded_at")
        )
        v = (
            entry.get("value")        or entry.get("health") or
            entry.get("health_index") or entry.get("health_value")
        )
        if t is None or v is None:
            continue
        if t <= target_dt and (best_time is None or t > best_time):
            best_time, best_val = t, v
    return best_val


def get_workshop_visits(assembly_id: str) -> list:
    """Return all ASSIGNED_TO-workshop edges for an assembly."""
    return [
        e for e in outgoing(assembly_id, "ASSIGNED_TO")
        if "WORKSHOP" in e.get("to", "")
    ]


# ──────────────────────────────────────────────────────────────────────────────
# 6. Core TSR: tsr_location — point-in-time location query
# ──────────────────────────────────────────────────────────────────────────────

_LOCATION_TYPES = {"Equipment", "Workshop", "MaterialOrganisation", "Base", "Dockyard"}

def tsr_location(entity_id: str, temporal_anchor) -> list:
    """
    Return the active location(s) of entity_id at temporal_anchor.

    Temporal rule:  from_time <= anchor < to_time
    Post-filter:    if any active Equipment installation is found, open
                    MaterialOrganisation assignments are suppressed
                    (assembly is physically on the ship, not in the store).

    Parameters
    ----------
    entity_id       : e.g. "ASSEMBLY_FS001"
    temporal_anchor : ISO datetime string or None (returns all)

    Returns
    -------
    List of location records sorted by timestamp ascending.
    Each record contains: timestamp, timestamp_str, to_time, edge_type,
    other_node, context, health_index, alpha, beta, node_type.
    """
    anchor_dt = parse_dt(temporal_anchor)
    timeline  = []

    for edge in outgoing(entity_id):
        if edge["edge_type"] not in ("INSTALLED_ON", "ASSIGNED_TO"):
            continue
        if get_node_type(edge["to"]) not in _LOCATION_TYPES:
            continue

        props = edge["properties"]

        if "installation_context" in props:
            for ctx in props["installation_context"]:
                ft = parse_dt(ctx.get("from_time"))
                tt = parse_dt(ctx.get("to_time"))
                if ft is None:
                    continue
                if anchor_dt:
                    if ft > anchor_dt:
                        continue
                    if tt is not None and tt <= anchor_dt:
                        continue
                timeline.append({
                    "timestamp":     ft,
                    "timestamp_str": ctx.get("from_time"),
                    "to_time":       ctx.get("to_time"),
                    "edge_type":     edge["edge_type"],
                    "other_node":    edge["to"],
                    "context":       ctx.get("context", ""),
                    "health_index":  ctx.get("health_index"),
                    "alpha":         ctx.get("alpha_param"),
                    "beta":          ctx.get("beta_param"),
                    "node_type":     get_node_type(edge["to"]),
                })

        elif "from_time" in props:
            ft = parse_dt(props.get("from_time"))
            tt = parse_dt(props.get("to_time"))
            if ft is None:
                continue
            if anchor_dt:
                if ft > anchor_dt:
                    continue
                if tt is not None and tt <= anchor_dt:
                    continue
            timeline.append({
                "timestamp":     ft,
                "timestamp_str": props.get("from_time"),
                "to_time":       props.get("to_time"),
                "edge_type":     edge["edge_type"],
                "other_node":    edge["to"],
                "context":       props.get("expert_notes", ""),
                "health_index":  None,
                "alpha":         None,
                "beta":          None,
                "node_type":     get_node_type(edge["to"]),
            })

    timeline.sort(key=lambda x: x["timestamp"] or datetime.max)

    # Post-filter: Equipment on-board supersedes open MO store assignment
    if anchor_dt and timeline:
        if any(e["node_type"] == "Equipment" for e in timeline):
            timeline = [e for e in timeline if e["node_type"] != "MaterialOrganisation"]

    return timeline


# ──────────────────────────────────────────────────────────────────────────────
# 7. Core TSR: tsr — BFS traversal for lifecycle / failure / causal / CF
# ──────────────────────────────────────────────────────────────────────────────

_EDGE_FILTER = {
    "location":       {"INSTALLED_ON", "ASSIGNED_TO"},
    "lifecycle":      None,   # all edge types
    "failure":        {"HAS_FUNCTION", "HAS_FAILURE", "HAS_EFFECT",
                       "HAS_CONSEQUENCE", "DETECTS_FAILURE"},
    "causal":         {"INSTALLED_ON", "DETECTS_FAILURE", "HAS_FUNCTION",
                       "HAS_FAILURE", "DECISION_TAKEN", "ASSIGNED_TO",
                       "HAS_EFFECT", "HAS_CONSEQUENCE"},
    "counterfactual": None,   # all edge types
}

def tsr(
    entity_id:       str,
    temporal_anchor  = None,
    query_type:      str = "lifecycle",
) -> list:
    """
    BFS-based Temporal Subgraph Retrieval.

    Traverses the graph breadth-first from entity_id, respecting:
      - edge-type filter per query_type
      - temporal filter (installation windows at anchor)

    Fixes multi-hop chains for failure/causal queries
    (e.g. Assembly→HAS_FUNCTION→Function→HAS_FAILURE→FailureMode
          ←DETECTS_FAILURE←Sensor).

    Parameters
    ----------
    entity_id       : starting node ID
    temporal_anchor : ISO datetime string or None
    query_type      : "lifecycle" | "location" | "failure" | "causal" |
                      "counterfactual"

    Returns
    -------
    List of event records sorted by timestamp ascending.
    Each record: timestamp, timestamp_str, to_time, edge_type, direction,
    from_node, to_node, other_node, context, health_index, alpha, beta,
    window_type.
    """
    anchor_dt = parse_dt(temporal_anchor)
    allowed   = _EDGE_FILTER.get(query_type)  # None means all

    # ── temporal gate ────────────────────────────────────────────────────────
    def _passes_temporal(edge: dict) -> bool:
        if not anchor_dt:
            return True
        props = edge["properties"]
        if "installation_context" in props:
            for ctx in props["installation_context"]:
                ft = parse_dt(ctx.get("from_time"))
                tt = parse_dt(ctx.get("to_time"))
                if ft is None:
                    continue
                if ft <= anchor_dt and (tt is None or anchor_dt <= tt):
                    return True
            return False
        ft = parse_dt(props.get("from_time"))
        tt = parse_dt(props.get("to_time"))
        if ft and ft > anchor_dt:
            return False
        if tt and tt < anchor_dt:
            return False
        return True

    # ── record builder ───────────────────────────────────────────────────────
    def _make_records(edge: dict, current_id: str) -> list:
        props     = edge["properties"]
        other     = edge["to"] if edge["from"] == current_id else edge["from"]
        direction = "OUT" if edge["from"] == current_id else "IN"

        if "installation_context" in props:
            records = []
            for ctx in props["installation_context"]:
                ft = parse_dt(ctx.get("from_time"))
                tt = parse_dt(ctx.get("to_time"))
                if ft is None:
                    continue
                if anchor_dt and not (ft <= anchor_dt and (tt is None or anchor_dt <= tt)):
                    continue
                records.append({
                    "timestamp":     ft,
                    "timestamp_str": ctx.get("from_time"),
                    "to_time":       ctx.get("to_time"),
                    "edge_type":     edge["edge_type"],
                    "direction":     direction,
                    "from_node":     edge["from"],
                    "to_node":       edge["to"],
                    "other_node":    other,
                    "context":       ctx.get("context", ""),
                    "health_index":  ctx.get("health_index"),
                    "alpha":         ctx.get("alpha_param"),
                    "beta":          ctx.get("beta_param"),
                    "window_type":   "installation_context",
                })
            return records

        if "from_time" in props:
            ft = parse_dt(props.get("from_time"))
            return [{
                "timestamp":     ft,
                "timestamp_str": props.get("from_time"),
                "to_time":       props.get("to_time"),
                "edge_type":     edge["edge_type"],
                "direction":     direction,
                "from_node":     edge["from"],
                "to_node":       edge["to"],
                "other_node":    other,
                "context":       props.get("expert_notes", ""),
                "health_index":  None,
                "alpha":         None,
                "beta":          None,
                "window_type":   "simple",
            }]

        # Static edges (no timestamps) — include for non-location queries
        if query_type in ("failure", "causal", "lifecycle", "counterfactual"):
            return [{
                "timestamp":     None,
                "timestamp_str": None,
                "to_time":       None,
                "edge_type":     edge["edge_type"],
                "direction":     direction,
                "from_node":     edge["from"],
                "to_node":       edge["to"],
                "other_node":    other,
                "context":       "",
                "health_index":  None,
                "alpha":         None,
                "beta":          None,
                "window_type":   "static",
            }]
        return []

    # ── BFS ──────────────────────────────────────────────────────────────────
    visited  = set()
    timeline = []
    queue    = [entity_id]

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for edge in outgoing(current) + incoming(current):
            if allowed and edge["edge_type"] not in allowed:
                continue
            if not _passes_temporal(edge):
                continue
            timeline.extend(_make_records(edge, current))
            neighbor = edge["to"] if edge["from"] == current else edge["from"]
            if neighbor not in visited:
                queue.append(neighbor)

    timeline.sort(key=lambda x: x["timestamp"] or datetime.max)
    return timeline


# ──────────────────────────────────────────────────────────────────────────────
# 8. Mission traversal — 5-hop hierarchy
# ──────────────────────────────────────────────────────────────────────────────

def tsr_mission_components_full(mission_id: str) -> dict:
    """
    5-hop traversal: Mission → CombatGroup → Ship → System → Equipment → Assembly.

    Returns a nested hierarchy dict with health and installation context for
    every assembly that was active during the mission window.
    """
    mission_node = get_node(mission_id)
    if not mission_node:
        return {}

    props         = mission_node["properties"]
    mission_start = parse_dt(props.get("start_date"))
    mission_end   = parse_dt(props.get("end_date"))

    hierarchy = {
        "mission_id":   mission_id,
        "mission_name": props.get("mission_name"),
        "start":        str(mission_start)[:10] if mission_start else None,
        "end":          str(mission_end)[:10]   if mission_end   else None,
        "ships":        {},
    }

    for cg_edge in incoming(mission_id, "ASSIGNED_TO"):
        cg_id = cg_edge["from"]
        for ship_edge in incoming(cg_id, "PART_OF"):
            ship_id   = ship_edge["from"]
            ship_node = get_node(ship_id)
            if not ship_node:
                continue
            hierarchy["ships"][ship_id] = {
                "name":    ship_node["properties"].get("name", ship_id),
                "systems": {},
            }
            for sys_edge in outgoing(ship_id, "HAS_SYSTEM"):
                sys_id   = sys_edge["to"]
                sys_node = get_node(sys_id)
                if not sys_node:
                    continue
                hierarchy["ships"][ship_id]["systems"][sys_id] = {
                    "name":      sys_node["properties"].get("system_name", sys_id),
                    "equipment": {},
                }
                for eq_edge in incoming(sys_id, "INSTALLED_ON"):
                    eq_id   = eq_edge["from"]
                    eq_node = get_node(eq_id)
                    if not eq_node or eq_node.get("type") != "Equipment":
                        continue
                    eq_props = eq_node["properties"]
                    hierarchy["ships"][ship_id]["systems"][sys_id]["equipment"][eq_id] = {
                        "type":       eq_props.get("equipment_type", ""),
                        "status":     eq_props.get("status", ""),
                        "assemblies": {},
                    }
                    for asm_edge in incoming(eq_id, "INSTALLED_ON"):
                        asm_id   = asm_edge["from"]
                        asm_node = get_node(asm_id)
                        if not asm_node or asm_node.get("type") != "Assembly":
                            continue
                        active_ctx = None
                        for ctx in asm_edge["properties"].get("installation_context", []):
                            ft = parse_dt(ctx.get("from_time"))
                            tt = parse_dt(ctx.get("to_time"))
                            if (ft and mission_end and ft <= mission_end and
                                    (tt is None or (mission_start and tt >= mission_start))):
                                active_ctx = ctx
                                break
                        if not active_ctx:
                            continue
                        asm_props = asm_node["properties"]
                        hierarchy["ships"][ship_id]["systems"][sys_id]\
                                 ["equipment"][eq_id]["assemblies"][asm_id] = {
                            "name":      asm_props.get("assembly_name", ""),
                            "health":    active_ctx.get("health_index", "N/A"),
                            "from_time": str(active_ctx.get("from_time", ""))[:10],
                            "to_time":   str(active_ctx.get("to_time", "present"))[:10],
                        }
    return hierarchy


# ──────────────────────────────────────────────────────────────────────────────
# 9. Fleet-level TSR handlers
# ──────────────────────────────────────────────────────────────────────────────

def tsr_mo_inventory(mo_id: str) -> list:
    """Return assemblies currently held in a material organisation (no to_time)."""
    return [
        {
            "assembly_id": edge["from"],
            "from_time":   edge["properties"].get("from_time"),
            "notes":       edge["properties"].get("expert_notes", ""),
        }
        for edge in incoming(mo_id, "ASSIGNED_TO")
        if not edge["properties"].get("to_time")
    ]


def tsr_fleet_workshop_load() -> list:
    """Return (workshop_id, visit_count) pairs sorted by busiest first."""
    counts: dict = {}
    for node_id in NODE_INDEX:
        if not node_id.startswith("ASSEMBLY_"):
            continue
        for edge in outgoing(node_id, "ASSIGNED_TO"):
            ws = edge["to"]
            if "WORKSHOP" in ws:
                counts[ws] = counts.get(ws, 0) + 1
    return sorted(counts.items(), key=lambda x: x[1], reverse=True)


def tsr_fleet_workshop_ineffective(threshold: float = 0.05) -> list:
    """
    Return assemblies with 2+ workshop visits where cumulative health
    improvement is ≤ threshold (default 0.05).

    Indicates workshop maintenance was ineffective — root cause is likely
    equipment-context incompatibility, not assembly defect.
    """
    results = []
    for node_id in NODE_INDEX:
        if not node_id.startswith("ASSEMBLY_"):
            continue
        visits = [
            e for e in outgoing(node_id, "ASSIGNED_TO")
            if "WORKSHOP" in e["to"]
        ]
        if len(visits) < 2:
            continue
        node   = get_node(node_id)
        h_hist = node["properties"].get("param_tracking", {}).get("health_history", [])
        if not h_hist:
            continue
        visit_times = sorted(
            [parse_dt(e["properties"].get("from_time")) for e in visits
             if parse_dt(e["properties"].get("from_time"))],
        )
        exit_times = sorted(
            [parse_dt(e["properties"].get("to_time")) for e in visits
             if parse_dt(e["properties"].get("to_time"))],
        )
        if not visit_times or not exit_times:
            continue
        h_start = get_health_at(h_hist, visit_times[0])
        h_end   = get_health_at(h_hist, exit_times[-1])
        if h_start is not None and h_end is not None:
            delta = h_end - h_start
            if delta <= threshold:
                results.append({
                    "assembly_id":  node_id,
                    "visits":       len(visits),
                    "health_start": round(h_start, 4),
                    "health_end":   round(h_end,   4),
                    "delta":        round(delta,    4),
                })
    return results


def tsr_fleet_in_workshop() -> list:
    """Return assemblies currently in a workshop (ASSIGNED_TO with no to_time)."""
    results = []
    for node_id in NODE_INDEX:
        if not node_id.startswith("ASSEMBLY_"):
            continue
        for edge in outgoing(node_id, "ASSIGNED_TO"):
            if "WORKSHOP" not in edge["to"]:
                continue
            if not edge["properties"].get("to_time"):
                results.append({
                    "assembly_id": node_id,
                    "workshop_id": edge["to"],
                    "since":       edge["properties"].get("from_time"),
                    "notes":       edge["properties"].get("expert_notes", ""),
                })
    return results


def tsr_fleet_degraded_systems(health_threshold: float = 0.80) -> list:
    """
    Return assemblies whose current health index is below health_threshold.
    Walks INSTALLED_ON edges for current (open to_time) deployments.
    """
    results = []
    for node_id in NODE_INDEX:
        if not node_id.startswith("ASSEMBLY_"):
            continue
        for edge in outgoing(node_id, "INSTALLED_ON"):
            if get_node_type(edge["to"]) != "Equipment":
                continue
            for ctx in edge["properties"].get("installation_context", []):
                if ctx.get("to_time"):
                    continue  # not current
                hi = ctx.get("health_index")
                if hi is not None and float(hi) < health_threshold:
                    results.append({
                        "assembly_id":  node_id,
                        "equipment_id": edge["to"],
                        "health_index": round(float(hi), 4),
                        "since":        ctx.get("from_time"),
                        "alpha":        ctx.get("alpha_param"),
                        "beta":         ctx.get("beta_param"),
                    })
    return sorted(results, key=lambda x: x["health_index"])


def tsr_fleet_high_failure(min_failures: int = 2) -> list:
    """
    Return assemblies where the number of recorded failure events ≥ min_failures.
    Counts INSTALLED_ON installation_context entries that have a 'failure' context tag.
    """
    results = []
    for node_id in NODE_INDEX:
        if not node_id.startswith("ASSEMBLY_"):
            continue
        failure_count = 0
        for edge in outgoing(node_id, "INSTALLED_ON"):
            for ctx in edge["properties"].get("installation_context", []):
                ctxt = ctx.get("context", "").lower()
                if "failure" in ctxt or "fail" in ctxt:
                    failure_count += 1
        # Also count HAS_FAILURE edges via BFS
        fm_edges = [
            e for e in tsr(node_id, query_type="failure")
            if e["edge_type"] == "HAS_FAILURE"
        ]
        failure_count = max(failure_count, len(fm_edges))
        if failure_count >= min_failures:
            results.append({
                "assembly_id":   node_id,
                "failure_count": failure_count,
            })
    return sorted(results, key=lambda x: x["failure_count"], reverse=True)


def tsr_fleet_redeployed() -> list:
    """
    Return assemblies that have been installed on more than one distinct
    equipment platform (i.e. cross-deployment / cannibalisation events).
    """
    results = []
    for node_id in NODE_INDEX:
        if not node_id.startswith("ASSEMBLY_"):
            continue
        platforms = set()
        for edge in outgoing(node_id, "INSTALLED_ON"):
            if get_node_type(edge["to"]) == "Equipment":
                platforms.add(edge["to"])
        if len(platforms) > 1:
            results.append({
                "assembly_id": node_id,
                "platforms":   sorted(platforms),
                "count":       len(platforms),
            })
    return results


def tsr_workshop_history(workshop_id: str) -> list:
    """Return all assembly visits to a workshop, sorted by start date."""
    results = []
    for edge in incoming(workshop_id, "ASSIGNED_TO"):
        props = edge["properties"]
        results.append({
            "assembly_id": edge["from"],
            "from_time":   props.get("from_time"),
            "to_time":     props.get("to_time"),
            "notes":       props.get("expert_notes", ""),
        })
    results.sort(key=lambda x: parse_dt(x["from_time"]) or datetime.max)
    return results


def tsr_ship_health(ship_id: str) -> list:
    """
    Return health summary of all assemblies currently installed on ship_id,
    traversing Ship→HAS_SYSTEM→System←INSTALLED_ON←Equipment←INSTALLED_ON←Assembly.
    """
    results = []
    for sys_edge in outgoing(ship_id, "HAS_SYSTEM"):
        sys_id = sys_edge["to"]
        for eq_edge in incoming(sys_id, "INSTALLED_ON"):
            eq_id = eq_edge["from"]
            if get_node_type(eq_id) != "Equipment":
                continue
            for asm_edge in incoming(eq_id, "INSTALLED_ON"):
                asm_id   = asm_edge["from"]
                asm_node = get_node(asm_id)
                if not asm_node or asm_node.get("type") != "Assembly":
                    continue
                # Find active context
                for ctx in asm_edge["properties"].get("installation_context", []):
                    if not ctx.get("to_time"):  # currently active
                        results.append({
                            "assembly_id":  asm_id,
                            "equipment_id": eq_id,
                            "system_id":    sys_id,
                            "health_index": ctx.get("health_index", "N/A"),
                            "since":        ctx.get("from_time"),
                            "beta":         ctx.get("beta_param"),
                        })
                        break
    return sorted(
        results,
        key=lambda x: float(x["health_index"]) if isinstance(x["health_index"], (int, float)) else 1.0
    )


# ──────────────────────────────────────────────────────────────────────────────
# 10. Subgraph → LLM context formatter
# ──────────────────────────────────────────────────────────────────────────────

def format_subgraph_for_llm(subgraph, query_type: str = "lifecycle") -> str:
    """
    Convert the output of any TSR function into a clean text block
    suitable for inclusion in an LLM prompt.

    Handles:
      - list of timeline records (from tsr / tsr_location)
      - dict hierarchy (from tsr_mission_components_full)
      - list of dicts (from fleet handlers)

    Parameters
    ----------
    subgraph   : output of any tsr_* function
    query_type : hint for section header

    Returns
    -------
    Multi-line string context block.
    """
    lines = [f"=== Temporal Subgraph Context  [query_type={query_type}] ===\n"]

    # ── Mission hierarchy dict ────────────────────────────────────────────────
    if isinstance(subgraph, dict) and "ships" in subgraph:
        lines.append(
            f"Mission: {subgraph.get('mission_name', subgraph.get('mission_id'))}  "
            f"({subgraph.get('start')} → {subgraph.get('end')})"
        )
        for ship_id, ship in subgraph.get("ships", {}).items():
            lines.append(f"\n  Ship: {ship['name']} ({ship_id})")
            for sys_id, sys in ship.get("systems", {}).items():
                lines.append(f"    System: {sys['name']} ({sys_id})")
                for eq_id, eq in sys.get("equipment", {}).items():
                    lines.append(f"      Equipment: {eq_id}  type={eq['type']}  status={eq['status']}")
                    for asm_id, asm in eq.get("assemblies", {}).items():
                        lines.append(
                            f"        Assembly: {asm_id}  name={asm['name']}  "
                            f"health={asm['health']}  "
                            f"({asm['from_time']} → {asm['to_time']})"
                        )
        return "\n".join(lines)

    # ── Timeline records from tsr / tsr_location ─────────────────────────────
    if isinstance(subgraph, list) and subgraph and "edge_type" in subgraph[0]:
        for e in subgraph:
            ts    = str(e.get("timestamp_str") or "no-timestamp")[:19]
            tt    = str(e.get("to_time") or "present")[:10]
            hi    = f"  health={e['health_index']:.3f}" if e.get("health_index") is not None else ""
            alpha = f"  alpha={e['alpha']:.3e}"         if e.get("alpha")        is not None else ""
            beta  = f"  beta={e['beta']:.4f}"           if e.get("beta")         is not None else ""
            ctx   = f"  [{e['context'][:80]}]"          if e.get("context")                  else ""
            lines.append(
                f"  {ts} → {tt:<10}  "
                f"[{e['edge_type']:<18}]  "
                f"{e.get('from_node','?'):<28} → {e.get('to_node','?'):<28}"
                f"{hi}{alpha}{beta}{ctx}"
            )
        if not subgraph:
            lines.append("  (no events found)")
        return "\n".join(lines)

    # ── Fleet handler list-of-dicts ───────────────────────────────────────────
    if isinstance(subgraph, list):
        if not subgraph:
            lines.append("  (no results)")
        for item in subgraph:
            lines.append("  " + "  ".join(f"{k}={v}" for k, v in item.items()))
        return "\n".join(lines)

    # ── Fallback ──────────────────────────────────────────────────────────────
    lines.append(str(subgraph))
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 11. LLM integration (Groq / Llama)
# ──────────────────────────────────────────────────────────────────────────────

_GROQ_CLIENT   = None
_GROQ_MODEL    = "llama-3.3-70b-versatile"
_GROQ_MAX_TOK  = 600
_GROQ_TEMP     = 0.2
_SYSTEM_PROMPT = (
    "You are a naval engineering analyst. "
    "Answer the question using ONLY the provided context. "
    "Be precise: cite specific dates, node IDs, health values, and beta parameters. "
    "If the context is insufficient to answer fully, say exactly what is missing."
)


def init_llm(api_key: str | None = None, model: str = _GROQ_MODEL) -> None:
    """
    Initialise the Groq LLM client.
    api_key defaults to the GROQ_API_KEY environment variable.
    Call this once before using ask_llm or query_pipeline.
    """
    global _GROQ_CLIENT, _GROQ_MODEL
    if not _GROQ_AVAILABLE:
        raise ImportError("groq package not installed.  Run: pip install groq")
    _GROQ_CLIENT = _Groq(api_key=api_key or os.environ.get("GROQ_API_KEY", ""))
    _GROQ_MODEL  = model
    print(f"[TSR] LLM client ready  model={_GROQ_MODEL}")


def ask_llm(context_text: str, question: str) -> str:
    """
    Send context_text + question to the configured LLM and return the answer.
    Call init_llm() first.
    """
    if _GROQ_CLIENT is None:
        return "[LLM not initialised — call init_llm() first]"
    user_prompt = f"Context:\n{context_text}\n\nQuestion: {question}"
    try:
        resp = _GROQ_CLIENT.chat.completions.create(
            model=_GROQ_MODEL,
            max_tokens=_GROQ_MAX_TOK,
            temperature=_GROQ_TEMP,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        return f"[LLM ERROR: {exc}]"


# ──────────────────────────────────────────────────────────────────────────────
# 12. End-to-end query pipeline
# ──────────────────────────────────────────────────────────────────────────────

_TSR_FN_MAP = {
    "location":                tsr_location,
    "lifecycle":               tsr,
    "failure":                 tsr,
    "causal":                  tsr,
    "counterfactual":          tsr,
    "mission_components":      tsr_mission_components_full,
    "mo_inventory":            tsr_mo_inventory,
    "fleet_workshop_load":     tsr_fleet_workshop_load,
    "fleet_workshop_ineffective": tsr_fleet_workshop_ineffective,
    "fleet_in_workshop":       tsr_fleet_in_workshop,
    "fleet_degraded_systems":  tsr_fleet_degraded_systems,
    "fleet_high_failure":      tsr_fleet_high_failure,
    "fleet_redeployed":        tsr_fleet_redeployed,
    "workshop_history":        tsr_workshop_history,
    "ship_health":             tsr_ship_health,
}


def query_pipeline(
    question:        str,
    entity_id:       str | None = None,
    query_type:      str        = "lifecycle",
    temporal_anchor             = None,
    tsr_fn:          str        = "lifecycle",
    **kwargs,
) -> dict:
    """
    Full TSR → LLM pipeline.

    1. Calls the appropriate TSR function to retrieve a temporal subgraph.
    2. Formats the subgraph as an LLM-ready context string.
    3. Sends context + question to the LLM.
    4. Returns a result dict with all intermediate artifacts.

    Parameters
    ----------
    question        : natural-language question
    entity_id       : starting node (required for entity-level queries)
    query_type      : "location" | "lifecycle" | "failure" | "causal" |
                      "counterfactual"
    temporal_anchor : ISO datetime string or None
    tsr_fn          : key into _TSR_FN_MAP (default "lifecycle")
    **kwargs        : extra keyword args forwarded to the TSR function
                      (e.g. threshold=0.03 for fleet_workshop_ineffective,
                            min_failures=3 for fleet_high_failure)

    Returns
    -------
    dict with keys:
        subgraph    — raw output of the TSR function
        context     — formatted context string sent to the LLM
        llm_answer  — LLM response string
        tsr_fn      — name of the TSR function used
        entity_id   — entity queried
        query_type  — query type used
    """
    fn = _TSR_FN_MAP.get(tsr_fn)
    if fn is None:
        raise ValueError(
            f"Unknown tsr_fn '{tsr_fn}'.  "
            f"Valid options: {sorted(_TSR_FN_MAP)}"
        )

    # ── Route arguments to the correct function signature ────────────────────
    fleet_fns_no_entity = {
        "fleet_workshop_load",
        "fleet_workshop_ineffective",
        "fleet_in_workshop",
        "fleet_degraded_systems",
        "fleet_high_failure",
        "fleet_redeployed",
    }

    if tsr_fn in fleet_fns_no_entity:
        subgraph = fn(**kwargs)
    elif tsr_fn == "location":
        subgraph = fn(entity_id, temporal_anchor)
    elif tsr_fn in ("lifecycle", "failure", "causal", "counterfactual"):
        subgraph = fn(entity_id, temporal_anchor, query_type)
    else:
        # workshop_history, ship_health, mo_inventory, mission_components
        subgraph = fn(entity_id, **kwargs)

    context    = format_subgraph_for_llm(subgraph, query_type)
    llm_answer = ask_llm(context, question)

    return {
        "subgraph":   subgraph,
        "context":    context,
        "llm_answer": llm_answer,
        "tsr_fn":     tsr_fn,
        "entity_id":  entity_id,
        "query_type": query_type,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 13. CLI / quick demo
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    JSON_PATH = (
        sys.argv[1]
        if len(sys.argv) > 1
        else r"query_final.json"
    )

    load_graph(JSON_PATH)

    # Optionally initialise LLM — requires GROQ_API_KEY env var
    try:
        init_llm()
        _LLM_READY = True
    except Exception:
        print("[TSR] LLM not available — skipping LLM demo")
        _LLM_READY = False

    # ── Demo: location query ─────────────────────────────────────────────────
    print("\n─── Demo: tsr_location ASSEMBLY_FS001 @ 2026-04-20 ───")
    loc = tsr_location("ASSEMBLY_FS001", "2026-04-20T00:00:00")
    print(format_subgraph_for_llm(loc, "location"))

    # ── Demo: failure BFS ────────────────────────────────────────────────────
    print("\n─── Demo: tsr ASSEMBLY_FS001 query_type=failure ───")
    failure = tsr("ASSEMBLY_FS001", query_type="failure")
    print(format_subgraph_for_llm(failure, "failure"))

    # ── Demo: fleet workshop ineffectiveness ─────────────────────────────────
    print("\n─── Demo: tsr_fleet_workshop_ineffective (threshold=0.05) ───")
    ineff = tsr_fleet_workshop_ineffective()
    print(format_subgraph_for_llm(ineff, "fleet"))

    # ── Demo: end-to-end pipeline with LLM ──────────────────────────────────
    if _LLM_READY:
        print("\n─── Demo: full pipeline — T01 question ───")
        result = query_pipeline(
            question="Where was ASSEMBLY_FS001 on April 20, 2026?",
            entity_id="ASSEMBLY_FS001",
            query_type="location",
            temporal_anchor="2026-04-20T00:00:00",
            tsr_fn="location",
        )
        print(result["llm_answer"])