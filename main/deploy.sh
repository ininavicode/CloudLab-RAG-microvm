#!/bin/bash
set -e

STACK_NAME="jmejias-rag-microvm-stack"
REGION="us-east-1"
IMAGE_NAME="jmejias-microvm-rag-image"
BASELINE_MEMORY_MIB="${BASELINE_MEMORY_MIB:-1024}"

echo "Fetching managed base image ARN..."
BASE_IMAGE_ARN=$(aws lambda-microvms list-managed-microvm-images \
    --query "items[0].imageArn" \
    --output text \
    --region $REGION)

echo "Deploying CloudFormation stack..."
aws cloudformation deploy \
    --template-file template.yaml \
    --stack-name $STACK_NAME \
    --capabilities CAPABILITY_NAMED_IAM \
    --region $REGION

echo "Fetching outputs from CloudFormation..."
CODE_BUCKET=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME --region $REGION \
    --query 'Stacks[0].Outputs[?OutputKey==`CodeBucketName`].OutputValue' \
    --output text)
BUILD_ROLE_ARN=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME --region $REGION \
    --query 'Stacks[0].Outputs[?OutputKey==`BuildRoleArn`].OutputValue' \
    --output text)
EXECUTION_ROLE_ARN=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME --region $REGION \
    --query 'Stacks[0].Outputs[?OutputKey==`ExecutionRoleArn`].OutputValue' \
    --output text)

echo "Packaging application..."
# -j stores files flat (no directory prefix) so Dockerfile is at the top level of the zip
zip -j app.zip Dockerfile app.py requirements.txt

echo "Uploading artifact to S3 ($CODE_BUCKET)..."
aws s3 cp app.zip s3://$CODE_BUCKET/app.zip

# Determine whether the image already exists using its ARN from list output,
# since get-microvm-image only accepts ARNs but we don't have one yet at first run.
echo "Checking if MicroVM image '$IMAGE_NAME' already exists..."
IMAGE_ARN=$(aws lambda-microvms list-microvm-images \
    --region $REGION \
    --query "items[?name=='$IMAGE_NAME'].imageArn" \
    --output text)

if [ -z "$IMAGE_ARN" ]; then
    echo "Image not found. Creating new image..."
    CREATE_OUTPUT=$(aws lambda-microvms create-microvm-image \
        --name "$IMAGE_NAME" \
        --code-artifact "uri=s3://$CODE_BUCKET/app.zip" \
        --base-image-arn "$BASE_IMAGE_ARN" \
        --build-role-arn "$BUILD_ROLE_ARN" \
        --resources "[{\"minimumMemoryInMiB\": $BASELINE_MEMORY_MIB}]" \
        --hooks '{"port":8080,"microvmImageHooks":{"ready":"ENABLED","readyTimeoutInSeconds":60}}' \
        --region $REGION)
    IMAGE_ARN=$(echo "$CREATE_OUTPUT" | jq -r '.imageArn')
else
    echo "Image exists (ARN: $IMAGE_ARN). Updating..."
    aws lambda-microvms update-microvm-image \
        --image-identifier "$IMAGE_ARN" \
        --code-artifact "uri=s3://$CODE_BUCKET/app.zip" \
        --base-image-arn "$BASE_IMAGE_ARN" \
        --build-role-arn "$BUILD_ROLE_ARN" \
        --region $REGION
fi

echo "Waiting for MicroVM image to reach CREATED or UPDATED state (ARN: $IMAGE_ARN)..."
while true; do
    BUILD_STATE=$(aws lambda-microvms get-microvm-image \
        --image-identifier "$IMAGE_ARN" \
        --region $REGION \
        --query "state" \
        --output text)
    echo "  Current build state: $BUILD_STATE"
    if [ "$BUILD_STATE" = "CREATED" ] || [ "$BUILD_STATE" = "UPDATED" ]; then
        echo "Image is ready."
        break
    elif [ "$BUILD_STATE" = "CREATE_FAILED" ] || [ "$BUILD_STATE" = "UPDATE_FAILED" ]; then
        FAILED_VER=$(aws lambda-microvms get-microvm-image \
            --image-identifier "$IMAGE_ARN" \
            --region $REGION \
            --query "latestFailedImageVersion" \
            --output text)
        REASON=$(aws lambda-microvms get-microvm-image-version \
            --image-identifier "$IMAGE_ARN" \
            --image-version "$FAILED_VER" \
            --region $REGION \
            --query "stateReason" \
            --output text)
        echo "Error: MicroVM image build FAILED. Reason: $REASON"
        exit 1
    fi
    sleep 15
done

echo "Running MicroVM..."
RUN_OUTPUT=$(aws lambda-microvms run-microvm \
    --image-identifier "$IMAGE_ARN" \
    --execution-role-arn "$EXECUTION_ROLE_ARN" \
    --ingress-network-connectors "arn:aws:lambda:$REGION:aws:network-connector:aws-network-connector:ALL_INGRESS" \
    --egress-network-connectors "arn:aws:lambda:$REGION:aws:network-connector:aws-network-connector:INTERNET_EGRESS" \
    --idle-policy '{"autoResumeEnabled":true,"maxIdleDurationSeconds":900,"suspendedDurationSeconds":300}' \
    --region $REGION)

MICROVM_ID=$(echo "$RUN_OUTPUT" | jq -r '.microvmId')
ENDPOINT=$(echo "$RUN_OUTPUT" | jq -r '.endpoint')

echo "Waiting for MicroVM to reach RUNNING state..."
while true; do
    MVM_STATE=$(aws lambda-microvms get-microvm \
        --microvm-identifier "$MICROVM_ID" \
        --region $REGION \
        --query "state" \
        --output text)
    echo "  Current MicroVM state: $MVM_STATE"
    if [ "$MVM_STATE" = "RUNNING" ]; then
        echo "MicroVM is running."
        break
    elif [ "$MVM_STATE" = "FAILED" ]; then
        echo "Error: MicroVM FAILED."
        exit 1
    fi
    sleep 10
done

if [[ "$ENDPOINT" != http* ]]; then
    ENDPOINT="https://$ENDPOINT"
fi
echo "MICROVM_URL=$ENDPOINT" > .env
echo "MICROVM_ID=$MICROVM_ID" >> .env

echo "Deployment complete! Endpoint and ID saved to .env"
