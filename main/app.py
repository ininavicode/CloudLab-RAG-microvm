import traceback
import os
import json
import time
import subprocess
import threading
import psutil

# Capture the exact moment the Python runtime starts executing our code
_APP_INIT_START = time.perf_counter()

import tempfile
import boto3
import lancedb
import pyarrow as pa
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel

from langchain_aws import BedrockEmbeddings
from langchain_text_splitters import CharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader

# Configuration
LANCEDB_PATH = "/tmp/lancedb_data"
TABLE_NAME = "rag_chunks"
BEDROCK_MODEL_ID = "amazon.titan-embed-text-v1"

S3_BUCKET = None
try:
    with open("config.json") as f:
        config = json.load(f)
        S3_BUCKET = config.get("S3_BUCKET")
except Exception:
    pass

# Globals initialized at startup, not at import time
embeddings = None
db = None
s3_client = None
bedrock_connection_creation_ms = 0.0
s3_client_connection_creation_ms = 0.0
db_connect_ms = 0.0
embeddings_read_ms = 0.0

# ── Helpers (Iguales que en la Lambda) ─────────────────────────────────────────

def _ms() -> int:
    """Current epoch time in milliseconds (equivalent to Date.now())."""
    return int(time.time() * 1000)

def _perf() -> float:
    """High-resolution monotonic time in milliseconds (equivalent to performance.now())."""
    return time.perf_counter() * 1000


class ResourceSampler:
    """Samples CPU, RAM and disk I/O in a background thread at a fixed interval.

    Usage::

        sampler = ResourceSampler(interval_ms=25)
        sampler.start()
        # ... do work ...
        samples = sampler.stop()  # list of dicts, one per sample tick

    Each sample dict contains:
        cpu_percent   – CPU utilisation since previous sample (all cores, 0-100*n_cpus)
        ram_rss_mb    – resident set size of this process in MiB
        disk_read_kb  – kilobytes read from disk since previous sample
        disk_write_kb – kilobytes written to disk since previous sample
        sample_interval_ms – actual elapsed wall-clock time since previous sample (ms)
    """

    def __init__(self, interval_ms: float = 25):
        self._interval_s = interval_ms / 1000.0
        self._stop_event = threading.Event()
        self._samples: list = []
        self._thread: threading.Thread | None = None
        self._proc = psutil.Process()
        # Prime the non-blocking cpu_percent so first real reading is meaningful
        self._proc.cpu_percent(interval=None)
        # Baseline disk counters (may be None on some platforms)
        self._last_disk = psutil.disk_io_counters()
        self._last_ts = time.perf_counter()

    def _run(self):
        while not self._stop_event.wait(timeout=self._interval_s):
            now = time.perf_counter()
            elapsed_ms = round((now - self._last_ts) * 1000, 3)
            self._last_ts = now

            cpu = self._proc.cpu_percent(interval=None)
            rss_mb = round(self._proc.memory_info().rss / (1024 * 1024), 3)

            disk_read_kb = 0.0
            disk_write_kb = 0.0
            try:
                cur_disk = psutil.disk_io_counters()
                if cur_disk is not None and self._last_disk is not None:
                    disk_read_kb = round((cur_disk.read_bytes - self._last_disk.read_bytes) / 1024, 3)
                    disk_write_kb = round((cur_disk.write_bytes - self._last_disk.write_bytes) / 1024, 3)
                self._last_disk = cur_disk
            except Exception:
                pass

            self._samples.append({
                "sample_interval_ms": elapsed_ms,
                "cpu_percent": cpu,
                "ram_rss_mb": rss_mb,
                "disk_read_kb": disk_read_kb,
                "disk_write_kb": disk_write_kb,
            })

    def start(self):
        """Start the background sampling thread."""
        self._stop_event.clear()
        self._samples = []
        self._last_ts = time.perf_counter()
        # Re-prime CPU counter right before we start so first delta is clean
        self._proc.cpu_percent(interval=None)
        self._last_disk = psutil.disk_io_counters()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> list:
        """Stop sampling and return the collected samples."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        return list(self._samples)

# ── Ciclo de Vida y Hooks ──────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources after the process is fully running inside the MicroVM."""
    global embeddings, db, s3_client, s3_client_connection_creation_ms, db_connect_ms, embeddings_read_ms
    os.makedirs(LANCEDB_PATH, exist_ok=True)
    
    s3_client_start = time.perf_counter() * 1000
    s3_client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    s3_client_connection_creation_ms = round((time.perf_counter() * 1000) - s3_client_start, 3)
    
    if S3_BUCKET:
        try:
            t_emb_read = _perf()
            s3_client.download_file(S3_BUCKET, "lancedb_snapshot.tar.gz", "/tmp/lancedb_snapshot.tar.gz")
            subprocess.run(["tar", "-xzf", "/tmp/lancedb_snapshot.tar.gz", "-C", "/tmp"], check=True)
            embeddings_read_ms = round(_perf() - t_emb_read, 3)
        except Exception:
            pass

    embeddings = BedrockEmbeddings(
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        model_id=BEDROCK_MODEL_ID
    )
    
    t_db = _perf()
    db = lancedb.connect(LANCEDB_PATH)
    db_connect_ms = round(_perf() - t_db, 3)
    global _APP_INIT_END
    _APP_INIT_END = time.perf_counter()
    yield

app = FastAPI(lifespan=lifespan)

class QueryRequest(BaseModel):
    question: str

class IngestRequest(BaseModel):
    s3_key: str

@app.post("/aws/lambda-microvms/runtime/v1/ready")
async def ready_hook():
    """Hook called during image build to trigger the Firecracker snapshot."""
    return {"status": "ready"}

@app.post("/aws/lambda-microvms/runtime/v1/run")
async def run_hook():
    """Hook called after snapshot restore, before routing external traffic.
    Re-initializes connections to prevent stale OS sockets post-suspend.
    """
    global embeddings, db, bedrock_connection_creation_ms, s3_client, s3_client_connection_creation_ms, db_connect_ms, embeddings_read_ms
    
    s3_client_start = time.perf_counter() * 1000
    s3_client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    s3_client_connection_creation_ms = round((time.perf_counter() * 1000) - s3_client_start, 3)
    
    if S3_BUCKET:
        try:
            t_emb_read = _perf()
            s3_client.download_file(S3_BUCKET, "lancedb_snapshot.tar.gz", "/tmp/lancedb_snapshot.tar.gz")
            subprocess.run(["tar", "-xzf", "/tmp/lancedb_snapshot.tar.gz", "-C", "/tmp"], check=True)
            embeddings_read_ms = round(_perf() - t_emb_read, 3)
        except Exception:
            pass
            
    t_start = _perf()
    embeddings = BedrockEmbeddings(
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        model_id=BEDROCK_MODEL_ID
    )
    bedrock_connection_creation_ms = round(_perf() - t_start, 3)

    t_db = _perf()
    db = lancedb.connect(LANCEDB_PATH)
    db_connect_ms = round(_perf() - t_db, 3)
    return {"status": "running"}

@app.post("/aws/lambda-microvms/runtime/v1/resume")
async def resume_hook():
    """Hook called after MicroVM resumes from suspended state.
    Re-establishes network connections, refresh credentials, validate state. 
    The MicroVM remains in SUSPENDED state while this hook executes.
    """
    global embeddings, db, bedrock_connection_creation_ms, s3_client, s3_client_connection_creation_ms, db_connect_ms
    
    s3_client_start = time.perf_counter() * 1000
    session = boto3.Session()
    s3_client = session.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    s3_client_connection_creation_ms = round((time.perf_counter() * 1000) - s3_client_start, 3)

    t_start = _perf()
    embeddings = BedrockEmbeddings(
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        model_id=BEDROCK_MODEL_ID
    )
    bedrock_connection_creation_ms = round(_perf() - t_start, 3)

    t_db = _perf()
    db = lancedb.connect(LANCEDB_PATH)
    db_connect_ms = round(_perf() - t_db, 3)
    return {"status": "running"}


def _sync_lancedb_to_s3():
    if S3_BUCKET and s3_client:
        try:
            subprocess.run(["tar", "-czf", "/tmp/lancedb_snapshot.tar.gz", "-C", "/tmp", "lancedb_data"], check=True)
            s3_client.upload_file("/tmp/lancedb_snapshot.tar.gz", S3_BUCKET, "lancedb_snapshot.tar.gz")
        except Exception as e:
            print(f"Failed to sync LanceDB to S3: {e}")

@app.post("/aws/lambda-microvms/runtime/v1/suspend")
async def suspend_hook():
    """Hook called before MicroVM suspends."""
    _sync_lancedb_to_s3()
    return {"status": "suspended"}

@app.post("/aws/lambda-microvms/runtime/v1/terminate")
async def terminate_hook():
    """Hook called before MicroVM terminates."""
    _sync_lancedb_to_s3()
    return {"status": "terminated"}

@app.post("/aws/lambda-microvms/runtime/v1/suspend")
async def suspend_hook():
    """Hook called before MicroVM suspends.
    Flush pending writes, close connections, release resources.
    """
    if S3_BUCKET and s3_client:
        try:
            subprocess.run(["tar", "-czf", "/tmp/lancedb_snapshot.tar.gz", "-C", "/tmp", "lancedb_data"], check=True)
            s3_client.upload_file("/tmp/lancedb_snapshot.tar.gz", S3_BUCKET, "lancedb_snapshot.tar.gz")
        except Exception as e:
            print(f"Failed to sync LanceDB to S3 on suspend: {e}")
    return {"status": "suspended"}

@app.post("/aws/lambda-microvms/runtime/v1/terminate")
async def terminate_hook():
    """Hook called before MicroVM terminates.
    Flush data, notify external systems, clean up.
    """
    if S3_BUCKET and s3_client:
        try:
            subprocess.run(["tar", "-czf", "/tmp/lancedb_snapshot.tar.gz", "-C", "/tmp", "lancedb_data"], check=True)
            s3_client.upload_file("/tmp/lancedb_snapshot.tar.gz", S3_BUCKET, "lancedb_snapshot.tar.gz")
        except Exception as e:
            print(f"Failed to sync LanceDB to S3 on terminate: {e}")
    return {"status": "terminated"}

# ── Endpoints ──────────────────────────────────────────────────────────────────



@app.get("/init-time")
async def get_init_time():
    """Returns the one-time cold start initialization time of the application."""
    if '_APP_INIT_END' not in globals():
        return {"init_time_ms": 0}
    return {"init_time_ms": round((_APP_INIT_END - _APP_INIT_START) * 1000)}

@app.post("/ingest")
async def ingest_document(request: IngestRequest):
    global bedrock_connection_creation_ms, s3_client_connection_creation_ms, db_connect_ms, embeddings_read_ms
    server_entry_epoch_ms = _ms()
    handler_start = _perf()

    if not request.s3_key.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    sampler = ResourceSampler(interval_ms=25)
    sampler.start()

    try:
        # 1. Download file from S3
        t_file_read = _perf()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = tmp.name
            
        s3_client.download_file(S3_BUCKET, request.s3_key, tmp_path)
        file_read_ms = round(_perf() - t_file_read)

        print("DOWNLOADED FILE")

        # 2. Carga y Chunking (Langchain - Igual que Lambda)
        t_load_split = _perf()
        splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
        loader = PyPDFLoader(tmp_path)
        docs = loader.load_and_split(splitter)
        texts = [doc.page_content for doc in docs]
        pdf_read_ms = round(_perf() - t_load_split)   # harness key: pdf_read_ms

        # Limpieza del archivo temporal
        os.remove(tmp_path)

        if not texts:
            resource_samples = sampler.stop()
            server_total_ms = round(_perf() - handler_start)
            return {
                "message": "No text extracted from PDF",
                "server_entry_epoch_ms": server_entry_epoch_ms,
                "timings": {
                    "server_total_ms": server_total_ms,
                    "file_read_ms": file_read_ms,
                    "pdf_read_ms": pdf_read_ms,
                    "bedrock_ms": 0,
                    "db_rows_creation_ms": 0,
                    "lance_table_open_ms": 0,
                    "lance_insert_rows_ms": 0,
                    "s3_client_connection_creation_ms": s3_client_connection_creation_ms,
                    "bedrock_connection_creation_ms": bedrock_connection_creation_ms,
                    "db_connect_ms": db_connect_ms,
                    "embeddings_read_ms": embeddings_read_ms,
                },
                "resource_samples": resource_samples,
            }

        # 3. Bedrock Embeddings (Langchain Secuencial - Igual que Lambda)
        t_embed = _perf()
        vectors = embeddings.embed_documents(texts)
        bedrock_ms = round(_perf() - t_embed)

        # 4. Preparar filas de LanceDB
        t_rows = _perf()
        rows = [
            {"vector": vectors[i], "text": texts[i]}
            for i in range(len(texts))
        ]
        db_rows_ms = round(_perf() - t_rows)

        # 5. Guardado en LanceDB — timer split into open/create + insert
        t_lance_open = _perf()
        create_table = False
        try:
            table = db.open_table(TABLE_NAME)
        except Exception:
            create_table = True
        lance_table_open_ms = round(_perf() - t_lance_open)

        if create_table:
            schema = pa.schema([
                pa.field("vector", pa.list_(pa.float32(), 1536)),
                pa.field("text", pa.string()),
            ])
            table = db.create_table(TABLE_NAME, schema=schema)

        t_lance_insert = _perf()
        table.add(rows)
        lance_insert_rows_ms = round(_perf() - t_lance_insert)

        resource_samples = sampler.stop()
        server_total_ms = round(_perf() - handler_start)

        temp_bedrock_connection_creation_ms = bedrock_connection_creation_ms
        temp_s3_client_connection_creation_ms = s3_client_connection_creation_ms
        temp_db_connect_ms = db_connect_ms
        temp_embeddings_read_ms = embeddings_read_ms
        bedrock_connection_creation_ms = 0
        s3_client_connection_creation_ms = 0
        db_connect_ms = 0
        embeddings_read_ms = 0
        
        return {
            "message": f"Successfully ingested {len(texts)} chunks from {request.s3_key}",
            "server_entry_epoch_ms": server_entry_epoch_ms,
            "timings": {
                "server_total_ms": server_total_ms,
                "file_read_ms": file_read_ms,
                "pdf_read_ms": pdf_read_ms,
                "bedrock_ms": bedrock_ms,
                "db_rows_creation_ms": db_rows_ms,
                "lance_table_open_ms": lance_table_open_ms,
                "lance_insert_rows_ms": lance_insert_rows_ms,
                "s3_client_connection_creation_ms": temp_s3_client_connection_creation_ms,
                "bedrock_connection_creation_ms": temp_bedrock_connection_creation_ms,
                "db_connect_ms": temp_db_connect_ms,
                "embeddings_read_ms": temp_embeddings_read_ms,
            },
            "resource_samples": resource_samples,
        }
    except Exception as e:
        sampler.stop()  # ensure thread is cleaned up on error
        tb_str = traceback.format_exc()
        print(f"[ingest] Error:\n{tb_str}")
        raise HTTPException(status_code=500, detail=f"{e}\n\nTraceback:\n{tb_str}")


@app.post("/query")
async def query_documents(request: QueryRequest):
    global bedrock_connection_creation_ms, s3_client_connection_creation_ms, db_connect_ms, embeddings_read_ms
    server_entry_epoch_ms = _ms()
    handler_start = _perf()

    if TABLE_NAME not in db.table_names():
        raise HTTPException(status_code=404, detail="No documents ingested yet")

    sampler = ResourceSampler(interval_ms=25)
    sampler.start()

    try:
        # Abrir tabla LanceDB
        t_lance_open = _perf()
        table = db.open_table(TABLE_NAME)
        lancedb_open_ms = round(_perf() - t_lance_open)

        # 1. Inferencia de Bedrock para la pregunta (Igual que Lambda)
        t_bedrock = _perf()
        query_vector = embeddings.embed_query(request.question)
        bedrock_ms = round(_perf() - t_bedrock)

        # 2. Búsqueda Vectorial (Límite 4 y selector - Igual que Lambda)
        t_lancedb_search = _perf()
        results = (
            table.search(query_vector)
                 .limit(4)
                 .select(["text"])
                 .to_list()
        )
        lancedb_search_ms = round(_perf() - t_lancedb_search)  # harness key: lancedb_search_ms

        # 3. Formateo de Contexto
        t_context = _perf()
        docs = [r["text"] for r in results]
        context = "\n\n".join(docs)
        vector_strip_ms = round(_perf() - t_context)           # harness key: vector_strip_ms

        server_total_ms = round(_perf() - handler_start)

        resource_samples = sampler.stop()

        global bedrock_connection_creation_ms, s3_client_connection_creation_ms, db_connect_ms, embeddings_read_ms
        temp_bedrock_connection_creation_ms = bedrock_connection_creation_ms
        temp_s3_client_connection_creation_ms = s3_client_connection_creation_ms
        temp_db_connect_ms = db_connect_ms
        temp_embeddings_read_ms = embeddings_read_ms
        bedrock_connection_creation_ms = 0
        s3_client_connection_creation_ms = 0
        db_connect_ms = 0
        embeddings_read_ms = 0

        return {
            "results": results,
            "server_entry_epoch_ms": server_entry_epoch_ms,
            "context_length_chars": len(context),
            "timings": {
                "server_total_ms": server_total_ms,
                "lancedb_open_ms": lancedb_open_ms,
                "bedrock_ms": bedrock_ms,
                "lancedb_search_ms": lancedb_search_ms,
                "vector_strip_ms": vector_strip_ms,
                "s3_client_connection_creation_ms": temp_s3_client_connection_creation_ms,
                "bedrock_connection_creation_ms": temp_bedrock_connection_creation_ms,
                "db_connect_ms": temp_db_connect_ms,
                "embeddings_read_ms": temp_embeddings_read_ms,
            },
            "resource_samples": resource_samples,
        }
    except Exception as e:
        sampler.stop()  # ensure thread is cleaned up on error
        tb_str = traceback.format_exc()
        print(f"[query] Error:\n{tb_str}")
        raise HTTPException(status_code=500, detail=f"{e}\n\nTraceback:\n{tb_str}")