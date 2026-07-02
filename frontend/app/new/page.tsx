"use client";

import React, { useEffect, useState, useCallback } from 'react';
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

import { Database, Waypoints, X, Loader2, Share2, Info } from 'lucide-react';

// --- 1. GUARANTEED CUSTOM NODE ---
const CustomNode = ({ data }: any) => {
  return (
    <div style={data.style} className="relative flex flex-col items-center justify-center rounded-lg shadow-md bg-white border-2 hover:shadow-lg transition-shadow">
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
  const [selectedItem, setSelectedItem] = useState<any>(null);
  const [isLoading, setIsLoading] = useState(true);
  
  const { fitView } = useReactFlow();

  useEffect(() => {
    const loadGraphData = async () => {
      try {
        const res = await fetch('http://localhost:8001/api/naval-graph');
        if (!res.ok) throw new Error("Failed to fetch data");
        const data = await res.json();

        // ---------------------------------------------------------
        // 1. EXTRACT & DEDUPLICATE ALL RAW NODES
        // ---------------------------------------------------------
        const uniqueNodesMap = new Map();
        
        if (data.node_types) {
          Object.values(data.node_types).forEach((group: any) => {
            if (group && Array.isArray(group.nodes)) {
              group.nodes.forEach((n: any) => {
                if (n && n.node_id) {
                  uniqueNodesMap.set(String(n.node_id).trim(), n);
                }
              });
            }
          });
        }
        const allRawNodes = Array.from(uniqueNodesMap.values());

        // ---------------------------------------------------------
        // 2. CREATE FUZZY MATCH DICTIONARY (For resolving Edge IDs)
        // ---------------------------------------------------------
        const validRawNodeIds = new Set(allRawNodes.map(n => String(n.node_id).trim()));
        const fuzzyMap = new Map();
        
        allRawNodes.forEach(n => {
           const id = String(n.node_id).trim();
           const lower = id.toLowerCase();
           const noPrefix = lower.replace(/^[^_]+_/, ''); 
           
           fuzzyMap.set(lower, id);
           fuzzyMap.set(noPrefix, id);
        });

        const resolveNodeId = (rawId: string) => {
           if (!rawId) return null;
           const clean = String(rawId).trim();
           if (validRawNodeIds.has(clean)) return clean;
           
           const lower = clean.toLowerCase();
           if (fuzzyMap.has(lower)) return fuzzyMap.get(lower);
           
           const noPrefix = lower.replace(/^[^_]+_/, '');
           if (fuzzyMap.has(noPrefix)) return fuzzyMap.get(noPrefix);
           
           return null;
        };

        // ---------------------------------------------------------
        // 3. EXTRACT EDGES (RECURSIVE SCANNER)
        // ---------------------------------------------------------
        let rawEdges: any[] = [];
        
        const findEdges = (obj: any, parentKey: string = '') => {
          if (!obj) return;
          if (typeof obj === 'object' && obj !== null && 'from' in obj && 'to' in obj) {
             rawEdges.push({ ...obj, relType: parentKey });
             return;
          }
          if (Array.isArray(obj)) {
             obj.forEach(item => findEdges(item, parentKey));
          } else if (typeof obj === 'object') {
             Object.entries(obj).forEach(([key, value]) => {
               const relationName = (key === 'edges' || key === 'relationship_types' || key === 'data') ? parentKey : key;
               findEdges(value, relationName);
             });
          }
        };

        findEdges(data);

        // ---------------------------------------------------------
        // 4. BUILD VALID EDGES & TRACK CONNECTED NODES
        // ---------------------------------------------------------
        const validEdges: Edge[] = [];
        const connectedNodeIds = new Set<string>(); // Tracks nodes that actually have edges

        rawEdges.forEach((l: any, i: number) => {
           const sourceId = resolveNodeId(l.from);
           const targetId = resolveNodeId(l.to);

           if (!sourceId || !targetId) return;

           // Save these IDs so we know which nodes to keep!
           connectedNodeIds.add(sourceId);
           connectedNodeIds.add(targetId);

           validEdges.push({
            id: `edge-${i}-${sourceId}-${targetId}`,
            source: sourceId,
            target: targetId,
            type: 'smoothstep', 
            animated: false, // Turn OFF animation so arrows render perfectly solid
            label: String(l.relType).replace(/_/g, ' '), 
            style: { stroke: '#3b82f6', strokeWidth: 2 }, // Slightly thinner line makes arrows clearer
            zIndex: 10,
            labelStyle: { fill: '#1e293b', fontWeight: 700, fontSize: 10 },
            labelBgStyle: { fill: '#ffffff', stroke: '#cbd5e1', strokeWidth: 1 },
            labelBgPadding: [6, 4],
            labelBgBorderRadius: 4,
            markerEnd: { 
                type: MarkerType.ArrowClosed, 
                color: '#3b82f6',
                width: 15, // Explicit sizing
                height: 15 
            },
            data: { fullProps: l.properties, source: sourceId, target: targetId }
          });
        });

        // ---------------------------------------------------------
        // 5. FILTER ORPHAN NODES AND GENERATE LAYOUT
        // ---------------------------------------------------------
        // Only keep nodes that exist in our connectedNodeIds Set
        const connectedNodesOnly = allRawNodes.filter(n => connectedNodeIds.has(String(n.node_id).trim()));
        
        console.log(`🧹 Filtered out ${allRawNodes.length - connectedNodesOnly.length} orphaned nodes.`);

        const layoutNodes = generateLayout(connectedNodesOnly);

        // ---------------------------------------------------------
        // 6. BUILD FINAL REACT FLOW NODES
        // ---------------------------------------------------------
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
            data: { 
              label: displayLabel, 
              category: n.type || 'Unknown',
              fullProps: n.properties,
              style: { background: theme.bg, borderColor: theme.border, width: '180px' }
            }
          };
        });

        setNodes(flowNodes);
        setEdges(validEdges);
        setIsLoading(false);
        
        setTimeout(() => fitView({ padding: 0.1, duration: 800 }), 100);
      } catch (error) {
        console.error("Error loading graph data:", error);
        setIsLoading(false);
      }
    };

    loadGraphData();
  }, [setNodes, setEdges, fitView]);

  const onNodeClick = useCallback((_: any, node: Node) => { 
      setSelectedItem({ id: node.id, type: 'NODE', category: node.data?.category, props: node.data?.fullProps }); 
  }, []);

  const onEdgeClick = useCallback((_: any, edge: Edge) => {
      setSelectedItem({ id: edge.id, type: 'EDGE', category: 'RELATIONSHIP', props: edge.data?.fullProps, source: edge.data?.source, target: edge.data?.target });
  }, []);

  return (
    <div className="flex flex-col h-screen bg-slate-50 font-sans text-slate-900">
      <header className="bg-slate-900 text-white px-6 py-4 flex items-center shadow-md z-10">
        <Share2 className="text-blue-400 mr-3" />
        <div>
           <h1 className="text-xl font-bold tracking-wide">Naval Digital Twin - Knowledge Graph</h1>
           <p className="text-xs text-slate-400">Interactive Entity & Relationship Explorer</p>
        </div>
      </header>

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
                              {Array.isArray(val) ? `View List (${val.length} items)` : 'View Details'}
                            </summary>
                            <div className="mt-2 pl-3 border-l-2 border-blue-200 space-y-2 max-h-56 overflow-y-auto pr-2">
                              {Array.isArray(val) ? val.map((item, idx) => (
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