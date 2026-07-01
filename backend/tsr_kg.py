"""
tsr.py  —  Temporal Subgraph Retrieval for Naval Digital Twin
=============================================================
Built exactly for query_final.json schema (18 node types, 12 edge types).

Graph facts this code knows:
  - INSTALLED_ON carries installation_context list  (from_time, to_time, health_index, alpha_param, beta_param)
  - ASSIGNED_TO   carries simple from_time/to_time  (workshops, missions, MO, dockyard)
  - PART_OF / BASED_AT / HAS_SYSTEM / MANUFACTURED_BY  — simple temporal edges
  - HAS_FUNCTION → HAS_FAILURE → HAS_EFFECT → HAS_CONSEQUENCE → DECISION_TAKEN  — static FMEA chain
  - DETECTS_FAILURE  — Sensor → FailureMode  (static)
  - param_tracking on Assembly: alpha_history, beta_history, health_history  (each: {timestamp, value, stage})

Public API
----------
  load_graph(path)                                   — call once
  tsr_location(entity_id, anchor)                    → list
  tsr_lifecycle(entity_id)                           → list
  tsr_failure_chain(entity_id)                       → dict  (full FMEA tree)
  tsr_causal(entity_id, anchor=None)                 → dict
  tsr_counterfactual(entity_id)                      → dict  (params + history for what-if)
  tsr_mission(mission_id)                            → dict  (5-hop hierarchy)
  tsr_mo_inventory(mo_id)                            → list
  tsr_workshop_history(workshop_id)                  → list
  tsr_ship_health(ship_id)                           → list
  tsr_fleet_workshop_ineffective(threshold=0.05)     → list
  tsr_fleet_workshop_load()                          → list
  tsr_fleet_in_workshop()                            → list
  tsr_fleet_degraded(health_threshold=0.80)          → list
  tsr_fleet_redeployed()                             → list
  tsr_health_at_time(entity_id, anchor)              → float | None
  tsr_param_at_time(entity_id, anchor)               → dict  (alpha, beta, stage)

  format_for_llm(result, query_type)                 → str
  init_llm(api_key, model)
  ask_llm(context, question)                         → str
  query_pipeline(question, tsr_fn, **kwargs)         → dict
"""

# ─────────────────────────────────────────────────────────────
# Imports
# ─────────────────────────────────────────────────────────────
import json, os
from datetime import datetime

try:
    from groq import Groq as _Groq
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False


# ─────────────────────────────────────────────────────────────
# Global indexes  (populated by load_graph)
# ─────────────────────────────────────────────────────────────
GRAPH      = {}
NODE_INDEX = {}   # node_id → full node dict
ALL_EDGES  = []   # [{edge_type, from, to, properties}]
ADJ_OUT    = {}   # node_id → [outgoing edge dicts]
ADJ_IN     = {}   # node_id → [incoming edge dicts]

# ── Typed sub-indexes built from the actual JSON keys ────────
# Populated by load_graph so every query has O(1) access.
ASSEMBLY_IDS   = set()   # nodes whose type == "Assembly"
SUBASSEMBLY_IDS = set()
SENSOR_IDS     = set()
WORKSHOP_IDS   = set()
EQUIPMENT_IDS  = set()
SHIP_IDS       = set()
MO_IDS         = set()

# ─────────────────────────────────────────────────────────────
# Graph loading
# ─────────────────────────────────────────────────────────────

def load_graph(path: str) -> None:
    """Load query_final.json and build all indexes."""
    global GRAPH, NODE_INDEX, ALL_EDGES, ADJ_OUT, ADJ_IN
    global ASSEMBLY_IDS, SUBASSEMBLY_IDS, SENSOR_IDS
    global WORKSHOP_IDS, EQUIPMENT_IDS, SHIP_IDS, MO_IDS

    with open(path, encoding="utf-8") as f:
        GRAPH = json.load(f)

    # Node index + typed sets
    NODE_INDEX = {}
    ASSEMBLY_IDS = SUBASSEMBLY_IDS = SENSOR_IDS = set()
    WORKSHOP_IDS = EQUIPMENT_IDS = SHIP_IDS = MO_IDS = set()
    ASSEMBLY_IDS, SUBASSEMBLY_IDS, SENSOR_IDS = set(), set(), set()
    WORKSHOP_IDS, EQUIPMENT_IDS, SHIP_IDS, MO_IDS = set(), set(), set(), set()

    for ntype, td in GRAPH["node_types"].items():
        for n in td.get("nodes", []):
            nid = n["node_id"]
            NODE_INDEX[nid] = n
            if ntype == "Assembly":          ASSEMBLY_IDS.add(nid)
            elif ntype == "SubAssembly":     SUBASSEMBLY_IDS.add(nid)
            elif ntype == "Sensors":         SENSOR_IDS.add(nid)
            elif ntype == "Workshop":        WORKSHOP_IDS.add(nid)
            elif ntype == "Equipment":       EQUIPMENT_IDS.add(nid)
            elif ntype == "Ship":            SHIP_IDS.add(nid)
            elif ntype == "MaterialOrganisation": MO_IDS.add(nid)

    # Edge index + adjacency
    ALL_EDGES = []
    ADJ_OUT, ADJ_IN = {}, {}
    for etype, td in GRAPH["relationship_types"].items():
        for e in td.get("edges", []):
            rec = {"edge_type": etype, "from": e["from"],
                   "to": e["to"], "properties": e.get("properties", {})}
            ALL_EDGES.append(rec)
            ADJ_OUT.setdefault(e["from"], []).append(rec)
            ADJ_IN .setdefault(e["to"],   []).append(rec)

    node_counts = {nt: len(td.get("nodes",[])) for nt, td in GRAPH["node_types"].items()}
    edge_counts = {et: len(td.get("edges",[])) for et, td in GRAPH["relationship_types"].items()}
    print(f"[TSR] Loaded  {sum(node_counts.values())} nodes  {sum(edge_counts.values())} edges")
    print(f"[TSR] Node types : {node_counts}")
    print(f"[TSR] Edge types : {edge_counts}")


# ─────────────────────────────────────────────────────────────
# Low-level helpers
# ─────────────────────────────────────────────────────────────

def node(nid: str) -> dict:
    return NODE_INDEX.get(nid, {})

def node_type(nid: str) -> str:
    n = NODE_INDEX.get(nid, {})
    return n.get("type") or n.get("node_type") or n.get("label") or ""

def props(nid: str) -> dict:
    return NODE_INDEX.get(nid, {}).get("properties", {})

def out(nid: str, etype=None) -> list:
    edges = ADJ_OUT.get(nid, [])
    return [e for e in edges if e["edge_type"] == etype] if etype else edges

def inc(nid: str, etype=None) -> list:
    edges = ADJ_IN.get(nid, [])
    return [e for e in edges if e["edge_type"] == etype] if etype else edges

def parse_dt(s) -> datetime | None:
    if not s: return None
    try:
        return datetime.fromisoformat(str(s).replace("Z","")).replace(tzinfo=None)
    except Exception:
        return None

def dt_str(dt: datetime | None) -> str:
    return str(dt)[:19] if dt else "None"


# ─────────────────────────────────────────────────────────────
# Temporal helpers
# ─────────────────────────────────────────────────────────────

def _ctx_active(ctx: dict, anchor: datetime | None) -> bool:
    """
    True if installation_context window covers anchor.
    Rule: from_time <= anchor < to_time   (open end = currently active)
    """
    ft = parse_dt(ctx.get("from_time"))
    tt = parse_dt(ctx.get("to_time"))
    if ft is None:
        return False
    if anchor is None:
        return True
    return ft <= anchor and (tt is None or anchor < tt)

def _simple_edge_active(p: dict, anchor: datetime | None) -> bool:
    """For edges with plain from_time/to_time (ASSIGNED_TO, PART_OF etc.)."""
    if anchor is None:
        return True
    ft = parse_dt(p.get("from_time"))
    tt = parse_dt(p.get("to_time"))
    if ft and ft > anchor:
        return False
    if tt and tt <= anchor:
        return False
    return True


# ─────────────────────────────────────────────────────────────
# Param / health history lookup
# ─────────────────────────────────────────────────────────────

def _history_at(history: list, anchor: datetime | None):
    """
    Return the entry from a param_tracking history list (alpha/beta/health)
    whose timestamp is the most recent <= anchor.
    Each entry: {timestamp, value, stage}
    """
    if not history:
        return None
    best_entry, best_t = None, None
    for entry in history:
        t = parse_dt(entry.get("timestamp"))
        if t is None:
            continue
        if anchor is None or t <= anchor:
            if best_t is None or t > best_t:
                best_t, best_entry = t, entry
    return best_entry

def tsr_health_at_time(entity_id: str, anchor) -> float | None:
    """
    Return health value of an Assembly at a given time.
    Uses param_tracking.health_history (monthly granularity).
    """
    anchor_dt = parse_dt(anchor)
    pt = props(entity_id).get("param_tracking", {})
    entry = _history_at(pt.get("health_history", []), anchor_dt)
    return entry["value"] if entry else None

def tsr_param_at_time(entity_id: str, anchor) -> dict:
    """
    Return {alpha, beta, stage, timestamp} of an Assembly at a given time.
    """
    anchor_dt = parse_dt(anchor)
    pt = props(entity_id).get("param_tracking", {})
    alpha_e = _history_at(pt.get("alpha_history", []), anchor_dt)
    beta_e  = _history_at(pt.get("beta_history",  []), anchor_dt)
    return {
        "alpha":     alpha_e["value"]     if alpha_e else None,
        "beta":      beta_e["value"]      if beta_e  else None,
        "stage":     alpha_e.get("stage") if alpha_e else None,
        "timestamp": alpha_e.get("timestamp") if alpha_e else None,
    }


# ─────────────────────────────────────────────────────────────
# 1. tsr_location  — where is entity at anchor time?
# ─────────────────────────────────────────────────────────────

def tsr_location(entity_id: str, anchor=None) -> list:
    """
    Return active location(s) of entity_id at anchor time.

    Checks INSTALLED_ON (installation_context) and ASSIGNED_TO (simple timestamps).
    Post-filter: if an Equipment location is active, suppress MO assignments
    (assembly is physically on-board, not in the store).

    Works for: Assembly, SubAssembly, Sensor, Ship (BASED_AT / ASSIGNED_TO)
    """
    anchor_dt = parse_dt(anchor)
    results   = []

    # ── INSTALLED_ON with installation_context ────────────────────────────
    for e in out(entity_id, "INSTALLED_ON"):
        for ctx in e["properties"].get("installation_context", []):
            if not _ctx_active(ctx, anchor_dt):
                continue
            results.append({
                "location":      e["to"],
                "location_type": node_type(e["to"]),
                "edge_type":     "INSTALLED_ON",
                "from_time":     ctx.get("from_time"),
                "to_time":       ctx.get("to_time"),
                "health_index":  ctx.get("health_index"),
                "alpha":         ctx.get("alpha_param"),
                "beta":          ctx.get("beta_param"),
                "context":       ctx.get("context", ""),
                "_ft":           parse_dt(ctx.get("from_time")),
            })

    # ── ASSIGNED_TO  (workshops, missions, MO, dockyard) ─────────────────
    for e in out(entity_id, "ASSIGNED_TO"):
        p = e["properties"]
        if not _simple_edge_active(p, anchor_dt):
            continue
        results.append({
            "location":      e["to"],
            "location_type": node_type(e["to"]),
            "edge_type":     "ASSIGNED_TO",
            "from_time":     p.get("from_time"),
            "to_time":       p.get("to_time"),
            "health_index":  None,
            "alpha":         None,
            "beta":          None,
            "context":       p.get("expert_notes", ""),
            "_ft":           parse_dt(p.get("from_time")),
        })

    # ── BASED_AT  (for Ship nodes) ────────────────────────────────────────
    for e in out(entity_id, "BASED_AT"):
        p = e["properties"]
        if not _simple_edge_active(p, anchor_dt):
            continue
        results.append({
            "location":      e["to"],
            "location_type": node_type(e["to"]),
            "edge_type":     "BASED_AT",
            "from_time":     p.get("from_time"),
            "to_time":       p.get("to_time"),
            "health_index":  None,
            "alpha":         None,
            "beta":          None,
            "context":       "",
            "_ft":           parse_dt(p.get("from_time")),
        })

    results.sort(key=lambda x: x["_ft"] or datetime.max)

    # Post-filter: Equipment on-board supersedes open MO store
    if anchor_dt and results:
        has_equip = any(r["location_type"] == "Equipment" for r in results)
        if has_equip:
            results = [r for r in results if r["location_type"] != "MaterialOrganisation"]

    # Clean up internal sort key
    for r in results:
        r.pop("_ft", None)
    return results


# ─────────────────────────────────────────────────────────────
# 2. tsr_lifecycle  — full timeline of an entity
# ─────────────────────────────────────────────────────────────

def tsr_lifecycle(entity_id: str) -> list:
    """
    Return every timestamped event connected to entity_id across all
    edge types, sorted chronologically.

    Covers: installations, workshop visits, MO assignments, mission
    assignments, sensor readings, manufacturing events, etc.
    """
    events = []

    def _add_ctx(e, ctx):
        ft = parse_dt(ctx.get("from_time"))
        events.append({
            "timestamp":    ft,
            "timestamp_str": ctx.get("from_time"),
            "to_time":      ctx.get("to_time"),
            "edge_type":    e["edge_type"],
            "direction":    "OUT" if e["from"] == entity_id else "IN",
            "other_node":   e["to"] if e["from"] == entity_id else e["from"],
            "other_type":   node_type(e["to"] if e["from"] == entity_id else e["from"]),
            "health_index": ctx.get("health_index"),
            "alpha":        ctx.get("alpha_param"),
            "beta":         ctx.get("beta_param"),
            "context":      ctx.get("context", ""),
            "window_type":  "installation_context",
        })

    def _add_simple(e, p):
        ft = parse_dt(p.get("from_time"))
        events.append({
            "timestamp":    ft,
            "timestamp_str": p.get("from_time"),
            "to_time":      p.get("to_time"),
            "edge_type":    e["edge_type"],
            "direction":    "OUT" if e["from"] == entity_id else "IN",
            "other_node":   e["to"] if e["from"] == entity_id else e["from"],
            "other_type":   node_type(e["to"] if e["from"] == entity_id else e["from"]),
            "health_index": None, "alpha": None, "beta": None,
            "context":      p.get("expert_notes", p.get("context", "")),
            "window_type":  "simple",
        })

    def _add_static(e):
        events.append({
            "timestamp":    None, "timestamp_str": None, "to_time": None,
            "edge_type":    e["edge_type"],
            "direction":    "OUT" if e["from"] == entity_id else "IN",
            "other_node":   e["to"] if e["from"] == entity_id else e["from"],
            "other_type":   node_type(e["to"] if e["from"] == entity_id else e["from"]),
            "health_index": None, "alpha": None, "beta": None,
            "context":      "", "window_type": "static",
        })

    for e in out(entity_id) + inc(entity_id):
        p = e["properties"]
        if "installation_context" in p:
            for ctx in p["installation_context"]:
                _add_ctx(e, ctx)
        elif "from_time" in p:
            _add_simple(e, p)
        else:
            _add_static(e)

    events.sort(key=lambda x: x["timestamp"] or datetime.max)
    return events


# ─────────────────────────────────────────────────────────────
# 3. tsr_failure_chain  — full FMEA tree from entity
# ─────────────────────────────────────────────────────────────

def tsr_failure_chain(entity_id: str) -> dict:
    """
    Walk the full FMEA chain reachable from entity_id via BFS:
      entity → HAS_FUNCTION → Function
             → HAS_FAILURE  → FailureMode
             → HAS_EFFECT   → Effect
             → HAS_CONSEQUENCE → Consequence
             → DECISION_TAKEN  → Decision
      Also: Sensor → DETECTS_FAILURE → FailureMode (reversed: who detects it)

    Returns a nested dict keyed by function → failure_mode → {effect, consequence, decision, sensors}
    """
    FMEA_EDGES = {"HAS_FUNCTION", "HAS_FAILURE", "HAS_EFFECT",
                  "HAS_CONSEQUENCE", "DECISION_TAKEN", "DETECTS_FAILURE"}

    # BFS restricted to FMEA edge types
    visited, queue = set(), [entity_id]
    collected = {et: [] for et in FMEA_EDGES}

    while queue:
        cur = queue.pop(0)
        if cur in visited: continue
        visited.add(cur)
        for e in out(cur) + inc(cur):
            if e["edge_type"] not in FMEA_EDGES: continue
            nb = e["to"] if e["from"] == cur else e["from"]
            collected[e["edge_type"]].append(e)
            if nb not in visited:
                queue.append(nb)

    # Build structured tree
    tree = {"entity_id": entity_id, "functions": {}}

    for func_edge in collected["HAS_FUNCTION"]:
        if func_edge["from"] != entity_id:
            continue  # only outgoing function edges from this entity
        fn_id   = func_edge["to"]
        fn_props = props(fn_id)
        fn_node  = {
            "function_id":        fn_id,
            "primary_function":   fn_props.get("primary_function", ""),
            "secondary_function": fn_props.get("secondary_function", ""),
            "failure_modes":      {}
        }

        for fm_edge in collected["HAS_FAILURE"]:
            if fm_edge["from"] != fn_id: continue
            fm_id   = fm_edge["to"]
            fm_props = props(fm_id)

            # Sensors that detect this failure mode
            detecting_sensors = [
                {
                    "sensor_id":   e["from"],
                    "sensor_type": props(e["from"]).get("sensor_type", ""),
                    "measurement": props(e["from"]).get("measurement_type", ""),
                    "threshold":   props(e["from"]).get("threshold"),
                    "current_val": props(e["from"]).get("current_value"),
                    "unit":        props(e["from"]).get("unit", ""),
                }
                for e in collected["DETECTS_FAILURE"]
                if e["to"] == fm_id
            ]

            # Effect
            effect_edges = [e for e in collected["HAS_EFFECT"] if e["from"] == fm_id]
            effects = []
            for eff_edge in effect_edges:
                eff_id    = eff_edge["to"]
                eff_props = props(eff_id)
                # Consequence
                cons_edges = [e for e in collected["HAS_CONSEQUENCE"] if e["from"] == eff_id]
                consequences = []
                for cons_edge in cons_edges:
                    cons_id    = cons_edge["to"]
                    cons_props = props(cons_id)
                    # Decision
                    dec_edges = [e for e in collected["DECISION_TAKEN"] if e["from"] == cons_id]
                    decisions = [
                        {
                            "decision_id":   de["to"],
                            "type":          props(de["to"]).get("type_description", ""),
                            "priority":      props(de["to"]).get("priority", ""),
                            "estimated_cost": props(de["to"]).get("estimated_cost"),
                            "date":          props(de["to"]).get("date"),
                        }
                        for de in dec_edges
                    ]
                    consequences.append({
                        "consequence_id":   cons_id,
                        "name":             cons_props.get("consequence_name", ""),
                        "category":         cons_props.get("category", ""),
                        "repair_cost":      cons_props.get("repair_cost"),
                        "impact_safety":    cons_props.get("impact_on_safety", ""),
                        "impact_mission":   cons_props.get("impact_on_mission_completion", ""),
                        "decisions":        decisions,
                    })
                effects.append({
                    "effect_id":   eff_id,
                    "effect_type": eff_props.get("effect_type", ""),
                    "symptoms":    eff_props.get("observable_symptoms", ""),
                    "consequences": consequences,
                })

            fn_node["failure_modes"][fm_id] = {
                "failure_mode_id":    fm_id,
                "failure_type":       fm_props.get("failure_mode_type", ""),
                "mechanism":          fm_props.get("failure_mechanism", ""),
                "severity":           fm_props.get("severity", ""),
                "detectability":      fm_props.get("detectability", ""),
                "occurrence_prob":    fm_props.get("occurrence_probability"),
                "detecting_sensors":  detecting_sensors,
                "effects":            effects,
            }

        tree["functions"][fn_id] = fn_node

    return tree


# ─────────────────────────────────────────────────────────────
# 4. tsr_causal  — why + what + what was decided
# ─────────────────────────────────────────────────────────────

def tsr_causal(entity_id: str, anchor=None) -> dict:
    """
    Return a causal narrative for entity_id:
      - Current/past installation context (with alpha, beta per platform)
      - Full FMEA failure chain
      - Workshop visits and their outcomes (health delta)
      - Decisions taken
      - Sensor alerts
    Combines tsr_location + tsr_failure_chain + workshop analysis.
    """
    anchor_dt = parse_dt(anchor)

    # Location context
    location = tsr_location(entity_id, anchor)

    # Failure chain
    failure_chain = tsr_failure_chain(entity_id)

    # Installation history per platform (with beta comparison)
    install_history = []
    for e in out(entity_id, "INSTALLED_ON"):
        platform = e["to"]
        for ctx in e["properties"].get("installation_context", []):
            install_history.append({
                "platform":     platform,
                "platform_type": node_type(platform),
                "from_time":    ctx.get("from_time"),
                "to_time":      ctx.get("to_time"),
                "health_index": ctx.get("health_index"),
                "alpha":        ctx.get("alpha_param"),
                "beta":         ctx.get("beta_param"),
                "context":      ctx.get("context", ""),
            })
    install_history.sort(key=lambda x: parse_dt(x["from_time"]) or datetime.max)

    # Workshop visits with health delta
    workshop_visits = []
    for e in out(entity_id, "ASSIGNED_TO"):
        if e["to"] not in WORKSHOP_IDS: continue
        p   = e["properties"]
        ft  = parse_dt(p.get("from_time"))
        tt  = parse_dt(p.get("to_time"))
        pt  = props(entity_id).get("param_tracking", {})
        h_before = _history_at(pt.get("health_history", []), ft)
        h_after  = _history_at(pt.get("health_history", []), tt)
        delta    = None
        if h_before and h_after:
            try: delta = round(float(h_after["value"]) - float(h_before["value"]), 4)
            except Exception: pass
        workshop_visits.append({
            "workshop":      e["to"],
            "from_time":     p.get("from_time"),
            "to_time":       p.get("to_time"),
            "notes":         p.get("expert_notes", ""),
            "health_before": h_before["value"] if h_before else None,
            "health_after":  h_after["value"]  if h_after  else None,
            "health_delta":  delta,
        })
    workshop_visits.sort(key=lambda x: parse_dt(x["from_time"]) or datetime.max)

    # Current params
    current_params = tsr_param_at_time(entity_id, anchor or datetime.now())
    current_health = tsr_health_at_time(entity_id, anchor or datetime.now())

    return {
        "entity_id":         entity_id,
        "anchor":            str(anchor_dt)[:19] if anchor_dt else "current",
        "current_health":    current_health,
        "current_params":    current_params,
        "active_location":   location,
        "install_history":   install_history,
        "workshop_visits":   workshop_visits,
        "failure_chain":     failure_chain,
    }


# ─────────────────────────────────────────────────────────────
# 5. tsr_counterfactual  — what-if reasoning data
# ─────────────────────────────────────────────────────────────

def tsr_counterfactual(entity_id: str) -> dict:
    """
    Collect all data needed for counterfactual / what-if reasoning:
      - Full installation context per platform (alpha, beta, health trajectory)
      - Full param_tracking history (monthly alpha, beta, health)
      - Workshop visits and outcomes
      - Alternative platforms in the graph (for comparison)
    The LLM uses this to answer "what if it had been redeployed earlier" etc.
    """
    # Platform contexts with degradation parameters
    platforms = {}
    for e in out(entity_id, "INSTALLED_ON"):
        pid = e["to"]
        ctxs = []
        for ctx in e["properties"].get("installation_context", []):
            ctxs.append({
                "from_time":    ctx.get("from_time"),
                "to_time":      ctx.get("to_time"),
                "health_index": ctx.get("health_index"),
                "alpha":        ctx.get("alpha_param"),
                "beta":         ctx.get("beta_param"),
                "context":      ctx.get("context", ""),
            })
        platforms[pid] = {
            "platform_type": node_type(pid),
            "platform_props": {k: v for k, v in props(pid).items()
                               if not isinstance(v, (list, dict))},
            "installation_contexts": ctxs,
        }

    # Full monthly history
    pt = props(entity_id).get("param_tracking", {})
    health_hist = [
        {"timestamp": e.get("timestamp"), "value": e.get("value"), "stage": e.get("stage")}
        for e in pt.get("health_history", [])
    ]
    alpha_hist = [
        {"timestamp": e.get("timestamp"), "value": e.get("value"), "stage": e.get("stage")}
        for e in pt.get("alpha_history", [])
    ]
    beta_hist = [
        {"timestamp": e.get("timestamp"), "value": e.get("value"), "stage": e.get("stage")}
        for e in pt.get("beta_history", [])
    ]

    # Workshop visits
    workshop_visits = []
    for e in out(entity_id, "ASSIGNED_TO"):
        if e["to"] not in WORKSHOP_IDS: continue
        p = e["properties"]
        workshop_visits.append({
            "workshop":  e["to"],
            "from_time": p.get("from_time"),
            "to_time":   p.get("to_time"),
            "notes":     p.get("expert_notes", ""),
        })

    # Other assemblies on the same equipment types (for comparison)
    comparisons = {}
    for pid in platforms:
        peers = []
        for e in inc(pid, "INSTALLED_ON"):
            if e["from"] != entity_id and e["from"] in ASSEMBLY_IDS:
                peer_ctxs = [
                    {"from_time": c.get("from_time"), "to_time": c.get("to_time"),
                     "alpha": c.get("alpha_param"), "beta": c.get("beta_param"),
                     "health_index": c.get("health_index")}
                    for c in e["properties"].get("installation_context", [])
                ]
                peers.append({"assembly": e["from"], "contexts": peer_ctxs})
        comparisons[pid] = peers

    return {
        "entity_id":       entity_id,
        "assembly_name":   props(entity_id).get("assembly_name", ""),
        "pooled_alpha":    props(entity_id).get("alpha_param"),
        "pooled_beta":     props(entity_id).get("beta_param"),
        "platforms":       platforms,
        "health_history":  health_hist,
        "alpha_history":   alpha_hist,
        "beta_history":    beta_hist,
        "workshop_visits": workshop_visits,
        "peer_assemblies_per_platform": comparisons,
    }


# ─────────────────────────────────────────────────────────────
# 6. tsr_mission  — 5-hop mission hierarchy
# ─────────────────────────────────────────────────────────────

def tsr_mission(mission_id: str) -> dict:
    """
    Traverse: Mission ← ASSIGNED_TO ← CombatGroup ← PART_OF ← Ship
              → HAS_SYSTEM → System ← INSTALLED_ON ← Equipment
              ← INSTALLED_ON ← Assembly (active during mission window)
    Also includes: which ships were directly ASSIGNED_TO the mission,
    and sensor readings on active assemblies.
    """
    mp = props(mission_id)
    m_start = parse_dt(mp.get("start_date"))
    m_end   = parse_dt(mp.get("end_date"))

    result = {
        "mission_id":   mission_id,
        "mission_name": mp.get("mission_name", ""),
        "start":        str(m_start)[:10] if m_start else None,
        "end":          str(m_end)[:10]   if m_end   else None,
        "status":       mp.get("status", ""),
        "type":         mp.get("mission_type", ""),
        "location":     mp.get("location", ""),
        "outcome":      mp.get("mission_outcome", ""),
        "notes":        mp.get("expert_notes", ""),
        "ships":        {}
    }

    # Ships directly ASSIGNED_TO the mission
    direct_ships = set()
    for e in inc(mission_id, "ASSIGNED_TO"):
        nid = e["from"]
        if nid in SHIP_IDS:
            direct_ships.add(nid)
            p = e["properties"]
            if _simple_edge_active(p, m_start or m_end):
                result["ships"][nid] = _build_ship_branch(nid, m_start, m_end)
                result["ships"][nid]["assignment_notes"] = p.get("expert_notes", "")

    # Ships via CombatGroup → ASSIGNED_TO → Mission
    for cg_edge in inc(mission_id, "ASSIGNED_TO"):
        cg_id = cg_edge["from"]
        if node_type(cg_id) != "CombatGroup": continue
        for ship_edge in inc(cg_id, "PART_OF"):
            sid = ship_edge["from"]
            if sid not in SHIP_IDS: continue
            if sid not in result["ships"]:
                result["ships"][sid] = _build_ship_branch(sid, m_start, m_end)

    return result


def _build_ship_branch(ship_id: str, m_start, m_end) -> dict:
    """Build the System→Equipment→Assembly branch for one ship."""
    sp = props(ship_id)
    branch = {
        "ship_name": sp.get("name", ship_id),
        "ship_class": sp.get("class", ""),
        "status": sp.get("status", ""),
        "systems": {}
    }
    for sys_e in out(ship_id, "HAS_SYSTEM"):
        sys_id = sys_e["to"]
        sp2    = props(sys_id)
        sys_node = {
            "system_name": sp2.get("system_name", sys_id),
            "system_type": sp2.get("system_type", ""),
            "equipment":   {}
        }
        for eq_e in inc(sys_id, "INSTALLED_ON"):
            eq_id = eq_e["from"]
            if eq_id not in EQUIPMENT_IDS: continue
            eq_p = props(eq_id)
            eq_node = {
                "equipment_type": eq_p.get("equipment_type", ""),
                "status":         eq_p.get("status", ""),
                "health":         eq_p.get("health_tracking", {}).get("health_history", [{}])[-1].get("reliability", "N/A"),
                "assemblies":     {}
            }
            for asm_e in inc(eq_id, "INSTALLED_ON"):
                asm_id = asm_e["from"]
                if asm_id not in ASSEMBLY_IDS: continue
                # Find context active during mission window
                active_ctx = next(
                    (ctx for ctx in asm_e["properties"].get("installation_context", [])
                     if _ctx_covers_window(ctx, m_start, m_end)),
                    None
                )
                if not active_ctx: continue
                asm_p = props(asm_id)
                # Sensors on this assembly
                sensors = [
                    {
                        "sensor_id":   se["from"],
                        "sensor_type": props(se["from"]).get("sensor_type", ""),
                        "current_val": props(se["from"]).get("current_value"),
                        "unit":        props(se["from"]).get("unit", ""),
                        "threshold":   props(se["from"]).get("threshold"),
                    }
                    for se in inc(asm_id, "INSTALLED_ON")
                    if se["from"] in SENSOR_IDS
                ]
                eq_node["assemblies"][asm_id] = {
                    "assembly_name": asm_p.get("assembly_name", ""),
                    "health_index":  active_ctx.get("health_index", "N/A"),
                    "alpha":         active_ctx.get("alpha_param"),
                    "beta":          active_ctx.get("beta_param"),
                    "from_time":     str(active_ctx.get("from_time", ""))[:10],
                    "to_time":       str(active_ctx.get("to_time",   "present"))[:10],
                    "context":       active_ctx.get("context", ""),
                    "sensors":       sensors,
                }
            sys_node["equipment"][eq_id] = eq_node
        branch["systems"][sys_id] = sys_node
    return branch


def _ctx_covers_window(ctx: dict, w_start, w_end) -> bool:
    """True if installation context overlaps the [w_start, w_end] window."""
    ft = parse_dt(ctx.get("from_time"))
    tt = parse_dt(ctx.get("to_time"))
    if not ft: return False
    if w_end   and ft > w_end:   return False
    if w_start and tt is not None and tt < w_start: return False
    return True


# ─────────────────────────────────────────────────────────────
# 7. Fleet handlers
# ─────────────────────────────────────────────────────────────

def tsr_mo_inventory(mo_id: str = "MO_001") -> list:
    """Assemblies currently in the material organisation (no to_time)."""
    return [
        {
            "assembly_id":   e["from"],
            "assembly_name": props(e["from"]).get("assembly_name", ""),
            "from_time":     e["properties"].get("from_time"),
            "notes":         e["properties"].get("expert_notes", ""),
        }
        for e in inc(mo_id, "ASSIGNED_TO")
        if not e["properties"].get("to_time")
    ]


def tsr_workshop_history(workshop_id: str) -> list:
    """All assembly visits to a workshop, sorted by start date."""
    rows = []
    for e in inc(workshop_id, "ASSIGNED_TO"):
        p = e["properties"]
        asm_id = e["from"]
        rows.append({
            "assembly_id":   asm_id,
            "assembly_name": props(asm_id).get("assembly_name", ""),
            "from_time":     p.get("from_time"),
            "to_time":       p.get("to_time"),
            "notes":         p.get("expert_notes", ""),
        })
    rows.sort(key=lambda x: parse_dt(x["from_time"]) or datetime.max)
    return rows


def tsr_ship_health(ship_id: str) -> list:
    """Health of all currently active assemblies on ship_id."""
    results = []
    for sys_e in out(ship_id, "HAS_SYSTEM"):
        sys_id = sys_e["to"]
        for eq_e in inc(sys_id, "INSTALLED_ON"):
            eq_id = eq_e["from"]
            if eq_id not in EQUIPMENT_IDS: continue
            for asm_e in inc(eq_id, "INSTALLED_ON"):
                asm_id = asm_e["from"]
                if asm_id not in ASSEMBLY_IDS: continue
                for ctx in asm_e["properties"].get("installation_context", []):
                    if not ctx.get("to_time"):  # open = active now
                        results.append({
                            "assembly_id":   asm_id,
                            "assembly_name": props(asm_id).get("assembly_name", ""),
                            "equipment_id":  eq_id,
                            "system_id":     sys_id,
                            "health_index":  ctx.get("health_index", "N/A"),
                            "alpha":         ctx.get("alpha_param"),
                            "beta":          ctx.get("beta_param"),
                            "since":         ctx.get("from_time"),
                        })
                        break
    return sorted(
        results,
        key=lambda x: float(x["health_index"]) if isinstance(x["health_index"], (int, float)) else 1.0
    )


def tsr_fleet_workshop_ineffective(threshold: float = 0.05) -> list:
    """
    Assemblies with ≥2 workshop visits where cumulative health Δ ≤ threshold.
    Root cause: equipment context incompatibility rather than assembly fault.
    """
    results = []
    for asm_id in ASSEMBLY_IDS:
        visits = [e for e in out(asm_id, "ASSIGNED_TO") if e["to"] in WORKSHOP_IDS]
        if len(visits) < 2: continue

        pt      = props(asm_id).get("param_tracking", {})
        h_hist  = pt.get("health_history", [])
        if not h_hist: continue

        v_times = sorted(filter(None, [parse_dt(e["properties"].get("from_time")) for e in visits]))
        x_times = sorted(filter(None, [parse_dt(e["properties"].get("to_time"))   for e in visits]))
        if not v_times or not x_times: continue

        h_start = _history_at(h_hist, v_times[0])
        h_end   = _history_at(h_hist, x_times[-1])
        if h_start is None or h_end is None: continue

        delta = float(h_end["value"]) - float(h_start["value"])
        if delta <= threshold:
            results.append({
                "assembly_id":   asm_id,
                "assembly_name": props(asm_id).get("assembly_name", ""),
                "visits":        len(visits),
                "health_before": round(float(h_start["value"]), 4),
                "health_after":  round(float(h_end["value"]),   4),
                "delta":         round(delta, 4),
                "verdict":       "Ineffective — context incompatibility suspected" if delta <= 0
                                 else f"Marginal improvement only (+{delta:.4f})",
            })
    return results


def tsr_fleet_workshop_load() -> list:
    """(workshop_id, visit_count) sorted by busiest first."""
    counts: dict = {}
    for asm_id in ASSEMBLY_IDS:
        for e in out(asm_id, "ASSIGNED_TO"):
            if e["to"] in WORKSHOP_IDS:
                counts[e["to"]] = counts.get(e["to"], 0) + 1
    return sorted(
        [{"workshop_id": wid, "workshop_type": props(wid).get("workshop_type",""),
          "visit_count": cnt} for wid, cnt in counts.items()],
        key=lambda x: x["visit_count"], reverse=True
    )


def tsr_fleet_in_workshop() -> list:
    """Assemblies currently inside a workshop (open to_time)."""
    results = []
    for asm_id in ASSEMBLY_IDS:
        for e in out(asm_id, "ASSIGNED_TO"):
            if e["to"] not in WORKSHOP_IDS: continue
            if not e["properties"].get("to_time"):
                results.append({
                    "assembly_id":   asm_id,
                    "assembly_name": props(asm_id).get("assembly_name", ""),
                    "workshop_id":   e["to"],
                    "workshop_type": props(e["to"]).get("workshop_type", ""),
                    "since":         e["properties"].get("from_time"),
                    "notes":         e["properties"].get("expert_notes", ""),
                })
    return results


def tsr_fleet_degraded(health_threshold: float = 0.80) -> list:
    """Assemblies currently below health_threshold on any equipment."""
    results = []
    for asm_id in ASSEMBLY_IDS:
        for e in out(asm_id, "INSTALLED_ON"):
            if e["to"] not in EQUIPMENT_IDS: continue
            for ctx in e["properties"].get("installation_context", []):
                if ctx.get("to_time"): continue  # closed
                hi = ctx.get("health_index")
                if hi is not None and float(hi) < health_threshold:
                    results.append({
                        "assembly_id":   asm_id,
                        "assembly_name": props(asm_id).get("assembly_name", ""),
                        "equipment_id":  e["to"],
                        "health_index":  round(float(hi), 4),
                        "alpha":         ctx.get("alpha_param"),
                        "beta":          ctx.get("beta_param"),
                        "since":         ctx.get("from_time"),
                    })
    return sorted(results, key=lambda x: x["health_index"])


def tsr_fleet_redeployed() -> list:
    """Assemblies that have been installed on more than one equipment platform."""
    results = []
    for asm_id in ASSEMBLY_IDS:
        platforms = {e["to"] for e in out(asm_id, "INSTALLED_ON") if e["to"] in EQUIPMENT_IDS}
        if len(platforms) > 1:
            # collect beta per platform for comparison
            platform_betas = {}
            for e in out(asm_id, "INSTALLED_ON"):
                if e["to"] not in EQUIPMENT_IDS: continue
                betas = [ctx.get("beta_param") for ctx in e["properties"].get("installation_context", [])]
                platform_betas[e["to"]] = betas
            results.append({
                "assembly_id":    asm_id,
                "assembly_name":  props(asm_id).get("assembly_name", ""),
                "platforms":      sorted(platforms),
                "platform_count": len(platforms),
                "beta_per_platform": platform_betas,
            })
    return results


# ─────────────────────────────────────────────────────────────
# 8. format_for_llm  — render any result as a clean text block
# ─────────────────────────────────────────────────────────────

def format_for_llm(result, query_type: str = "") -> str:
    """Convert any TSR result into a plain-text context block for the LLM."""
    lines = [f"=== TSR Context  [{query_type}] ===\n"]

    # ── location list ─────────────────────────────────────────────────────
    if isinstance(result, list) and result and "location" in result[0]:
        for r in result:
            hi   = f"  health={r['health_index']:.4f}" if r.get("health_index") is not None else ""
            ab   = (f"  alpha={r['alpha']:.3e}  beta={r['beta']:.4f}"
                    if r.get("alpha") is not None else "")
            ctx  = f"  [{r['context'][:80]}]" if r.get("context") else ""
            tt   = str(r.get("to_time") or "present")[:10]
            lines.append(
                f"  Location: {r['location']}  ({r['location_type']})  "
                f"from={str(r.get('from_time','?'))[:19]}  to={tt}"
                f"{hi}{ab}{ctx}"
            )
        return "\n".join(lines)

    # ── lifecycle event list ───────────────────────────────────────────────
    if isinstance(result, list) and result and "edge_type" in result[0]:
        for e in result:
            ts  = str(e.get("timestamp_str") or "no-ts")[:19]
            tt  = str(e.get("to_time") or "present")[:10]
            hi  = f"  health={e['health_index']:.4f}" if e.get("health_index") is not None else ""
            ab  = (f"  α={e['alpha']:.3e}  β={e['beta']:.4f}"
                   if e.get("alpha") is not None else "")
            ctx = f"  [{e['context'][:70]}]" if e.get("context") else ""
            lines.append(
                f"  {ts} → {tt:<10}  [{e['edge_type']:<20}]  "
                f"{e.get('other_node','?'):<30}  ({e.get('other_type','')})"
                f"{hi}{ab}{ctx}"
            )
        return "\n".join(lines)

    # ── failure chain dict ────────────────────────────────────────────────
    if isinstance(result, dict) and "functions" in result:
        lines.append(f"Entity: {result['entity_id']}")
        for fn_id, fn in result["functions"].items():
            lines.append(f"\n  Function: {fn_id}  — {fn['primary_function']}")
            for fm_id, fm in fn["failure_modes"].items():
                lines.append(
                    f"    FailureMode: {fm_id}  type={fm['failure_type']}"
                    f"  severity={fm['severity']}  detectability={fm['detectability']}"
                    f"  prob={fm['occurrence_prob']}"
                )
                lines.append(f"      mechanism: {fm['mechanism']}")
                for s in fm["detecting_sensors"]:
                    lines.append(
                        f"      Sensor: {s['sensor_id']}  type={s['sensor_type']}"
                        f"  current={s['current_val']} {s['unit']}  threshold={s['threshold']}"
                    )
                for eff in fm["effects"]:
                    lines.append(f"      Effect: {eff['effect_id']}  {eff['effect_type']}  — {eff['symptoms']}")
                    for cons in eff["consequences"]:
                        lines.append(
                            f"        Consequence: {cons['consequence_id']}  {cons['name']}"
                            f"  cost=₹{cons['repair_cost']}  safety={cons['impact_safety']}"
                        )
                        for dec in cons["decisions"]:
                            lines.append(
                                f"          Decision: {dec['decision_id']}  {dec['type']}"
                                f"  priority={dec['priority']}  cost=₹{dec['estimated_cost']}"
                            )
        return "\n".join(lines)

    # ── causal dict ───────────────────────────────────────────────────────
    if isinstance(result, dict) and "active_location" in result:
        lines.append(f"Entity: {result['entity_id']}  anchor={result['anchor']}")
        lines.append(f"Health: {result['current_health']}  "
                     f"α={result['current_params'].get('alpha')}  "
                     f"β={result['current_params'].get('beta')}  "
                     f"stage={result['current_params'].get('stage')}")
        lines.append("\nActive location:")
        for r in result["active_location"]:
            lines.append(f"  {r['location']}  ({r['location_type']})  "
                         f"health={r.get('health_index')}  β={r.get('beta')}")
        lines.append("\nInstallation history by platform:")
        for ih in result["install_history"]:
            lines.append(
                f"  platform={ih['platform']}  from={str(ih.get('from_time','?'))[:10]}"
                f"  to={str(ih.get('to_time','present'))[:10]}"
                f"  health={ih.get('health_index')}  α={ih.get('alpha')}  β={ih.get('beta')}"
                f"  [{ih.get('context','')[:60]}]"
            )
        lines.append("\nWorkshop visits:")
        for wv in result["workshop_visits"]:
            lines.append(
                f"  {wv['workshop']}  from={str(wv.get('from_time','?'))[:10]}"
                f"  to={str(wv.get('to_time','?'))[:10]}"
                f"  health_before={wv.get('health_before')}  after={wv.get('health_after')}"
                f"  Δ={wv.get('health_delta')}"
                f"  notes={str(wv.get('notes',''))[:60]}"
            )
        lines.append("\nFailure chain — see FMEA section:")
        for fn_id, fn in result["failure_chain"]["functions"].items():
            lines.append(f"  {fn_id}: {fn['primary_function']}")
            for fm_id in fn["failure_modes"]:
                lines.append(f"    → {fm_id}")
        return "\n".join(lines)

    # ── counterfactual dict ───────────────────────────────────────────────
    if isinstance(result, dict) and "platforms" in result:
        lines.append(f"Entity: {result['entity_id']}  {result.get('assembly_name','')}")
        lines.append(f"Pooled node params: α={result['pooled_alpha']}  β={result['pooled_beta']}")
        lines.append("\nPer-platform installation contexts:")
        for pid, pdata in result["platforms"].items():
            lines.append(f"  Platform: {pid}  ({pdata['platform_type']})")
            for ctx in pdata["installation_contexts"]:
                lines.append(
                    f"    from={str(ctx.get('from_time','?'))[:10]}"
                    f"  to={str(ctx.get('to_time','present'))[:10]}"
                    f"  health={ctx.get('health_index')}  α={ctx.get('alpha')}  β={ctx.get('beta')}"
                    f"  [{ctx.get('context','')[:60]}]"
                )
        lines.append("\nHealth trajectory (last 12 months):")
        for h in result["health_history"][-12:]:
            lines.append(f"  {str(h.get('timestamp','?'))[:10]}  value={h.get('value')}  stage={h.get('stage')}")
        lines.append("\nBeta trajectory (last 12 months):")
        for b in result["beta_history"][-12:]:
            lines.append(f"  {str(b.get('timestamp','?'))[:10]}  β={b.get('value')}  stage={b.get('stage')}")
        return "\n".join(lines)

    # ── mission hierarchy ─────────────────────────────────────────────────
    if isinstance(result, dict) and "ships" in result:
        lines.append(f"Mission: {result.get('mission_name')}  ({result.get('start')} → {result.get('end')})")
        lines.append(f"  Status={result.get('status')}  Type={result.get('type')}  Outcome={result.get('outcome')}")
        for sid, ship in result["ships"].items():
            lines.append(f"\n  Ship: {ship['ship_name']} ({sid})  class={ship['ship_class']}")
            for sys_id, sys in ship.get("systems", {}).items():
                lines.append(f"    System: {sys['system_name']} ({sys_id})")
                for eq_id, eq in sys.get("equipment", {}).items():
                    lines.append(f"      Equipment: {eq_id}  type={eq['equipment_type']}  status={eq['status']}")
                    for asm_id, asm in eq.get("assemblies", {}).items():
                        lines.append(
                            f"        Assembly: {asm_id}  {asm['assembly_name']}"
                            f"  health={asm['health_index']}"
                            f"  α={asm.get('alpha')}  β={asm.get('beta')}"
                            f"  ({asm['from_time']} → {asm['to_time']})"
                        )
                        for s in asm.get("sensors", []):
                            lines.append(
                                f"          Sensor: {s['sensor_id']}  {s['sensor_type']}"
                                f"  val={s['current_val']} {s['unit']}  threshold={s['threshold']}"
                            )
        return "\n".join(lines)

    # ── generic list of dicts (fleet handlers) ────────────────────────────
    if isinstance(result, list):
        if not result:
            lines.append("  (no results)")
        for item in result:
            lines.append("  " + "  ".join(f"{k}={v}" for k, v in item.items()
                                          if not isinstance(v, (list, dict))))
        return "\n".join(lines)

    lines.append(str(result))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 9. LLM integration
# ─────────────────────────────────────────────────────────────

_GROQ_CLIENT  = None
_GROQ_MODEL   = "llama-3.3-70b-versatile"
_GROQ_MAX_TOK = 800
_GROQ_TEMP    = 0.1

_SYSTEM_PROMPT = """You are a senior naval engineering analyst for the Indian Navy's digital twin system.
Answer questions using ONLY the provided graph context.
Be specific: cite node IDs, health values, alpha/beta degradation parameters, dates, and cost figures.
For causal questions: trace the full FMEA chain (FailureMode → Effect → Consequence → Decision).
For temporal questions: state the exact installation window that covers the anchor date.
For counterfactual questions: compare beta parameters across platforms and project health trajectories.
If the context is insufficient, say exactly which data is missing."""


def init_llm(api_key: str | None = None, model: str = _GROQ_MODEL) -> None:
    global _GROQ_CLIENT, _GROQ_MODEL
    if not _GROQ_AVAILABLE:
        raise ImportError("Run: pip install groq")
    _GROQ_CLIENT = _Groq(api_key=api_key or os.environ.get("GROQ_API_KEY", ""))
    _GROQ_MODEL  = model
    print(f"[TSR] LLM ready  model={_GROQ_MODEL}")


def ask_llm(context: str, question: str) -> str:
    if _GROQ_CLIENT is None:
        return "[LLM not initialised — call init_llm() first]"
    try:
        resp = _GROQ_CLIENT.chat.completions.create(
            model=_GROQ_MODEL,
            max_tokens=_GROQ_MAX_TOK,
            temperature=_GROQ_TEMP,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": f"Context:\n{context}\n\nQuestion: {question}"},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        return f"[LLM ERROR: {exc}]"


# ─────────────────────────────────────────────────────────────
# 10. query_pipeline  — single entry point
# ─────────────────────────────────────────────────────────────

# Maps query_type string → (tsr_function, required_kwargs)
_PIPELINE = {
    # entity-level
    "location":         (tsr_location,   ["entity_id"], ["anchor"]),
    "lifecycle":        (tsr_lifecycle,  ["entity_id"], []),
    "failure":          (tsr_failure_chain, ["entity_id"], []),
    "causal":           (tsr_causal,     ["entity_id"], ["anchor"]),
    "counterfactual":   (tsr_counterfactual, ["entity_id"], []),
    "mission":          (tsr_mission,    ["entity_id"], []),   # entity_id = mission_id
    "workshop_history": (tsr_workshop_history, ["entity_id"], []),
    "ship_health":      (tsr_ship_health, ["entity_id"], []),
    "mo_inventory":     (tsr_mo_inventory, [], []),
    # fleet-level (no entity_id required)
    "fleet_ineffective":    (tsr_fleet_workshop_ineffective, [], ["threshold"]),
    "fleet_workshop_load":  (tsr_fleet_workshop_load,  [], []),
    "fleet_in_workshop":    (tsr_fleet_in_workshop,    [], []),
    "fleet_degraded":       (tsr_fleet_degraded,       [], ["health_threshold"]),
    "fleet_redeployed":     (tsr_fleet_redeployed,     [], []),
}


def query_pipeline(
    question:   str,
    query_type: str,
    entity_id:  str | None = None,
    anchor                  = None,
    **kwargs,
) -> dict:
    """
    Full TSR → format → LLM pipeline.

    Parameters
    ----------
    question   : natural language question
    query_type : one of the keys in _PIPELINE above
    entity_id  : node ID for entity-level queries
    anchor     : ISO datetime string for temporal queries
    **kwargs   : forwarded to the TSR function (e.g. threshold=0.03)

    Returns
    -------
    {subgraph, context, llm_answer, query_type, entity_id}
    """
    if query_type not in _PIPELINE:
        raise ValueError(f"Unknown query_type '{query_type}'. Valid: {sorted(_PIPELINE)}")

    fn, req, opt = _PIPELINE[query_type]

    # Build call args
    call_kwargs = {}
    if "entity_id" in req:
        if entity_id is None:
            raise ValueError(f"query_type='{query_type}' requires entity_id")
        call_kwargs["entity_id"] = entity_id
    if "anchor" in req or "anchor" in opt:
        call_kwargs["anchor"] = anchor
    call_kwargs.update(kwargs)

    subgraph = fn(**call_kwargs)
    context  = format_for_llm(subgraph, query_type)
    answer   = ask_llm(context, question)

    return {
        "subgraph":   subgraph,
        "context":    context,
        "llm_answer": answer,
        "query_type": query_type,
        "entity_id":  entity_id,
    }


# ─────────────────────────────────────────────────────────────
# 11. CLI demo
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "query_final.json"
    load_graph(path)

    try:
        init_llm()
        llm_ok = True
    except Exception as ex:
        print(f"[TSR] LLM unavailable: {ex}")
        llm_ok = False

    demos = [
        ("location",       "ASSEMBLY_FS001", "2026-04-20T00:00:00"),
        ("location",       "ASSEMBLY_FS001", "2026-04-12T00:00:00"),
        ("failure",        "ASSEMBLY_FS001", None),
        ("causal",         "ASSEMBLY_FS001", None),
        ("counterfactual", "ASSEMBLY_FS013", None),
        ("mission",        "MISSION_M01",    None),
        ("lifecycle",      "ASSEMBLY_FS001", None),
    ]

    for qtype, eid, anchor in demos:
        print(f"\n{'='*70}")
        print(f"  {qtype.upper()}  entity={eid}  anchor={anchor}")
        print(f"{'='*70}")
        fn, req, opt = _PIPELINE[qtype]
        kw = {"entity_id": eid} if "entity_id" in req else {}
        if anchor: kw["anchor"] = anchor
        result = fn(**kw)
        print(format_for_llm(result, qtype))

    # Fleet demos
    print("\n=== FLEET: workshop_ineffective ===")
    print(format_for_llm(tsr_fleet_workshop_ineffective(), "fleet_ineffective"))

    print("\n=== FLEET: redeployed ===")
    print(format_for_llm(tsr_fleet_redeployed(), "fleet_redeployed"))

    print("\n=== FLEET: degraded (health < 0.80) ===")
    print(format_for_llm(tsr_fleet_degraded(), "fleet_degraded"))

    print("\n=== SHIP health: INS001 ===")
    print(format_for_llm(tsr_ship_health("SHIP_INS001"), "ship_health"))