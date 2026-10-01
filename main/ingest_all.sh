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

# Read S3_BUCKET from config.json
S3_BUCKET=$(jq -r '.S3_BUCKET' config.json)
if [ -z "$S3_BUCKET" ] || [ "$S3_BUCKET" = "null" ]; then
    echo "Error: Could not read S3_BUCKET from config.json"
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
        BASENAME=$(basename "$pdf_file")
        S3_KEY="pdfs/$BASENAME"

        echo "Uploading $BASENAME to s3://$S3_BUCKET/$S3_KEY ..."
        aws s3 cp "$pdf_file" "s3://$S3_BUCKET/$S3_KEY"

        echo "Ingesting $BASENAME via MicroVM..."
        curl -s -X POST "$MICROVM_URL/ingest" \
            -H "X-aws-proxy-auth: $TOKEN" \
            -H "X-aws-proxy-port: 8080" \
            -H "Content-Type: application/json" \
            -d "{\"s3_key\": \"$S3_KEY\"}"
        echo -e "\n"
    fi
done

echo "Ingestion process finished."