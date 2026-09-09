#!/bin/bash
set -e

STACK_NAME="jmejias-rag-microvm-stack"
REGION="us-east-1"
IMAGE_NAME="jmejias-rag-microvm-image"

echo "Fetching managed base image..."
BASE_IMAGE=$(aws lambda-microvms list-managed-microvm-images --query "items[0].imageArn" --output text)

echo "Deploying CloudFormation stack..."
aws cloudformation deploy \
    --template-file template.yaml \
    --stack-name $STACK_NAME \
    --capabilities CAPABILITY_NAMED_IAM \
    --region $REGION

echo "Fetching outputs from CloudFormation..."
CODE_BUCKET=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION --query 'Stacks[0].Outputs[?OutputKey==`CodeBucketName`].OutputValue' --output text)
BUILD_ROLE_ARN=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION --query 'Stacks[0].Outputs[?OutputKey==`BuildRoleArn`].OutputValue' --output text)
EXECUTION_ROLE_ARN=$(aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION --query 'Stacks[0].Outputs[?OutputKey==`ExecutionRoleArn`].OutputValue' --output text)

echo "Packaging application..."
zip -r app.zip app.py requirements.txt

echo "Uploading artifact to S3 ($CODE_BUCKET)..."
aws s3 cp app.zip s3://$CODE_BUCKET/app.zip

# Check if the MicroVM image already exists by listing and filtering by name
echo "Checking if MicroVM image '$IMAGE_NAME' already exists..."
EXISTING_IMAGE_ARN=$(aws lambda-microvms list-microvm-images \
    --region $REGION \
    --output json | jq -r ".items[]? | select(.name == \"$IMAGE_NAME\" or .imageName == \"$IMAGE_NAME\") | .imageArn")

if [ -n "$EXISTING_IMAGE_ARN" ]; then
    echo "Image exists (ARN: $EXISTING_IMAGE_ARN). Updating MicroVM image '$IMAGE_NAME'..."
    aws lambda-microvms update-microvm-image \
        --image-identifier "$EXISTING_IMAGE_ARN" \
        --base-image-arn $BASE_IMAGE \
        --code-artifact uri=s3://$CODE_BUCKET/app.zip \
        --build-role-arn $BUILD_ROLE_ARN \
        --region $REGION
    IMAGE_ARN="$EXISTING_IMAGE_ARN"
else
    echo "Image not found. Creating MicroVM image '$IMAGE_NAME'..."
    aws lambda-microvms create-microvm-image \
        --name "$IMAGE_NAME" \
        --base-image-arn $BASE_IMAGE \
        --code-artifact uri=s3://$CODE_BUCKET/app.zip \
        --build-role-arn $BUILD_ROLE_ARN \
        --region $REGION
        
    echo "Fetching ARN of newly created MicroVM image..."
    IMAGE_ARN=$(aws lambda-microvms list-microvm-images \
        --region $REGION \
        --query "items[?name=='$IMAGE_NAME'].imageArn" \
        --output text)
fi

# Wait until the image build state reaches CREATED
echo "Waiting for MicroVM image to reach CREATED state (ARN: $IMAGE_ARN)..."
while true; do
    BUILD_STATE=$(aws lambda-microvms get-microvm-image \
        --image-identifier "$IMAGE_ARN" \
        --region $REGION \
        --query 'buildState' \
        --output text)
    echo "  Current build state: $BUILD_STATE"
    if [ "$BUILD_STATE" = "CREATED" ]; then
        echo "Image is ready."
        break
    elif [ "$BUILD_STATE" = "FAILED" ]; then
        echo "Error: MicroVM image build FAILED. Aborting."
        exit 1
    fi
    sleep 15
done

echo "Running MicroVM..."
# Based on instructions: aws lambda-microvms run-microvm using Execution Role, attaching ALL_INGRESS and INTERNET_EGRESS
RUN_OUTPUT=$(aws lambda-microvms run-microvm \
    --image-identifier "$IMAGE_ARN" \
    --role-arn $EXECUTION_ROLE_ARN \
    --network-connectors ALL_INGRESS INTERNET_EGRESS \
    --region $REGION)

ENDPOINT=$(echo "$RUN_OUTPUT" | jq -r '.endpoint')
if [[ "$ENDPOINT" != http* ]]; then
    ENDPOINT="https://$ENDPOINT"
fi
echo "MICROVM_URL=$ENDPOINT" > .env

echo "Deployment complete! Endpoint saved to .env"
