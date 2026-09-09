#!/bin/bash
set -e

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <directory_with_pdfs>"
    exit 1
fi

DIR=$1

if [ -f ".env" ]; then
    source .env
fi

if [ -z "$MICROVM_URL" ]; then
    echo "Error: MICROVM_URL environment variable is not set."
    echo "Please ensure the .env file was created by deploy.sh."
    exit 1
fi

echo "Generating auth token..."
TOKEN_JSON=$(aws lambda-microvms create-microvm-auth-token)
TOKEN=$(echo "$TOKEN_JSON" | jq -r '.authToken."X-aws-proxy-auth"')

for pdf_file in "$DIR"/*.pdf; do
    if [ -f "$pdf_file" ]; then
        echo "Ingesting $pdf_file..."
        curl -X POST "$MICROVM_URL/ingest" \
            -H "X-aws-proxy-auth: $TOKEN" \
            -F "file=@$pdf_file"
        echo -e "\n"
    fi
done

echo "Ingestion process finished."
