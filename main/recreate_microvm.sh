#!/bin/bash
# Terminates the MicroVM currently recorded in .env (if it still exists)
# and launches a fresh one from the existing image, updating .env afterwards.
set -e

REGION="us-east-1"
IMAGE_NAME="jmejias-microvm-rag-image"
STACK_NAME="jmejias-rag-microvm-stack"

# ── 1. Load current .env ────────────────────────────────────────────────────
if [ -f ".env" ]; then
    source .env
else
    echo "Warning: .env not found, skipping termination step."
fi

# ── 2. Terminate existing MicroVM (ignore errors if it's already gone) ──────
if [ -n "$MICROVM_ID" ]; then
    echo "Terminating MicroVM $MICROVM_ID ..."
    aws lambda-microvms terminate-microvm \
        --microvm-identifier "$MICROVM_ID" \
        --region "$REGION" 2>/dev/null \
        && echo "Termination request sent." \
        || echo "MicroVM not found or already terminated — continuing."
else
    echo "No MICROVM_ID in .env, skipping termination."
fi

# ── 3. Fetch infrastructure ARNs ────────────────────────────────────────────
echo "Fetching Execution Role ARN..."
EXECUTION_ROLE_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='ExecutionRoleArn'].OutputValue" \
    --output text)

if [ -z "$EXECUTION_ROLE_ARN" ] || [ "$EXECUTION_ROLE_ARN" = "None" ]; then
    echo "Error: Could not get ExecutionRoleArn from CloudFormation stack '$STACK_NAME'."
    exit 1
fi

echo "Fetching Image ARN for '$IMAGE_NAME'..."
IMAGE_ARN=$(aws lambda-microvms list-microvm-images \
    --region "$REGION" \
    --query "items[?name=='$IMAGE_NAME'].imageArn" \
    --output text)

if [ -z "$IMAGE_ARN" ] || [ "$IMAGE_ARN" = "None" ]; then
    echo "Error: Image '$IMAGE_NAME' not found. Deploy the image first with deploy.sh."
    exit 1
fi

echo "Using Image ARN: $IMAGE_ARN"

# ── 4. Run a new MicroVM ─────────────────────────────────────────────────────
echo "Running new MicroVM..."
RUN_OUTPUT=$(aws lambda-microvms run-microvm \
    --image-identifier "$IMAGE_ARN" \
    --execution-role-arn "$EXECUTION_ROLE_ARN" \
    --ingress-network-connectors "arn:aws:lambda:$REGION:aws:network-connector:aws-network-connector:ALL_INGRESS" \
    --egress-network-connectors "arn:aws:lambda:$REGION:aws:network-connector:aws-network-connector:INTERNET_EGRESS" \
    --idle-policy '{"autoResumeEnabled":true,"maxIdleDurationSeconds":900,"suspendedDurationSeconds":300}' \
    --region "$REGION")

NEW_MICROVM_ID=$(echo "$RUN_OUTPUT" | jq -r '.microvmId')
NEW_ENDPOINT=$(echo "$RUN_OUTPUT" | jq -r '.endpoint')

if [ -z "$NEW_MICROVM_ID" ] || [ "$NEW_MICROVM_ID" = "null" ]; then
    echo "Error: run-microvm did not return a microvmId."
    echo "$RUN_OUTPUT"
    exit 1
fi

echo "New MicroVM ID: $NEW_MICROVM_ID"

# ── 5. Wait for RUNNING ──────────────────────────────────────────────────────
echo "Waiting for MicroVM to reach RUNNING state..."
while true; do
    MVM_STATE=$(aws lambda-microvms get-microvm \
        --microvm-identifier "$NEW_MICROVM_ID" \
        --region "$REGION" \
        --query "state" \
        --output text)
    echo "  State: $MVM_STATE"
    if [ "$MVM_STATE" = "RUNNING" ]; then
        echo "MicroVM is RUNNING."
        break
    elif [ "$MVM_STATE" = "FAILED" ]; then
        echo "Error: MicroVM entered FAILED state."
        exit 1
    fi
    sleep 5
done

# ── 6. Normalise endpoint URL ────────────────────────────────────────────────
if [[ "$NEW_ENDPOINT" != http* ]]; then
    NEW_ENDPOINT="https://$NEW_ENDPOINT"
fi

# ── 7. Update .env (preserve S3_BUCKET if already there) ────────────────────
# Read S3_BUCKET from current .env or config.json as fallback
CURRENT_S3_BUCKET="${S3_BUCKET}"
if [ -z "$CURRENT_S3_BUCKET" ] && [ -f "config.json" ]; then
    CURRENT_S3_BUCKET=$(jq -r '.S3_BUCKET' config.json)
fi

{
    echo "MICROVM_URL=$NEW_ENDPOINT"
    echo "MICROVM_ID=$NEW_MICROVM_ID"
    [ -n "$CURRENT_S3_BUCKET" ] && echo "S3_BUCKET=$CURRENT_S3_BUCKET"
} > .env

echo "==========================================="
echo "New MicroVM ready!"
echo "  Endpoint: $NEW_ENDPOINT"
echo "  ID:       $NEW_MICROVM_ID"
echo ".env updated."
echo "==========================================="
