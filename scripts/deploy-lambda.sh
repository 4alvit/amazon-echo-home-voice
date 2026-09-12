#!/usr/bin/env bash
set -euo pipefail
: "${AWS_PROFILE:?Set an explicitly authorized personal AWS profile}"
: "${AWS_REGION:=us-east-1}"
: "${ASK_SKILL_ID:?Set the existing Alexa custom skill ID}"
: "${IGW_URL:?Set the HTTPS /v1/energy endpoint}"
: "${IGW_READ_TOKEN_SECRET_ARN:?Set the existing Secrets Manager secret ARN}"
: "${STACK_NAME:=home-energy-alexa}"
cd "$(dirname "$0")/.."
sam build --template-file template.yaml
sam deploy --template-file .aws-sam/build/template.yaml \
  --stack-name "$STACK_NAME" --profile "$AWS_PROFILE" --region "$AWS_REGION" \
  --resolve-s3 --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  --parameter-overrides \
  "AskSkillId=$ASK_SKILL_ID" "GatewayUrl=$IGW_URL" \
  "GatewayReadTokenSecretArn=$IGW_READ_TOKEN_SECRET_ARN" \
  "CloudflareSecretArn=${CF_ACCESS_SECRET_ARN:-}"
