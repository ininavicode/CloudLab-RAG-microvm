#!/bin/bash
set -e

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <question_string>"
    exit 1
fi

QUESTION=$1

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

echo "Querying..."
# Construct JSON payload
PAYLOAD=$(jq -n --arg q "$QUESTION" '{"question": $q}')

curl -X POST "$MICROVM_URL/query" \
    -H "X-aws-proxy-auth: $TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD"

echo -e "\n"
