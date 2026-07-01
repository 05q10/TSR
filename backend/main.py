from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import json
import os
from mistralai import Mistral
import psycopg2
import urllib.parse
from sqlalchemy import create_engine, text
from pymongo import MongoClient
from cassandra.cluster import Cluster
from psycopg2.extras import RealDictCursor

app = FastAPI(title="Naval Digital Twin API")

# 1. CORS Configuration (Allows Next.js on port 3000 to talk to FastAPI on port 8000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. Mistral AI Setup
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "HKEjufvqu5vLPSI9Jbz9udAPjYaRESLb") # Replace with your key

class NLPQuery(BaseModel):
    prompt: str

# --- API ROUTES ---

@app.get("/api/graph")
def get_knowledge_graph():
    """Serves the generated master_graph.json to the React Flow frontend."""
    if not os.path.exists("master_graph.json"):
        raise HTTPException(status_code=404, detail="Graph JSON not found. Run graph_builder.py first.")
    
    with open("master_graph.json", "r") as f:
        data = json.load(f)
    return data

@app.get("/api/data/{source}")
def get_stakeholder_data(source: str):
    """Serves the raw LIVE database data for the 4 stakeholder CRUD tables using native dicts."""
    try:
        data = []
        
        if source == "oem":
            pg_conn = psycopg2.connect(host="localhost", port=5432, dbname="postgres", user="postgres", password="Ria05@10")
            pg_cursor = pg_conn.cursor(cursor_factory=RealDictCursor)
            pg_cursor.execute("SELECT * FROM _oem_motor_final;")
            data = [dict(row) for row in pg_cursor.fetchall()]
            pg_conn.close()
            
        elif source == "ship":
            encoded_password = urllib.parse.quote_plus("Ria05@10")
            mysql_engine = create_engine(f"mysql+pymysql://root:{encoded_password}@localhost:3306/ship_ingest")
            with mysql_engine.connect() as conn:
                result = conn.execute(text("SELECT * FROM ship_assignment;"))
                data = [dict(row._mapping) for row in result]
                
        elif source == "store":
            mongo_client = MongoClient("mongodb://localhost:27017")
            collection = mongo_client["mo_db"]["material_organisation_final"]
            data = list(collection.find({}, {"_id": 0})) # Drop Mongo ObjectId
            
        elif source == "workshop":
            cluster = Cluster(contact_points=["127.0.0.1"], port=9042)
            cass_session = cluster.connect("naval_system")
            rows = cass_session.execute("SELECT * FROM workshop_events;")
            data = [row._asdict() for row in rows]
            cluster.shutdown()
        else:
            raise HTTPException(status_code=404, detail="Invalid source")

        # Force all dates, UUIDs, and complex objects into JSON-safe strings
        for row in data:
            for key, val in row.items():
                if val is not None and not isinstance(val, (int, float, str, bool)):
                    row[key] = str(val)

        return data
        
    except Exception as e:
        print(f"❌ API Error fetching {source} data: {str(e)}") # This prints to your terminal!
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/api/naval-graph")
def get_naval_knowledge_graph():

    file_path = "query_final.json" 
    
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=404, 
            detail=f"Naval Graph JSON not found at {file_path}. Run your ingestion script first."
        )
    
    try:
        with open(file_path, "r") as f:
            data = json.load(f)
        for rel, rel_data in data["relationship_types"].items():
         for e in rel_data.get("edges", []):
           if "ASSEMBLY_FS013" in e.get("from", "") or "ASSEMBLY_FS013" in e.get("to", ""):
            print("FS013 EDGE:", rel, e)

        return data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading Graph file: {str(e)}")

@app.post("/api/nlp/query")
def nlp_graph_query(query: NLPQuery):
    """Passes the user's question and the Knowledge Graph JSON to Mistral."""
    try:
        # Load the graph as context
        with open("master_graph.json", "r") as f:
            graph_context = f.read()
            
        # We truncate the context slightly if it's too massive, but Mistral handles large contexts well.
        context_snippet = graph_context[:15000] 

        prompt = f"""You are a Naval Logistics AI. Use the provided JSON Knowledge Graph of naval motors to answer the officer's query.
        
        GRAPH DATA:
        {context_snippet}
        
        OFFICER QUERY: {query.prompt}
        """

        client = Mistral(api_key=MISTRAL_API_KEY)
        chat_response = client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        
        return {"response": chat_response.choices[0].message.content}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Mistral API Error: {str(e)}")