import os
import json
import boto3
import lancedb
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from pypdf import PdfReader
import io

# Configuration
LANCEDB_PATH = "/tmp/lancedb_data"
TABLE_NAME = "rag_chunks"
BEDROCK_MODEL_ID = "amazon.titan-embed-text-v1"

# Globals initialized at startup, not at import time
bedrock_client = None
db = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources after the process is fully running inside the MicroVM."""
    global bedrock_client, db
    os.makedirs(LANCEDB_PATH, exist_ok=True)
    bedrock_client = boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", "us-east-1")
    )
    db = lancedb.connect(LANCEDB_PATH)
    yield

app = FastAPI(lifespan=lifespan)

class QueryRequest(BaseModel):
    question: str

def get_embedding(text: str) -> list[float]:
    """Call Bedrock Titan to generate a text embedding. Raises on failure."""
    body = json.dumps({"inputText": text})
    response = bedrock_client.invoke_model(
        body=body,
        modelId=BEDROCK_MODEL_ID,
        accept="application/json",
        contentType="application/json"
    )
    response_body = json.loads(response.get("body").read())
    embedding = response_body.get("embedding")
    if not embedding:
        raise ValueError("Bedrock returned an empty embedding")
    return embedding

@app.post("/aws/lambda-microvms/runtime/v1/ready")
async def ready_hook():
    """Hook called during image build to trigger the Firecracker snapshot."""
    return {"status": "ready"}

@app.post("/aws/lambda-microvms/runtime/v1/run")
async def run_hook():
    """Hook called after snapshot restore, before routing external traffic."""
    return {"status": "running"}

@app.post("/ingest")
async def ingest_document(file: UploadFile = File(...)):
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        content = await file.read()
        pdf = PdfReader(io.BytesIO(content))

        text_chunks = []
        # Chunk by page — each page becomes one document
        for i, page in enumerate(pdf.pages):
            text = page.extract_text()
            if text and text.strip():
                text_chunks.append({
                    "text": text.strip(),
                    "page": i + 1,
                    "filename": file.filename
                })

        if not text_chunks:
            return {"message": "No text extracted from PDF"}

        # Generate embeddings and prepare records
        data_to_insert = []
        for chunk in text_chunks:
            embedding = get_embedding(chunk["text"])
            data_to_insert.append({
                "vector": embedding,
                "text": chunk["text"],
                "page": chunk["page"],
                "filename": chunk["filename"]
            })

        # Insert into LanceDB (create table on first run, append thereafter)
        if TABLE_NAME not in db.table_names():
            db.create_table(TABLE_NAME, data=data_to_insert)
        else:
            table = db.open_table(TABLE_NAME)
            table.add(data_to_insert)

        return {"message": f"Successfully ingested {len(text_chunks)} chunks from {file.filename}"}
    except Exception as e:
        print(f"[ingest] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/query")
async def query_documents(request: QueryRequest):
    if TABLE_NAME not in db.table_names():
        raise HTTPException(status_code=404, detail="No documents ingested yet")

    try:
        question_embedding = get_embedding(request.question)

        table = db.open_table(TABLE_NAME)
        results = table.search(question_embedding).limit(5).to_list()

        # Strip the vector field to keep the response payload small
        for res in results:
            res.pop("vector", None)

        return {"results": results}
    except Exception as e:
        print(f"[query] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
