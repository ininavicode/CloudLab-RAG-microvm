#!/bin/bash
# ingest_all.sh — Uploads PDFs to S3 and triggers ingestion via the MicroVM /ingest endpoint.
#
# Usage:
#   ./ingest_all.sh <directory_with_pdfs>
#
# Reads MICROVM_URL, MICROVM_ID and S3_BUCKET from main/.env (or environment).
set -e

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <directory_with_pdfs>"
    exit 1
fi

DIR="$1"

# Load .env from the main/ subdirectory (same convention as run_benchmarks.py)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

if [ -z "$MICROVM_URL" ] || [ -z "$MICROVM_ID" ]; then
    echo "Error: MICROVM_URL or MICROVM_ID are not set."
    echo "Please ensure main/.env was created by deploy.sh."
    exit 1
fi

if [ -z "$S3_BUCKET" ]; then
    echo "Error: S3_BUCKET is not set in main/.env."
    exit 1
fi

echo "Generating auth token for MicroVM $MICROVM_ID..."
TOKEN_JSON=$(aws lambda-microvms create-microvm-auth-token \
    --microvm-identifier "$MICROVM_ID" \
    --expiration-in-minutes 30 \
    --allowed-ports '[{"allPorts":{}}]')

TOKEN=$(echo "$TOKEN_JSON" | jq -r '.authToken["X-aws-proxy-auth"]')

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
    echo "Error: Failed to obtain auth token."
    exit 1
fi

shopt -s nullglob
PDF_FILES=("$DIR"/*.pdf)

if [ ${#PDF_FILES[@]} -eq 0 ]; then
    echo "No PDF files found in $DIR"
    exit 1
fi

echo "Found ${#PDF_FILES[@]} PDF(s) to ingest."
echo ""

for pdf_file in "${PDF_FILES[@]}"; do
    filename=$(basename "$pdf_file")
    s3_key="pdfs/$filename"

    echo "[$filename] Uploading to s3://$S3_BUCKET/$s3_key ..."
    aws s3 cp "$pdf_file" "s3://$S3_BUCKET/$s3_key"

    echo "[$filename] Triggering ingestion on MicroVM..."
    response=$(curl -s -w "\n%{http_code}" -X POST "$MICROVM_URL/ingest" \
        -H "X-aws-proxy-auth: $TOKEN" \
        -H "X-aws-proxy-port: 8080" \
        -H "Content-Type: application/json" \
        -d "{\"s3_key\": \"$s3_key\"}")

    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n -1)

    if [ "$http_code" -ge 200 ] && [ "$http_code" -lt 300 ]; then
        echo "[$filename] OK ($http_code)"
        echo "$body" | jq '.timings' 2>/dev/null || echo "$body"
    else
        echo "[$filename] ERROR ($http_code): $body"
    fi
    echo ""
done

echo "Ingestion process finished."
