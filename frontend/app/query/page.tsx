"use client";

import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { 
  ReactFlow, 
  MiniMap, 
  Controls, 
  Background, 
  useNodesState, 
  useEdgesState,
  MarkerType,
  ReactFlowProvider,
  useReactFlow,
  Position,
  Handle,
  type Edge,
  type Node
} from '@xyflow/react';

// @ts-ignore
import '@xyflow/react/dist/style.css';  
import * as d3 from 'd3'; 

import { Database, Waypoints, X, Loader2, Share2, Info, Search, RotateCcw, Play, AlertCircle } from 'lucide-react';

// --- Import static graph data ---
import graphData from './query_final.json';

// --- 1. GUARANTEED CUSTOM NODE ---
const CustomNode = ({ data }: any) => {
  return (
    <div style={data.style} className={`relative flex flex-col items-center justify-center rounded-lg shadow-md bg-white border-2 hover:shadow-lg transition-shadow ${data.isHighlighted ? 'ring-4 ring-blue-400 ring-opacity-60 shadow-blue-200' : ''}`}>
      <Handle type="target" position={Position.Top} className="w-3 h-3 rounded-full bg-slate-400 border-2 border-white" />
      
      <div className="font-bold text-center px-3 py-2 leading-tight w-full break-words text-[11px]">
        {data.label}
      </div>
      <div className="text-[9px] uppercase tracking-wider font-semibold opacity-75 pb-2">
        {data.category}
      </div>
      
      <Handle type="source" position={Position.Bottom} className="w-3 h-3 rounded-full bg-slate-400 border-2 border-white" />
    </div>
  );
};

const nodeTypes = { custom: CustomNode };

// --- 2. THEME & COLORS ---
const getNodeTheme = (type: string) => {
  const themes: Record<string, { bg: string; border: string }> = {
    Ship: { bg: '#EFF6FF', border: '#3B82F6' },
    Base: { bg: '#ECFEFF', border: '#06B6D4' },
    Dockyard: { bg: '#F0F9FF', border: '#0EA5E9' },
    Mission: { bg: '#F8FAFC', border: '#64748B' },
    CombatGroup: { bg: '#F1F5F9', border: '#475569' },
    OEM: { bg: '#FEF9C3', border: '#EAB308' },
    System: { bg: '#D1FAE5', border: '#10B981' },
    Equipment: { bg: '#FEF3C7', border: '#F59E0B' },
    Assembly: { bg: '#E0E7FF', border: '#6366F1' },
    SubAssembly: { bg: '#EDE9FE', border: '#8B5CF6' },
    Sensor: { bg: '#FFEDD5', border: '#F97316' },
    Workshop: { bg: '#F5F5F4', border: '#A8A29E' },
    MaterialOrganisation: { bg: '#F3E8FF', border: '#A855F7' },
    Function: { bg: '#CCFBF1', border: '#14B8A6' },
    FailureMode: { bg: '#FEE2E2', border: '#EF4444' },
    Effect: { bg: '#FCE7F3', border: '#EC4899' },
    Consequence: { bg: '#FFE4E6', border: '#E11D48' },
    Decision: { bg: '#DCFCE7', border: '#22C55E' }
  };
  return themes[type] || { bg: '#F3F4F6', border: '#9CA3AF' };
};

// --- 3. LAYOUT ENGINE ---
const generateLayout = (nodes: any[]) => {
  const grouped: Record<string, any[]> = {};
  
  nodes.forEach(n => {
    const t = n.type || 'Unknown';
    if (!grouped[t]) grouped[t] = [];
    grouped[t].push(n);
  });

  const rowTiers = [
    ['Ship', 'Base', 'Dockyard', 'CombatGroup', 'Mission', 'OEM'],
    ['System', 'MaterialOrganisation', 'Workshop'],
    ['Equipment'],
    ['Assembly'],
    ['SubAssembly', 'Sensor'],
    ['Function', 'FailureMode'],
    ['Effect', 'Consequence', 'Decision']
  ];

  const positionedNodes: any[] = [];
  let currentY = 0;
  const SPACING_X = 260;
  const SPACING_Y = 220;

  rowTiers.forEach(tier => {
    let maxSubRowsInTier = 0;
    let currentX = 0;

    tier.forEach(type => {
      const typeNodes = grouped[type] || [];
      if (typeNodes.length === 0) return;

      typeNodes.forEach((node, index) => {
        const columns = 5; 
        const col = index % columns;
        const subRow = Math.floor(index / columns);
        
        if (subRow > maxSubRowsInTier) maxSubRowsInTier = subRow;

        positionedNodes.push({
          ...node,
          position: { x: currentX + (col * SPACING_X), y: currentY + (subRow * SPACING_Y) }
        });
      });

      currentX += (Math.min(typeNodes.length, 5) * SPACING_X) + 100; 
      delete grouped[type];
    });

    if (currentX > 0) currentY += (maxSubRowsInTier + 1) * SPACING_Y + 100;
  });

  let leftoverX = 0;
  Object.values(grouped).flat().forEach((node, idx) => {
      positionedNodes.push({
        ...node,
        position: { x: leftoverX, y: currentY + Math.floor(idx/5) * SPACING_Y }
      });
      leftoverX += SPACING_X;
      if (leftoverX > 1500) leftoverX = 0;
  });

  return positionedNodes;
};

// --- 4. MAIN COMPONENT ---
function DigitalTwinContent() {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  
  const [fullNodes, setFullNodes] = useState<Node[]>([]);
  const [fullEdges, setFullEdges] = useState<Edge[]>([]);
  
  const [query, setQuery] = useState('MATCH (s:Ship)-[:HAS_SYSTEM]->(sys:System)\nRETURN s, sys');
  const [queryError, setQueryError] = useState<string | null>(null);
  
  const [selectedItem, setSelectedItem] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(true);
  
  const { fitView } = useReactFlow();

  // --- O(1) Adjacency Maps for Fast Traversal ---
  const { outgoingAdj, incomingAdj } = useMemo(() => {
    const outMap = new Map<string, Edge[]>();
    const inMap = new Map<string, Edge[]>();
    
    fullEdges.forEach(e => {
        if (!outMap.has(e.source)) outMap.set(e.source, []);
        outMap.get(e.source)!.push(e);
        
        if (!inMap.has(e.target)) inMap.set(e.target, []);
        inMap.get(e.target)!.push(e);
    });
    
    return { outgoingAdj: outMap, incomingAdj: inMap };
  }, [fullEdges]);

  useEffect(() => {
    const loadGraphData = () => {
      try {
        const data = graphData as any;

        // ── Build node map from node_types ──
        const uniqueNodesMap = new Map<string, any>();
        if (data.node_types) {
          Object.entries(data.node_types).forEach(([typeName, group]: [string, any]) => {
            if (group && Array.isArray(group.nodes)) {
              group.nodes.forEach((n: any) => {
                if (n && n.node_id) {
                  // Attach the type from the key if not already present
                  uniqueNodesMap.set(String(n.node_id).trim(), { ...n, type: n.type || typeName });
                }
              });
            }
          });
        }
        const allRawNodes = Array.from(uniqueNodesMap.values());

        const validRawNodeIds = new Set(allRawNodes.map(n => String(n.node_id).trim()));
        const fuzzyMap = new Map<string, string>();
        
        allRawNodes.forEach(n => {
           const id = String(n.node_id).trim();
           const lower = id.toLowerCase();
           const noPrefix = lower.replace(/^[^_]+_/, ''); 
           fuzzyMap.set(lower, id);
           fuzzyMap.set(noPrefix, id);
        });

        const resolveNodeId = (rawId: string): string | null => {
           if (!rawId) return null;
           const clean = String(rawId).trim();
           if (validRawNodeIds.has(clean)) return clean;
           const lower = clean.toLowerCase();
           if (fuzzyMap.has(lower)) return fuzzyMap.get(lower)!;
           const noPrefix = lower.replace(/^[^_]+_/, '');
           if (fuzzyMap.has(noPrefix)) return fuzzyMap.get(noPrefix)!;
           return null;
        };

        // ── Build edges from relationship_types ──
        // Each key in relationship_types is the relationship name (e.g. "INSTALLED_ON")
        // Each has an "edges" array with { from, to, properties }
        const validEdges: Edge[] = [];
        const connectedNodeIds = new Set<string>();

        if (data.relationship_types) {
          Object.entries(data.relationship_types).forEach(([relType, relGroup]: [string, any]) => {
            const edgeArray: any[] = relGroup?.edges || [];
            edgeArray.forEach((l: any, i: number) => {
              const sourceId = resolveNodeId(l.from);
              const targetId = resolveNodeId(l.to);
              if (!sourceId || !targetId) return;

              connectedNodeIds.add(sourceId);
              connectedNodeIds.add(targetId);

              validEdges.push({
                id: `edge-${relType}-${i}-${sourceId}-${targetId}`,
                source: sourceId,
                target: targetId,
                type: 'smoothstep',
                animated: false,
                label: relType.replace(/_/g, ' '),
                style: { stroke: '#94a3b8', strokeWidth: 1.5, opacity: 1 },
                zIndex: 10,
                labelStyle: { fill: '#475569', fontWeight: 600, fontSize: 9 },
                labelBgStyle: { fill: '#f8fafc', stroke: '#e2e8f0', strokeWidth: 1 },
                labelBgPadding: [6, 4] as [number, number],
                labelBgBorderRadius: 4,
                markerEnd: { 
                    type: MarkerType.ArrowClosed, 
                    color: '#94a3b8',
                    width: 15, 
                    height: 15 
                },
                data: { fullProps: l.properties, source: sourceId, target: targetId, originalType: relType }
              });
            });
          });
        }

        // ── Layout only nodes that appear in at least one edge ──
        const connectedNodesOnly = allRawNodes.filter(n => connectedNodeIds.has(String(n.node_id).trim()));
        const layoutNodes = generateLayout(connectedNodesOnly);

        const flowNodes: Node[] = layoutNodes.map((n: any) => {
          const theme = getNodeTheme(n.type);
          let displayLabel = n.node_id;
          if (n.properties) {
            displayLabel = n.properties.name || n.properties.system_name || n.properties.assembly_name || n.properties.equipment_id || n.node_id;
          }

          return {
            id: String(n.node_id).trim(),
            position: n.position,
            type: 'custom',
            style: { opacity: 1 }, 
            data: { 
              label: displayLabel, 
              category: n.type || 'Unknown',
              fullProps: n.properties,
              style: { background: theme.bg, borderColor: theme.border, width: '180px' },
              isHighlighted: false
            }
          };
        });

        setNodes(flowNodes);
        setEdges(validEdges);
        setFullNodes(flowNodes);
        setFullEdges(validEdges);
        
        setIsLoading(false);
        setTimeout(() => fitView({ padding: 0.1, duration: 800 }), 100);
      } catch (error) {
        console.error("Error loading graph data:", error);
        setIsLoading(false);
      }
    };

    loadGraphData();
  }, [setNodes, setEdges, fitView]);

  // --- CYPHER EXECUTION ENGINE (MULTI-HOP & BRANCHING) ---
  const executeCypher = () => {
    setQueryError(null);
    if (!query.trim().toUpperCase().startsWith('MATCH')) {
        setQueryError("Syntax Error: Query must start with MATCH.");
        return;
    }

    // 1. Separate RETURN clause from the rest of the query
    const returnMatch = query.match(/\s+RETURN\s+(.+)$/i);
    const returnVars = returnMatch ? returnMatch[1].split(',').map(v => v.trim()) : [];
    const matchString = returnMatch ? query.substring(0, returnMatch.index) : query;

    // 2. Tokenize Clauses (MATCH and OPTIONAL MATCH) safely
    const queryParts = matchString.split(/(?=\bMATCH\b|\bOPTIONAL\s+MATCH\b)/i);
    const clauses: { isOptional: boolean, patternStr: string }[] = [];
    
    for (const part of queryParts) {
        const trimmed = part.trim();
        if (!trimmed) continue;
        
        const isOpt = trimmed.toUpperCase().startsWith('OPTIONAL MATCH');
        const prefixLen = isOpt ? 14 : (trimmed.toUpperCase().startsWith('MATCH') ? 5 : 0);
        
        if (prefixLen === 0) continue;
        
        clauses.push({
            isOptional: isOpt,
            patternStr: trimmed.substring(prefixLen).trim()
        });
    }

    if (clauses.length === 0) {
        setQueryError("Syntax Error: Could not parse MATCH clauses.");
        return;
    }

    // Advanced Property Parser
    const parseProps = (propStr: string | undefined) => {
        if (!propStr) return {};
        const props: Record<string, any> = {};
        const propMatches = propStr.match(/(\w+)\s*:\s*("[^"]+"|'[^']+'|[^,}]+)/g);
        if (propMatches) {
            propMatches.forEach(pm => {
                const idx = pm.indexOf(':');
                const k = pm.slice(0, idx).trim();
                let v: any = pm.slice(idx + 1).trim();
                if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
                    v = v.slice(1, -1);
                } else if (v === 'true') v = true;
                else if (v === 'false') v = false;
                else if (!isNaN(Number(v))) v = Number(v);
                props[k] = v;
            });
        }
        return props;
    };

    const parseNodeToken = (str: string) => {
        const m = str.match(/\(\s*(\w+)?\s*(?::\s*(\w+))?\s*(?:\{\s*([^}]+)\s*\})?\s*\)/);
        if (!m) return null;
        return { variable: m[1], label: m[2], props: parseProps(m[3]) };
    };

    const parseEdgeToken = (str: string) => {
        const isReverse = str.startsWith('<-');
        if (str === '-->' || str === '<--') return { type: null, isReverse, props: {} };
        const m = str.match(/-\[\s*(.*?)\s*\]->|<-\[\s*(.*?)\s*\]-/);
        const inner = m ? (m[1] || m[2]) : "";
        const innerM = inner?.match(/(\w+)?\s*(?::\s*(\w+))?\s*(?:\{\s*([^}]+)\s*\})?/);
        return { variable: innerM ? innerM[1] : null, type: innerM ? innerM[2] : null, props: innerM ? parseProps(innerM[3]) : {}, isReverse };
    };

    // Validates if a React Flow Node matches the Cypher Node Rule
    const nodeMatchesRule = (flowNode: Node, rule: any) => {
        const nodeData = flowNode.data as Record<string, any>;
        const fullProps = (nodeData.fullProps || {}) as Record<string, any>;

        if (rule.label && nodeData.category !== rule.label) {
            if (!flowNode.id.toUpperCase().startsWith(rule.label.toUpperCase())) return false;
        }
        
        if (rule.props && Object.keys(rule.props).length > 0) {
            for (const [key, val] of Object.entries(rule.props)) {
                const nodeVal = key === 'node_id' ? (flowNode.id || fullProps.node_id) : fullProps[key];
                if (String(nodeVal).toLowerCase() !== String(val).toLowerCase()) return false;
            }
        }
        return true;
    };

    // 3. Traversal Context State
    let currentContexts = [ { vars: {} as Record<string, Node>, nodes: new Set<string>(), edges: new Set<string>() } ];

    // 4. Clause Execution Engine
    for (const clause of clauses) {
        const paths = clause.patternStr.split(/,(?![^\{]*\})/g).map(s => s.trim()).filter(Boolean);
        let nextGlobalContexts: any[] = [];

        for (const ctx of currentContexts) {
            let pathContexts = [ctx];
            let clauseFailed = false;

            for (const pathStr of paths) {
                const tokens = pathStr.match(/(\([^)]+\)|<-\[.*?\]-|-\[.*?\]->|<--|-->)/g);
                if (!tokens) continue;
                const parsedSteps = tokens.map(t => t.startsWith('(') ? parseNodeToken(t) : parseEdgeToken(t));
                if (parsedSteps.length === 0) continue;

                let nextPathContexts: any[] = [];

                for (const pCtx of pathContexts) {
                    const startRule = parsedSteps[0] as any;
                    let validStarts: Node[] = [];

                    if (startRule.variable && pCtx.vars[startRule.variable]) {
                        const boundNode = pCtx.vars[startRule.variable];
                        if (nodeMatchesRule(boundNode, startRule)) validStarts.push(boundNode);
                    } else {
                        validStarts = fullNodes.filter(n => nodeMatchesRule(n, startRule));
                    }

                    let pathStates = validStarts.map(startNode => {
                        const newVars = { ...pCtx.vars };
                        if (startRule.variable) newVars[startRule.variable] = startNode;
                        const newNodes = new Set(pCtx.nodes);
                        newNodes.add(startNode.id);
                        return { vars: newVars, nodes: newNodes, edges: new Set(pCtx.edges), currentNode: startNode };
                    });

                    for (let i = 1; i < parsedSteps.length; i += 2) {
                        const edgeRule = parsedSteps[i] as any;
                        const targetNodeRule = parsedSteps[i + 1] as any;
                        if (!targetNodeRule) break;

                        let nextPathStates: any[] = [];
                        
                        for (const state of pathStates) {
                            const cNode = state.currentNode;
                            const candidateEdges = edgeRule.isReverse ? (incomingAdj.get(cNode.id) || []) : (outgoingAdj.get(cNode.id) || []);

                            const matchedEdges = candidateEdges.filter(e => {
                                const edgeData = e.data as Record<string, any>;
                                if (edgeRule.type && (edgeData.originalType || '').toLowerCase() !== edgeRule.type.toLowerCase()) return false;
                                if (edgeRule.props && Object.keys(edgeRule.props).length > 0) {
                                    const fullProps = (edgeData.fullProps || {}) as Record<string, any>;
                                    for (const [key, val] of Object.entries(edgeRule.props)) {
                                        if (String(fullProps[key]).toLowerCase() !== String(val).toLowerCase()) return false;
                                    }
                                }
                                return true;
                            });

                            for (const edge of matchedEdges) {
                                const targetNodeId = edgeRule.isReverse ? edge.source : edge.target;
                                const targetNode = fullNodes.find(n => n.id === targetNodeId);
                                
                                if (targetNode && nodeMatchesRule(targetNode, targetNodeRule)) {
                                    if (targetNodeRule.variable && state.vars[targetNodeRule.variable] && state.vars[targetNodeRule.variable].id !== targetNode.id) {
                                        continue;
                                    }

                                    const newVars = { ...state.vars };
                                    if (targetNodeRule.variable) newVars[targetNodeRule.variable] = targetNode;
                                    const newNodes = new Set(state.nodes);
                                    newNodes.add(targetNode.id);
                                    const newEdges = new Set(state.edges);
                                    newEdges.add(edge.id);

                                    nextPathStates.push({ vars: newVars, nodes: newNodes, edges: newEdges, currentNode: targetNode });
                                }
                            }
                        }
                        pathStates = nextPathStates;
                    }
                    nextPathContexts.push(...pathStates);
                }
                
                pathContexts = nextPathContexts;
                if (pathContexts.length === 0) {
                    clauseFailed = true;
                    break;
                }
            }

            if (clauseFailed) {
                if (clause.isOptional) {
                    nextGlobalContexts.push(ctx);
                }
            } else {
                nextGlobalContexts.push(...pathContexts);
            }
        }
        currentContexts = nextGlobalContexts;
    }

    if (currentContexts.length === 0) {
        setQueryError("No paths matched the given query.");
        return;
    }

    // 5. Final Subgraph Merge & Verification
    const finalNodeIds = new Set<string>();
    const finalEdgeIds = new Set<string>();

    currentContexts.forEach(ctx => {
        if (returnVars.length > 0 && returnVars[0] !== '*') {
            returnVars.forEach(v => {
                if (ctx.vars[v]) finalNodeIds.add(ctx.vars[v].id);
            });
        } else {
            ctx.nodes.forEach(id => finalNodeIds.add(id));
        }
    });

    currentContexts.forEach(ctx => {
        ctx.edges.forEach(edgeId => {
            const edge = fullEdges.find(e => e.id === edgeId);
            if (edge && finalNodeIds.has(edge.source) && finalNodeIds.has(edge.target)) {
                finalEdgeIds.add(edgeId);
            }
        });
    });

    // Visual Transition using D3
    const nonMatchedNodes = d3.selectAll('.react-flow__node').filter(function(this: Element) {
        const d3Datum: any = d3.select(this).datum();
        const id = d3Datum?.id || (this as any).__data__?.id || this.getAttribute('data-id');
        return id ? !finalNodeIds.has(id) : true;
    });

    const nonMatchedEdges = d3.selectAll('.react-flow__edge').filter(function(this: Element) {
        const d3Datum: any = d3.select(this).datum();
        const id = d3Datum?.id || (this as any).__data__?.id || this.getAttribute('data-id');
        return id ? !finalEdgeIds.has(id) : true;
    });

    const applyReactFlowState = () => {
        const filteredNodes = fullNodes
            .filter(n => finalNodeIds.has(n.id))
            .map(n => ({ ...n, style: { opacity: 1 }, data: { ...n.data, isHighlighted: true } }));

        const filteredEdges = fullEdges
            .filter(e => finalEdgeIds.has(e.id))
            .map(e => ({ ...e, animated: true, style: { stroke: '#3B82F6', strokeWidth: 2.5, opacity: 1 }, markerEnd: { type: MarkerType.ArrowClosed, color: '#3B82F6', width: 15, height: 15 } }));

        setNodes(filteredNodes);
        setEdges(filteredEdges);
        setSelectedItem(null);
        setTimeout(() => fitView({ padding: 0.2, duration: 800 }), 100);
    };

    if (!nonMatchedNodes.empty() || !nonMatchedEdges.empty()) {
        nonMatchedNodes.transition().duration(400).style('opacity', 0);
        nonMatchedEdges.transition().duration(400).style('opacity', 0);
        setTimeout(applyReactFlowState, 400); 
    } else {
        applyReactFlowState();
    }
  };

  const resetGraph = () => {
      d3.selectAll('.react-flow__node, .react-flow__edge').style('opacity', 1);
      setNodes(fullNodes.map(n => ({
          ...n,
          data: { ...n.data, isHighlighted: false }
      })));
      setEdges(fullEdges.map(e => ({
          ...e,
          animated: false,
          style: { stroke: '#94a3b8', strokeWidth: 1.5, opacity: 1 },
          markerEnd: { type: MarkerType.ArrowClosed, color: '#94a3b8', width: 15, height: 15 }
      })));
      
      setSelectedItem(null);
      setQueryError(null);
      setTimeout(() => fitView({ padding: 0.1, duration: 800 }), 100);
  };

  const onNodeClick = useCallback((_: any, node: Node) => { 
      const data = node.data as Record<string, any>;
      setSelectedItem({ id: node.id, type: 'NODE', category: data?.category, props: data?.fullProps }); 
  }, []);

  const onEdgeClick = useCallback((_: any, edge: Edge) => {
      const data = edge.data as Record<string, any>;
      setSelectedItem({ id: edge.id, type: 'EDGE', category: 'RELATIONSHIP', props: data?.fullProps, source: data?.source, target: data?.target });
  }, []);

  return (
    <div className="flex flex-col h-screen bg-slate-50 font-sans text-slate-900">
      <header className="bg-slate-900 text-white px-6 py-4 flex items-center shadow-md z-20">
        <Share2 className="text-blue-400 mr-3" />
        <div>
           <h1 className="text-xl font-bold tracking-wide">Naval Digital Twin - Knowledge Graph</h1>
           <p className="text-xs text-slate-400">Interactive Entity &amp; Relationship Explorer</p>
        </div>
      </header>

      {/* CYPHER QUERY PANEL */}
      <div className="bg-white border-b border-slate-200 px-6 py-3 shadow-sm z-10 relative">
          <div className="flex gap-3 items-center">
              <Search className="text-slate-400" size={18} />
              <div className="flex-grow">
                  <textarea
                      value={query}
                      onChange={(e) => { setQuery(e.target.value); setQueryError(null); }}
                      onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) {
                              e.preventDefault();
                              executeCypher();
                          }
                      }}
                      rows={3}
                      className={`w-full bg-slate-50 font-mono text-sm border rounded-md px-3 py-2 focus:outline-none focus:ring-1 transition-all shadow-inner resize-y ${queryError ? 'border-red-300 focus:border-red-500 focus:ring-red-500' : 'border-slate-300 focus:border-blue-500 focus:ring-blue-500'}`}
                      placeholder={'MATCH (s:Ship)-[:HAS_SYSTEM]->(sys:System)\nRETURN s, sys'}
                  />
              </div>
              <div className="flex flex-col gap-2">
                  <button 
                      onClick={executeCypher} 
                      className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-semibold flex items-center gap-2 transition-colors shadow-sm"
                  >
                     <Play size={14} fill="currentColor" /> Run Query
                  </button>
                  <button 
                      onClick={resetGraph} 
                      className="bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300 px-4 py-2 rounded-md text-sm font-semibold flex items-center gap-2 transition-colors shadow-sm"
                  >
                     <RotateCcw size={14} /> Reset Graph
                  </button>
              </div>
          </div>
          {queryError && (
              <div className="flex items-center gap-1 text-red-500 text-xs mt-2 font-medium">
                  <AlertCircle size={12} /> {queryError}
              </div>
          )}
      </div>

      <div className="flex-grow relative w-full h-full">
        {isLoading ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
                <Loader2 className="animate-spin mb-3 text-slate-900" size={40} />
                <p>Constructing Twin Environment...</p>
             </div>
        ) : (
          <ReactFlow 
            nodes={nodes} 
            edges={edges} 
            nodeTypes={nodeTypes} 
            onNodesChange={onNodesChange} 
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            onEdgeClick={onEdgeClick}
            minZoom={0.05}
          >
            <Controls className="bg-white shadow-md border border-slate-200" />
            <MiniMap className="border border-slate-300 shadow-sm rounded-lg bg-white" nodeStrokeWidth={3} zoomable pannable />
            <Background color="#cbd5e1" gap={25} size={1} />
          </ReactFlow>
        )}

        {/* SIDEBAR PANEL */}
        {selectedItem && (
          <div className="absolute top-4 right-4 w-96 max-h-[calc(100vh-100px)] bg-white shadow-2xl rounded-xl border border-slate-200 flex flex-col overflow-hidden animate-in slide-in-from-right z-50">
            <div className={`p-4 border-b flex justify-between items-center ${selectedItem.type === 'EDGE' ? 'bg-indigo-50' : 'bg-slate-100'}`}>
              <div className="flex items-center gap-2 text-slate-800 font-bold">
                {selectedItem.type === 'EDGE' ? <Waypoints size={16}/> : <Database size={16} />}
                <span>{selectedItem.category || "Details"}</span>
              </div>
              <button onClick={() => setSelectedItem(null)} className="text-slate-500 hover:text-red-600 transition-colors">
                <X size={18} />
              </button>
            </div>
            
            <div className="p-4 overflow-y-auto flex-grow bg-white">
              <div className="mb-4">
                  <span className="text-[10px] font-mono bg-slate-100 border border-slate-200 text-slate-600 px-2 py-1 rounded break-all shadow-sm">
                      ID: {selectedItem.id}
                  </span>
              </div>

              {selectedItem.type === 'EDGE' && selectedItem.source && selectedItem.target && (
                  <div className="mb-5 space-y-1">
                      <div className="text-[10px] text-slate-500 font-bold uppercase tracking-wider">Direction</div>
                      <div className="text-xs bg-slate-50 p-3 rounded-lg border border-slate-100 flex flex-col items-center gap-1 shadow-inner">
                          <span className="font-mono text-blue-600 break-words text-center">{selectedItem.source}</span>
                          <span className="text-slate-300">▼</span>
                          <span className="font-mono text-emerald-600 break-words text-center">{selectedItem.target}</span>
                      </div>
                  </div>
              )}

              <div className="text-[10px] text-slate-500 font-bold uppercase tracking-wider mb-2">Properties</div>
              {(!selectedItem.props || Object.keys(selectedItem.props).length === 0) ? (
                  <p className="text-sm text-slate-400 italic">No properties available.</p>
              ) : (
                <div className="space-y-3 text-sm">
                  {Object.entries(selectedItem.props).map(([key, val]) => {
                    if (val === null || val === undefined || val === '') return null;

                    return (
                      <div key={key} className="border-b border-slate-50 pb-2 last:border-0 hover:bg-slate-50/50 rounded transition-colors px-1">
                        <span className="block font-semibold text-slate-700 capitalize mb-1 text-xs">
                          {key.replace(/_/g, ' ')}
                        </span>
                        
                        {(Array.isArray(val) || typeof val === 'object') ? (
                          <details className="cursor-pointer group mt-1">
                            <summary className="text-blue-600 text-xs font-semibold hover:text-blue-800 transition-colors flex items-center select-none">
                              <Info size={12} className="mr-1.5 inline" />
                              {Array.isArray(val) ? `View List (${(val as any[]).length} items)` : 'View Details'}
                            </summary>
                            <div className="mt-2 pl-3 border-l-2 border-blue-200 space-y-2 max-h-56 overflow-y-auto pr-2">
                              {Array.isArray(val) ? (val as any[]).map((item: any, idx: number) => (
                                <div key={idx} className="bg-slate-50 p-2.5 rounded border border-slate-100 text-xs text-slate-700 font-mono shadow-sm">
                                  {typeof item === 'object' ? JSON.stringify(item, null, 2) : String(item)}
                                </div>
                              )) : (
                                <pre className="bg-slate-50 p-2.5 rounded border border-slate-100 text-[10px] text-slate-700 overflow-x-auto shadow-sm whitespace-pre-wrap">
                                  {JSON.stringify(val, null, 2)}
                                </pre>
                              )}
                            </div>
                          </details>
                        ) : (
                          <span className="text-slate-600 break-words text-xs font-medium">
                             {String(val)}
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function DigitalTwinPage() {
    return <ReactFlowProvider><DigitalTwinContent /></ReactFlowProvider>;
}