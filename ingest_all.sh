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

if [ -z "$MICROVM_URL" ] || [ -z "$MICROVM_ID" ]; then
    echo "Error: MICROVM_URL or MICROVM_ID environment variables are not set."
    echo "Please ensure the .env file was created by deploy.sh."
    exit 1
fi

echo "Generating auth token..."
TOKEN_JSON=$(aws lambda-microvms create-microvm-auth-token \
    --microvm-identifier "$MICROVM_ID" \
    --expiration-in-minutes 30 \
    --allowed-ports '[{"allPorts":{}}]')
    
TOKEN=$(echo "$TOKEN_JSON" | jq -r '.authToken["X-aws-proxy-auth"]')

for pdf_file in "$DIR"/*.pdf; do
    if [ -f "$pdf_file" ]; then
        echo "Ingesting $pdf_file..."
        curl -s -X POST "$MICROVM_URL/ingest" \
            -H "X-aws-proxy-auth: $TOKEN" \
            -H "X-aws-proxy-port: 8080" \
            -F "file=@$pdf_file"
        echo -e "\n"
    fi
done

echo "Ingestion process finished."
