# AWS Lambda MicroVMs RAG System

A stateful, single-node Retrieval-Augmented Generation (RAG) system utilizing the new AWS Lambda MicroVMs service. This service provides VM-level isolation, persistent memory/disk state, and a long-running HTTPS endpoint.

## Architecture

- **Backend**: Python (FastAPI) web server.
- **Vector Storage**: LanceDB initialized locally at `/tmp/lancedb_data`.
- **Embeddings**: Amazon Bedrock (`amazon.titan-embed-text-v1`).
- **Infrastructure**: AWS CloudFormation for prerequisites (S3, IAM Roles).
- **Orchestration**: Custom bash scripts orchestrating the deployment and authentication via the `aws lambda-microvms` CLI extension.

## Prerequisites

1. AWS CLI installed and configured.
2. `lambda-microvms` CLI extension installed.
3. `jq` installed for JSON parsing.
4. AWS credentials with permissions to manage CloudFormation, IAM, S3, and Lambda MicroVMs.

## Usage Instructions

### 1. Deployment

Deploy the infrastructure, package the application, and provision the MicroVM:

```bash
./deploy.sh
```

During deployment, the script automatically fetches the `al2023-1` managed base image and deploys the MicroVM. It captures the resulting endpoint and stores it in a local `.env` file (e.g., `MICROVM_URL=https://...`).

### 2. Ingesting Documents

You can ingest multiple PDF documents from a specific directory into the vector store.

```bash
./ingest_all.sh /path/to/pdf/directory
```

The script will securely authenticate using `aws lambda-microvms create-microvm-auth-token` and upload the documents to the running MicroVM for chunking and embedding.

### 3. Querying

Ask a question against your ingested knowledge base:

```bash
./query.sh "Your question here?"
```

This will embed your question via Bedrock, query the local LanceDB, and return the most relevant text chunks directly from the stateful MicroVM memory.
