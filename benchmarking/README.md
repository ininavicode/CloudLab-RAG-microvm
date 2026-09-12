# MicroVM RAG Benchmarking Harness

Automated benchmarking suite for the Lambda MicroVM RAG pipeline. Measures end-to-end latencies, server-side timing breakdowns (Bedrock embedding, LanceDB I/O), and suspend/resume overhead.

## Prerequisites

1. **Instrumented MicroVM deployed** — The `main/app.py` must include timing instrumentation (returns `timings` object in responses). Re-run `main/deploy.sh` after updating `app.py`.
2. **AWS credentials** configured with permissions for `lambda-microvms:*`.
3. **Python dependencies**: `pip install -r requirements.txt`
4. **For accurate results**: Run from an EC2 instance or Cloud9 in `us-east-1` to eliminate transatlantic jitter.

## Usage

The script reads `MICROVM_URL` and `MICROVM_ID` from `../main/.env` by default. Override with `--endpoint` and `--microvm-id`.

### Ingestion Benchmarks

```bash
# Suspended mode — suspends before each PDF, measures resume + ingest
python run_benchmarks.py --mode ingest-suspended --dir /path/to/pdfs

# Warm mode — MicroVM stays active, sequential ingestion
python run_benchmarks.py --mode ingest-warm --dir /path/to/pdfs
```

### Query Benchmarks

```bash
# Suspended mode — suspends before each question, measures resume + query
python run_benchmarks.py --mode query-suspended --questions questions.json

# Warm mode — MicroVM stays active, sequential queries
python run_benchmarks.py --mode query-warm --questions questions.json
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--mode` | *(required)* | One of: `ingest-suspended`, `ingest-warm`, `query-suspended`, `query-warm` |
| `--dir` | — | Directory containing PDF files (required for ingest modes) |
| `--questions` | — | JSON file with array of question strings (required for query modes) |
| `--microvm-id` | from `../main/.env` | MicroVM identifier |
| `--endpoint` | from `../main/.env` | MicroVM endpoint URL |
| `--region` | `us-east-1` | AWS region |
| `--output` | `benchmark_results.csv` | Output CSV file path |

### Output

Results are written to both CSV and JSON (same basename):

```
benchmark_results.csv
benchmark_results.json
```

**Columns:**

| Column | Description |
|--------|-------------|
| `benchmark_type` | The mode that was run |
| `item_name_or_query` | PDF filename or question text |
| `probe_e2e_ms` | Total round-trip time from probe |
| `server_total_ms` | Server-side total processing time |
| `resume_overhead_ms` | `probe_e2e_ms - server_total_ms` (network + resume) |
| `bedrock_ms` | Time spent in Bedrock Titan embeddings |
| `lancedb_ms` | Time spent in LanceDB operations |
