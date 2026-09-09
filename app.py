import os
import json
import boto3
import lancedb
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from pypdf import PdfReader
import io

app = FastAPI()

# Configuration
LANCEDB_PATH = "/tmp/lancedb_data"
TABLE_NAME = "rag_chunks"
BEDROCK_MODEL_ID = "amazon.titan-embed-text-v1"

# Initialize clients
bedrock_client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
db = lancedb.connect(LANCEDB_PATH)

class QueryRequest(BaseModel):
    question: str

def get_embedding(text: str) -> list[float]:
    try:
        body = json.dumps({"inputText": text})
        response = bedrock_client.invoke_model(
            body=body,
            modelId=BEDROCK_MODEL_ID,
            accept="application/json",
            contentType="application/json"
        )
        response_body = json.loads(response.get("body").read())
        return response_body.get("embedding")
    except Exception as e:
        print(f"Error generating embedding: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate embedding")

@app.post("/aws/lambda-microvms/runtime/v1/ready")
async def ready_hook():
    """Hook called when VM is ready to be snapshotted."""
    return {"status": "ready"}

@app.post("/aws/lambda-microvms/runtime/v1/run")
async def run_hook():
    """Hook called when VM wakes up from snapshot to accept traffic."""
    return {"status": "running"}

@app.post("/ingest")
async def ingest_document(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    try:
        # Read PDF
        content = await file.read()
        pdf = PdfReader(io.BytesIO(content))
        
        text_chunks = []
        # Basic chunking: page by page for simplicity
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and text.strip():
                text_chunks.append({"text": text.strip(), "page": i + 1, "filename": file.filename})
        
        if not text_chunks:
            return {"message": "No text extracted from PDF"}
        
        # Embed and prepare data for LanceDB
        data_to_insert = []
        for chunk in text_chunks:
            embedding = get_embedding(chunk["text"])
            data_to_insert.append({
                "vector": embedding,
                "text": chunk["text"],
                "page": chunk["page"],
                "filename": chunk["filename"]
            })
        
        # Insert into LanceDB
        if TABLE_NAME not in db.table_names():
            db.create_table(TABLE_NAME, data=data_to_insert)
        else:
            table = db.open_table(TABLE_NAME)
            table.add(data_to_insert)
            
        return {"message": f"Successfully ingested {len(text_chunks)} chunks from {file.filename}"}
    except Exception as e:
        print(f"Error during ingestion: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/query")
async def query_documents(request: QueryRequest):
    if TABLE_NAME not in db.table_names():
        raise HTTPException(status_code=404, detail="No documents ingested yet")
        
    try:
        question_embedding = get_embedding(request.question)
        
        table = db.open_table(TABLE_NAME)
        results = table.search(question_embedding).limit(5).to_list()
        
        # Remove the vector field from response for smaller payload
        for res in results:
            res.pop("vector", None)
            
        return {"results": results}
    except Exception as e:
        print(f"Error during query: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
