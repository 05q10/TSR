"use client";

import React, { useState, useEffect } from 'react';
import { Database, Ship, Store, Wrench, Loader2 } from 'lucide-react';
import Link from 'next/link';

export default function DataManagementPage() {
  const [activeTab, setActiveTab] = useState('oem');
  const [data, setData] = useState<any[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const tabs = [
    { id: 'oem', label: 'OEM (PostgreSQL)', icon: Database },
    { id: 'ship', label: 'Ship (MySQL)', icon: Ship },
    { id: 'store', label: 'Store (MongoDB)', icon: Store },
    { id: 'workshop', label: 'Workshop (Cassandra)', icon: Wrench },
  ];

  // Fetch data whenever the active tab changes
  useEffect(() => {
    setIsLoading(true);
    fetch(`http://localhost:8001/api/data/${activeTab}`)
      .then(res => res.json())
      .then(fetchedData => {
        setData(fetchedData);
        setIsLoading(false);
      })
      .catch(err => {
        console.error("Error fetching data:", err);
        setIsLoading(false);
      });
  }, [activeTab]);

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900">
      
      {/* SIDEBAR */}
      <div className="w-64 bg-slate-900 text-slate-300 flex flex-col shadow-xl z-10">
        <div className="p-6">
          <h1 className="text-xl font-bold text-white tracking-wider">FLEET COMMAND</h1>
          <p className="text-xs text-slate-500 mt-1 uppercase">Data Management</p>
        </div>
        
        <nav className="flex-1 px-4 space-y-2 mt-4">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`w-full flex items-center gap-3 px-4 py-3 rounded-md transition-colors text-sm font-medium ${
                  isActive ? 'bg-blue-600 text-white shadow-md' : 'hover:bg-slate-800 hover:text-white'
                }`}
              >
                <Icon size={18} />
                {tab.label}
              </button>
            );
          })}
        </nav>

        <div className="p-4 border-t border-slate-800">
          <Link href="/twin" className="w-full flex items-center justify-center gap-2 bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-md transition-colors text-sm font-medium">
            View Digital Twin
          </Link>
        </div>
      </div>

      {/* MAIN CONTENT AREA */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="bg-white border-b border-slate-200 px-8 py-5 shadow-sm">
          <h2 className="text-2xl font-semibold text-slate-800 capitalize">
            {activeTab} Database View
          </h2>
        </header>

        <main className="flex-1 overflow-auto p-8">
          {isLoading ? (
            <div className="flex flex-col items-center justify-center h-full text-slate-400">
              <Loader2 className="animate-spin mb-4" size={32} />
              <p>Syncing secure data stream...</p>
            </div>
          ) : data.length === 0 ? (
            <div className="flex items-center justify-center h-full text-slate-400">
              <p>No records found in this database.</p>
            </div>
          ) : (
            <div className="bg-white border border-slate-200 shadow-sm rounded-lg overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-left">
                  <thead className="text-xs text-slate-500 uppercase bg-slate-50 border-b border-slate-200">
                    <tr>
                      {Object.keys(data[0]).map((key) => (
                        <th key={key} className="px-6 py-4 font-semibold tracking-wider whitespace-nowrap">
                          {key.replace(/_/g, ' ')}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.map((row, idx) => (
                      <tr key={idx} className="border-b border-slate-100 hover:bg-slate-50 transition-colors">
                        {Object.values(row).map((val: any, colIdx) => (
                          <td key={colIdx} className="px-6 py-4 whitespace-nowrap text-slate-700">
                            {val !== null && val !== undefined ? String(val) : <span className="text-slate-300">-</span>}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}