import os
import json
import time
import boto3
import lancedb
import io
import concurrent.futures
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter

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
    """Hook called after snapshot restore, before routing external traffic.

    IMPORTANT: boto3 and lancedb hold TCP connection pools that become stale
    after the MicroVM is suspended (the OS freezes all sockets). If we reuse
    those connections on resume, boto3 silently retries the same request (same
    x-amzn-requestid) after hitting a dead socket, causing 15-20s outliers.
    Reinitialising the clients here gives every resume a fresh connection pool
    before any user traffic is routed.
    """
    global bedrock_client, db
    bedrock_client = boto3.client(
        "bedrock-runtime",
        region_name=os.environ.get("AWS_REGION", "us-east-1")
    )
    db = lancedb.connect(LANCEDB_PATH)
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

        t_pdf_read = time.perf_counter_ns()
        pdf = PdfReader(io.BytesIO(content))
        
        # 1. Replicating `splitPages: false` by concatenating all pages into a single string
        full_text = ""
        for page in pdf.pages:
            extracted = page.extract_text()
            if extracted:
                full_text += extracted + "\n"
        pdf_read_ns = time.perf_counter_ns() - t_pdf_read

        t_chunking = time.perf_counter_ns()
        # 2. Replicating the Lambda RecursiveCharacterTextSplitter
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200, 
            length_function=len,
        )
        
        split_texts = text_splitter.split_text(full_text)
        
        text_chunks = []
        for chunk in split_texts:
            if chunk.strip():
                text_chunks.append({
                    "text": chunk.strip(),
                    "filename": file.filename
                    # 'page' metadata is removed as chunks now span across multiple pages
                })
        chunking_ns = time.perf_counter_ns() - t_chunking

        if not text_chunks:
            elapsed = _ns_to_ms(time.perf_counter_ns() - t_start)
            return {
                "message": "No text extracted from PDF",
                "timings": {
                    "server_total_ms": elapsed,
                    "bedrock_ms": 0.0,
                    "file_read_ms": _ns_to_ms(file_read_ns),
                    "pdf_read_ms": _ns_to_ms(pdf_read_ns),
                    "chunking_ms": _ns_to_ms(chunking_ns),
                    "lance_table_open_ms": 0.0,
                    "lance_insert_rows_ms": 0.0,
                }
            }

        # 3. Generate embeddings concurrently to match LangChain's Node.js behavior
        t_bed_start = time.perf_counter_ns()
        
        def fetch_embedding(text: str):
            return get_embedding(text)
            
        data_to_insert = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            # Execute all Bedrock API calls in parallel
            vectors = list(executor.map(fetch_embedding, [c["text"] for c in text_chunks]))
            
        bedrock_ns = time.perf_counter_ns() - t_bed_start

        for chunk, vector in zip(text_chunks, vectors):
            data_to_insert.append({
                "vector": vector,
                "text": chunk["text"],
                "filename": chunk["filename"]
            })

        # Insert into LanceDB (create table on first run, append thereafter)
        t_lance_table_open = 0
        t_lance_insert_rows = 0
        lance_table_open_ns = 0
        lance_insert_rows_ns = 0
        
        if TABLE_NAME not in db.table_names():
            t_lance_table_open = time.perf_counter_ns()
            db.create_table(TABLE_NAME, data=data_to_insert)
            lance_table_open_ns = time.perf_counter_ns() - t_lance_table_open
        else:
            t_lance_table_open = time.perf_counter_ns()
            table = db.open_table(TABLE_NAME)
            lance_table_open_ns = time.perf_counter_ns() - t_lance_table_open

            t_lance_insert_rows = time.perf_counter_ns()
            table.add(data_to_insert)
            lance_insert_rows_ns = time.perf_counter_ns() - t_lance_insert_rows

        server_total_ns = time.perf_counter_ns() - t_start
        return {
            "message": f"Successfully ingested {len(text_chunks)} chunks from {file.filename}",
            "timings": {
                "server_total_ms": _ns_to_ms(server_total_ns),
                "bedrock_ms": _ns_to_ms(bedrock_ns),
                "file_read_ms": _ns_to_ms(file_read_ns),
                "pdf_read_ms": _ns_to_ms(pdf_read_ns),
                "chunking_ms": _ns_to_ms(chunking_ns),
                "lance_table_open_ms": _ns_to_ms(lance_table_open_ns),
                "lance_insert_rows_ms": _ns_to_ms(lance_insert_rows_ns),
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

        t_lance_open = time.perf_counter_ns()
        table = db.open_table(TABLE_NAME)
        lancedb_open_ns = time.perf_counter_ns() - t_lance_open

        t_lance_search = time.perf_counter_ns()
        results = table.search(question_embedding).limit(5).to_list()
        lancedb_search_ns = time.perf_counter_ns() - t_lance_search

        t_vector_strip = time.perf_counter_ns()
        retrieved_context_length = 0
        for res in results:
            res.pop("vector", None)
            retrieved_context_length += len(res.get("text", ""))
        vector_strip_ns = time.perf_counter_ns() - t_vector_strip

        server_total_ns = time.perf_counter_ns() - t_start
        return {
            "results": results,
            "context_length_chars": retrieved_context_length,
            "timings": {
                "server_total_ms": _ns_to_ms(server_total_ns),
                "bedrock_ms": _ns_to_ms(bedrock_ns),
                "lancedb_open_ms": _ns_to_ms(lancedb_open_ns),
                "lancedb_search_ms": _ns_to_ms(lancedb_search_ns),
                "vector_strip_ms": _ns_to_ms(vector_strip_ns),
            }
        }
    except Exception as e:
        print(f"[query] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))