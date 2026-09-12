import os
import json
import time
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


def _ns_to_ms(ns: int) -> float:
    """Convert nanoseconds to milliseconds with microsecond precision."""
    return round(ns / 1_000_000, 3)


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
    t_start = time.perf_counter_ns()

    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        t_read = time.perf_counter_ns()
        content = await file.read()
        file_read_ns = time.perf_counter_ns() - t_read
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
            elapsed = _ns_to_ms(time.perf_counter_ns() - t_start)
            return {
                "message": "No text extracted from PDF",
                "timings": {"server_total_ms": elapsed, "bedrock_ms": 0, "lancedb_ms": 0, "file_read_ms": _ns_to_ms(file_read_ns)}
            }

        # Generate embeddings and prepare records
        bedrock_ns = 0
        data_to_insert = []
        for chunk in text_chunks:
            t_bed = time.perf_counter_ns()
            embedding = get_embedding(chunk["text"])
            bedrock_ns += time.perf_counter_ns() - t_bed

            data_to_insert.append({
                "vector": embedding,
                "text": chunk["text"],
                "page": chunk["page"],
                "filename": chunk["filename"]
            })

        # Insert into LanceDB (create table on first run, append thereafter)
        t_lance = time.perf_counter_ns()
        if TABLE_NAME not in db.table_names():
            db.create_table(TABLE_NAME, data=data_to_insert)
        else:
            table = db.open_table(TABLE_NAME)
            table.add(data_to_insert)
        lancedb_ns = time.perf_counter_ns() - t_lance

        server_total_ns = time.perf_counter_ns() - t_start
        return {
            "message": f"Successfully ingested {len(text_chunks)} chunks from {file.filename}",
            "timings": {
                "server_total_ms": _ns_to_ms(server_total_ns),
                "bedrock_ms": _ns_to_ms(bedrock_ns),
                "lancedb_ms": _ns_to_ms(lancedb_ns),
                "file_read_ms": _ns_to_ms(file_read_ns),
            }
        }
    except Exception as e:
        print(f"[ingest] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/query")
async def query_documents(request: QueryRequest):
    t_start = time.perf_counter_ns()

    if TABLE_NAME not in db.table_names():
        raise HTTPException(status_code=404, detail="No documents ingested yet")

    try:
        t_bed = time.perf_counter_ns()
        question_embedding = get_embedding(request.question)
        bedrock_ns = time.perf_counter_ns() - t_bed

        t_lance = time.perf_counter_ns()
        table = db.open_table(TABLE_NAME)
        results = table.search(question_embedding).limit(5).to_list()
        lancedb_ns = time.perf_counter_ns() - t_lance

        # Strip the vector field to keep the response payload small
        for res in results:
            res.pop("vector", None)

        server_total_ns = time.perf_counter_ns() - t_start
        return {
            "results": results,
            "timings": {
                "server_total_ms": _ns_to_ms(server_total_ns),
                "bedrock_ms": _ns_to_ms(bedrock_ns),
                "lancedb_ms": _ns_to_ms(lancedb_ns),
            }
        }
    except Exception as e:
        print(f"[query] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
