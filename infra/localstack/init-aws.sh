#!/bin/bash
# Runs inside LocalStack once it is ready. Idempotent.
set -euo pipefail
R=ap-south-1

for q in order-events reminders; do
  awslocal sqs create-queue --region $R --queue-name "$q-dlq" >/dev/null
  DLQ_ARN=$(awslocal sqs get-queue-attributes --region $R \
    --queue-url "http://localhost:4566/000000000000/$q-dlq" \
    --attribute-names QueueArn --query Attributes.QueueArn --output text)
  awslocal sqs create-queue --region $R --queue-name "$q" \
    --attributes "{\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"5\\\"}\",\"VisibilityTimeout\":\"30\"}" >/dev/null
done

awslocal s3 mb s3://converse-artifacts --region $R >/dev/null 2>&1 || true

awslocal iam create-role --role-name converse-scheduler \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"scheduler.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
  >/dev/null 2>&1 || true

echo "converse: localstack resources ready"
