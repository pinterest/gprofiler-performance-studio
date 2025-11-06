#!/bin/bash

echo "🚀 Initializing LocalStack S3 and SQS..."

# Create S3 bucket (must match BUCKET_NAME in .env)
awslocal s3 mb s3://performance_studio_bucket
echo "✅ S3 bucket 'performance_studio_bucket' created"

# Create SQS queue
awslocal sqs create-queue --queue-name performance_studio_queue
echo "✅ SQS queue 'performance_studio_queue' created"

# Get queue URL
QUEUE_URL=$(awslocal sqs get-queue-url --queue-name performance_studio_queue --output text)
echo "✅ Queue URL: $QUEUE_URL"

echo "🎉 LocalStack initialization complete!"


