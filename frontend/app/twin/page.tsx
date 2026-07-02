"use client";

import React, { useEffect, useState, useCallback } from 'react';
import ReactFlow, { 
  MiniMap, 
  Controls, 
  Background, 
  useNodesState, 
  useEdgesState,
  MarkerType,
  ReactFlowProvider,
  EdgeLabelRenderer,
  BaseEdge,
  getStraightPath,
  useReactFlow,
  Edge
} from 'reactflow';
import 'reactflow/dist/style.css';
import { Bot, User, Loader2, X, Share2, Database, Waypoints } from 'lucide-react';

// --- 1. CUSTOM EDGE COMPONENT (Neo4j Style) ---
const Neo4jEdge = ({ id, sourceX, sourceY, targetX, targetY, label, style, markerEnd }: any) => {
  if (typeof sourceX !== 'number' || typeof sourceY !== 'number' || 
      typeof targetX !== 'number' || typeof targetY !== 'number') {
      return null;
  }

  const [edgePath, labelX, labelY] = getStraightPath({ sourceX, sourceY, targetX, targetY });

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            background: 'white',
            padding: '2px 6px',
            borderRadius: '6px',
            fontSize: '9px',
            fontWeight: 700,
            color: '#374151',
            border: '1px solid #E5E7EB',
            cursor: 'pointer',
            pointerEvents: 'all',
            boxShadow: '0 1px 2px rgba(0,0,0,0.05)',
            zIndex: 10,
            whiteSpace: 'nowrap'
          }}
          className="nodrag"
        >
          {label}
        </div>
      </EdgeLabelRenderer>
    </>
  );
};

const edgeTypes = { neo4j: Neo4jEdge };

// --- 2. LAYOUT ENGINE (Spaced Out Clusters) ---
const getClusterLayout = (nodes: any[], edges: any[]) => {
  const GRID_COLS = 4;
  const CLUSTER_SPACING_X = 1200; 
  const CLUSTER_SPACING_Y = 1000;
  const ORBIT_RADIUS = 350;

  const motors = nodes.filter(n => n.labels.some((l: string) => l.includes("Motor")));
  const motorIds = new Set(motors.map(m => m.id));
  
  const nodeParents: Record<string, Set<string>> = {};
  motors.forEach(m => nodeParents[m.id] = new Set([m.id]));

  edges.forEach(e => {
    if (motorIds.has(e.source)) {
       if (!nodeParents[e.target]) nodeParents[e.target] = new Set();
       nodeParents[e.target].add(e.source);
    }
    if (motorIds.has(e.target)) {
       if (!nodeParents[e.source]) nodeParents[e.source] = new Set();
       nodeParents[e.source].add(e.target);
    }
  });

  const positionedNodes: any[] = [];
  const motorPositions: Record<string, {x: number, y: number}> = {};

  motors.forEach((motor, index) => {
    const x = (index % GRID_COLS) * CLUSTER_SPACING_X;
    const y = Math.floor(index / GRID_COLS) * CLUSTER_SPACING_Y;
    motorPositions[motor.id] = { x, y };
    positionedNodes.push({ ...motor, position: { x, y } });
  });

  const satellites = nodes.filter(n => !motorIds.has(n.id));
  
  satellites.forEach(node => {
      const parents = Array.from(nodeParents[node.id] || []);
      if (parents.length === 1) {
          const parentPos = motorPositions[parents[0]];
          const angle = Math.random() * 2 * Math.PI;
          positionedNodes.push({
              ...node,
              position: { x: parentPos.x + Math.cos(angle) * ORBIT_RADIUS, y: parentPos.y + Math.sin(angle) * ORBIT_RADIUS }
          });
      } else if (parents.length > 1) {
          let avgX = 0, avgY = 0, validParents = 0;
          parents.forEach(pid => {
              if (motorPositions[pid]) {
                  avgX += motorPositions[pid].x;
                  avgY += motorPositions[pid].y;
                  validParents++;
              }
          });
          positionedNodes.push({
              ...node,
              position: validParents > 0 ? { x: avgX / validParents, y: avgY / validParents } : { x: 0, y: 0 }
          });
      } else {
          positionedNodes.push({
              ...node,
              position: { x: Math.random() * 2000, y: 3000 + Math.random() * 500 }
          });
      }
  });

  return positionedNodes;
};

function DigitalTwinContent() {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedItem, setSelectedItem] = useState<any>(null);
  const [activeFilter, setActiveFilter] = useState("ALL");
  const [query, setQuery] = useState('');
  const [chatHistory, setChatHistory] = useState<{role: string, text: string}[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [graphStatus, setGraphStatus] = useState("Loading...");
  
  // ---> ADDED setCenter HERE <---
  const { fitView, setCenter } = useReactFlow();

  // 1. DATA LOAD
  useEffect(() => {
    fetch('http://localhost:8001/api/graph')
      .then(res => res.json())
      .then(data => {
        if (!data || !data.nodes) return;

        const layoutNodes = getClusterLayout(data.nodes, data.links || []);

        const flowNodes = layoutNodes.map((n: any) => {
          const labels = (n.labels || []).map((l: string) => l.trim());
          const isMotor = labels.includes("Motor");
          const isIdentity = labels.includes("Identity");
          const isEvent = labels.includes("Event");

          let bg = "#F3F4F6"; let border = "#9CA3AF"; let size = 70; let fontSize = 9;
          let label = n.id;

          if (isMotor) { 
            bg = "#FACC15"; border = "#EAB308"; size = 110; fontSize = 12;
            label = n.properties?.motor_uid || "Motor";
          } else if (isIdentity) { 
            bg = "#60A5FA"; border = "#2563EB"; size = 80;
            label = n.properties?.identity_id || "Loc";
          } else if (isEvent) { 
            bg = "#4ADE80"; border = "#16A34A"; size = 80;
            label = n.properties?.type || "Event";
          }

          return {
            id: String(n.id),
            position: n.position,
            data: { 
              label: <div className="text-center font-bold px-1 break-all" style={{fontSize: `${fontSize}px`}}>{label}</div>,
              fullProps: n.properties,
              category: isMotor ? "Motor" : isIdentity ? "Identity" : "Event",
              isMotor: isMotor
            },
            style: {
              background: bg, border: `3px solid ${border}`, borderRadius: '50%',
              width: size, height: size,
              boxShadow: '0 4px 6px -1px rgba(0,0,0,0.15)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              zIndex: isMotor ? 20 : 10
            }
          };
        });

        const flowEdges = (data.links || []).map((l: any, i: number) => ({
          id: `e-${i}`,
          source: String(l.source),
          target: String(l.target),
          type: 'neo4j',
          label: l.type,
          data: { label: l.type, fullProps: l.properties, source: l.source, target: l.target },
          style: { stroke: '#9CA3AF', strokeWidth: 1.5, cursor: 'pointer' },
          markerEnd: { type: MarkerType.ArrowClosed, color: '#9CA3AF' },
        }));

        setNodes(flowNodes);
        setEdges(flowEdges);
        setGraphStatus("");
        
        // ---> CHANGED LOGIC HERE: Zoom in on the first motor instead of fitting the whole view <---
        setTimeout(() => {
          // Sort motors to reliably find MTR_001
          const sortedMotors = flowNodes.filter((n: any) => n.data.isMotor).sort((a, b) => a.id.localeCompare(b.id));
          if (sortedMotors.length > 0) {
             const firstMotor = sortedMotors[0];
             // zoom: 0.85 sets a nice close-up view centered directly on the motor
             setCenter(firstMotor.position.x, firstMotor.position.y, { zoom: 0.5, duration: 1000 });
          } else {
             fitView({ padding: 0.2 }); // Fallback if no motors exist
          }
        }, 100);
      })
      .catch(err => {
        console.error(err);
        setGraphStatus("Connection Error.");
      });
  }, [setNodes, setEdges, fitView, setCenter]);

  // --- 3. GRAPH TRAVERSAL FILTER ---
  // --- 3. GRAPH TRAVERSAL FILTER (WITH AGGRESSIVE ZOOM) ---
  const handleFilter = (motorId: string) => {
    setActiveFilter(motorId);
    
    if (motorId === "ALL") {
       // Show all nodes and zoom out to see the entire universe
       setNodes((nds) => nds.map((n) => ({...n, hidden: false })));
       setEdges((eds) => eds.map((e) => ({...e, hidden: false })));
       setTimeout(() => fitView({ duration: 800, padding: 0.2 }), 100);
    } else {
       // 1. Hide unrelated nodes to isolate the specific Motor Lifecycle
       setEdges((currentEdges) => {
           const identityIds = new Set<string>();
           currentEdges.forEach(e => {
               if (e.source === motorId) identityIds.add(e.target);
               if (e.target === motorId) identityIds.add(e.source);
           });

           const eventIds = new Set<string>();
           currentEdges.forEach(e => {
               if (identityIds.has(e.source)) eventIds.add(e.target);
               if (identityIds.has(e.target)) eventIds.add(e.source);
           });

           const allowedNodeIds = new Set([motorId, ...Array.from(identityIds), ...Array.from(eventIds)]);

           setNodes((currentNodes) => currentNodes.map(n => ({
               ...n,
               hidden: !allowedNodeIds.has(n.id)
           })));

           return currentEdges.map(e => ({
               ...e,
               hidden: !(allowedNodeIds.has(e.source) && allowedNodeIds.has(e.target))
           }));
       });

       // 2. FLY TO AND ZOOM IN ON THE SELECTED MOTOR
       const targetMotor = nodes.find(n => n.id === motorId);
       if (targetMotor) {
           setTimeout(() => {
               // zoom: 1.5 gives a beautiful close-up of the specific motor lifecycle
               setCenter(targetMotor.position.x, targetMotor.position.y, { 
                 zoom: 0.9, 
                 duration: 1000 // Smooth 1-second fly-in animation
               });
           }, 100);
       }
    }
  };

  const onNodeClick = useCallback((_: any, node: any) => { 
      setSelectedItem({ id: node.id, type: 'NODE', category: node.data.category, props: node.data.fullProps }); 
  }, []);

  const onEdgeClick = useCallback((_: any, edge: Edge) => {
      setSelectedItem({ id: edge.data.label, type: 'EDGE', category: 'RELATIONSHIP', props: edge.data.fullProps, source: edge.data.source, target: edge.data.target });
  }, []);

  const handleQuerySubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim()) return;
    const userText = query;
    setChatHistory(prev => [...prev, { role: 'user', text: userText }]);
    setQuery('');
    setIsLoading(true);
    try {
      const res = await fetch('http://localhost:8001/api/nlp/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: userText })
      });
      const data = await res.json();
      setChatHistory(prev => [...prev, { role: 'ai', text: data.response }]);
    } catch (error) { setChatHistory(prev => [...prev, { role: 'ai', text: "API Error." }]); } finally { setIsLoading(false); }
  };

  const motorList = nodes.filter(n => n.data.isMotor).sort((a, b) => a.id.localeCompare(b.id));

  return (
    <div className="flex flex-col h-screen bg-slate-50 font-sans">
      
      {/* HEADER */}
      <header className="bg-[#0B253A] text-white px-6 py-4 shadow-lg flex justify-between items-center z-20 overflow-hidden">
        <div className="flex items-center gap-3 shrink-0 mr-6">
          <Share2 className="text-[#FACC15]" />
          <div>
            <h1 className="text-lg font-bold tracking-wide">NAVAL GRAPH EXPLORER</h1>
            <p className="text-[10px] text-slate-400 uppercase tracking-widest">Multi-Cycle Cluster View</p>
          </div>
        </div>
        
        <div className="flex gap-2 overflow-x-auto py-1 scrollbar-hide w-full max-w-[65vw]">
            <button 
              onClick={() => handleFilter("ALL")} 
              className={`text-xs px-4 py-1.5 rounded-full border transition-colors shrink-0 ${activeFilter === "ALL" ? 'bg-white text-[#0B253A] font-bold' : 'border-slate-600 text-slate-400 hover:text-white'}`}
            >
              ALL MOTORS
            </button>
            {motorList.map(m => (
                 <button 
                   key={m.id} 
                   onClick={() => handleFilter(m.id)} 
                   className={`text-xs px-4 py-1.5 rounded-full border transition-colors shrink-0 ${activeFilter === m.id ? 'bg-[#FACC15] text-black font-bold border-[#FACC15]' : 'border-slate-600 text-slate-400 hover:text-white hover:border-slate-400'}`}
                 >
                    {m.data.label.props.children}
                </button>
            ))}
        </div>
      </header>

      {/* GRAPH */}
      <div className="flex-grow relative w-full h-full bg-[#FAFAFA]">
        {graphStatus ? (
             <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
                <Loader2 className="animate-spin mb-3 text-[#0B253A]" size={40} />
                <p>{graphStatus}</p>
             </div>
        ) : (
          <ReactFlow 
            nodes={nodes} 
            edges={edges} 
            onNodesChange={onNodesChange} 
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            onEdgeClick={onEdgeClick}
            edgeTypes={edgeTypes}
            minZoom={0.1}
          >
            <Controls className="bg-white shadow-md border border-slate-200" />
            <MiniMap className="border border-slate-300 shadow-sm rounded-lg" nodeStrokeWidth={6} zoomable pannable />
            <Background color="#E5E7EB" gap={25} size={1} />
          </ReactFlow>
        )}

        {/* SIDE DRAWER */}
        {selectedItem && (
          <div className="absolute top-4 right-4 w-80 max-h-[calc(100vh-200px)] bg-white shadow-2xl rounded-lg border-l-8 border-[#FACC15] overflow-hidden z-50 animate-in slide-in-from-right flex flex-col">
            <div className={`p-4 border-b border-slate-200 flex justify-between items-center ${selectedItem.type === 'EDGE' ? 'bg-orange-50' : 'bg-[#F8FAFC]'}`}>
              <div className="flex items-center gap-2 text-[#0B253A] font-bold uppercase text-sm">
                {selectedItem.type === 'EDGE' ? <Waypoints size={14}/> : <Database size={14} />}
                <span>{selectedItem.category} Properties</span>
              </div>
              <button onClick={() => setSelectedItem(null)} className="text-slate-400 hover:text-red-500"><X size={16} /></button>
            </div>
            <div className="p-0 overflow-y-auto flex-grow">
              <table className="w-full text-xs">
                <tbody>
                  {selectedItem.type === 'EDGE' && (
                     <>
                        <tr className="bg-orange-50/50 border-b border-orange-100"><td className="p-2 font-bold text-slate-500">Source</td><td className="p-2 font-mono text-blue-600 break-all">{selectedItem.source}</td></tr>
                        <tr className="bg-orange-50/50 border-b border-orange-100"><td className="p-2 font-bold text-slate-500">Target</td><td className="p-2 font-mono text-blue-600 break-all">{selectedItem.target}</td></tr>
                     </>
                  )}
                  {Object.entries(selectedItem.props || {}).length === 0 ? (
                      <tr><td colSpan={2} className="p-4 text-center text-slate-400 italic">No properties</td></tr>
                  ) : (
                      Object.entries(selectedItem.props || {}).map(([key, val], idx) => (
                        <tr key={key} className="border-b border-slate-50 last:border-0 hover:bg-slate-50">
                          <td className="py-2 px-4 font-semibold text-slate-500 capitalize">{key.replace(/_/g, ' ')}</td>
                          <td className="py-2 px-4 text-slate-800 break-words">
                            {typeof val === 'object' ? JSON.stringify(val) : String(val)}
                          </td>
                        </tr>
                      ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* CHAT INTERFACE */}
      <div className="h-44 bg-white border-t border-slate-200 shrink-0 flex flex-col shadow-inner z-20">
        <div className="flex-grow overflow-y-auto p-4 space-y-2">
          {chatHistory.map((msg, idx) => (
            <div key={idx} className={`text-sm p-2 rounded-lg max-w-[90%] ${msg.role === 'user' ? 'bg-[#0B253A] text-white self-end ml-auto' : 'bg-slate-100 text-slate-800 border'}`}>
               <span className="font-bold mr-2 text-xs opacity-50 uppercase">{msg.role}:</span>{msg.text}
            </div>
          ))}
        </div>
        <form onSubmit={handleQuerySubmit} className="p-2 bg-slate-50 border-t flex gap-2">
          <input type="text" value={query} onChange={(e) => setQuery(e.target.value)} className="flex-grow border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:border-[#0B253A]" placeholder="Ask about the lifecycle..." />
          <button type="submit" disabled={isLoading} className="bg-[#0B253A] text-white px-4 py-2 rounded-md text-sm font-bold">{isLoading ? "..." : "RUN"}</button>
        </form>
      </div>
    </div>
  );
}

export default function DigitalTwinPage() {
    return <ReactFlowProvider><DigitalTwinContent /></ReactFlowProvider>;
}