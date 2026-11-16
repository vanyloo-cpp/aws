#!/usr/bin/env bash
set -euo pipefail

if (( $# < 1 || $# > 3 )); then
  echo "Usage: bash deploy.sh STACK_NAME [dev|defence] [ALLOWED_IPV4_CIDR]" >&2
  exit 2
fi

stack_name=$1
environment=${2:-dev}
allowed_cidr=${3:-0.0.0.0/0}
if [[ ! $stack_name =~ ^[a-z][a-z0-9-]{2,31}$ || $stack_name == *-registry ]]; then
  echo "Use a 3–32 character stack name starting with a lowercase letter and ending without -registry." >&2
  exit 2
fi
if [[ $environment != dev && $environment != defence ]]; then
  echo "Environment must be dev or defence." >&2
  exit 2
fi
python3 -c 'import ipaddress, sys; ipaddress.IPv4Network(sys.argv[1])' "$allowed_cidr"

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export AWS_REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-central-1}}
export AWS_PAGER=""
registry_stack="$stack_name-registry"

docker info >/dev/null
aws cloudformation validate-template --template-body file://infra/registry.yaml >/dev/null
aws cloudformation validate-template --template-body file://infra/application.yaml >/dev/null

echo "Deploying container registry $registry_stack in $AWS_REGION"
aws cloudformation deploy \
  --stack-name "$registry_stack" \
  --template-file infra/registry.yaml \
  --no-fail-on-empty-changeset

# Read all named outputs together; CloudFormation does not guarantee output order.
registry_outputs=$(aws cloudformation describe-stacks --stack-name "$registry_stack" \
  --query 'Stacks[0].Outputs' --output json)
read -r repo_name repo_uri repo_arn < <(
  python3 -c 'import json, sys
values = {output["OutputKey"]: output["OutputValue"] for output in json.load(sys.stdin)}
print(values["RepositoryName"], values["RepositoryUri"], values["RepositoryArn"])' <<<"$registry_outputs"
)

image_hash=$(sha256sum app/Dockerfile app/requirements.txt app/server.py | sha256sum)
image_tag=${image_hash%% *}
image_with_tag="$repo_uri:$image_tag"

if ! lookup=$(aws ecr describe-images --repository-name "$repo_name" \
  --image-ids "imageTag=$image_tag" --query 'imageDetails[0].imageDigest' --output text 2>&1); then
  if [[ $lookup != *ImageNotFoundException* ]]; then
    echo "$lookup" >&2
    exit 1
  fi

  docker_config=$(mktemp -d)
  trap 'rm -rf -- "$docker_config"' EXIT
  registry_host=${repo_uri%%/*}
  aws ecr get-login-password | docker --config "$docker_config" login --username AWS --password-stdin "$registry_host"
  docker --config "$docker_config" build --platform linux/amd64 --tag "$image_with_tag" app
  docker --config "$docker_config" push "$image_with_tag"
  lookup=$(aws ecr describe-images --repository-name "$repo_name" \
    --image-ids "imageTag=$image_tag" --query 'imageDetails[0].imageDigest' --output text)
fi
if [[ ! $lookup =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "ECR did not return an image digest for $image_with_tag." >&2
  exit 1
fi

echo "Deploying application stack $stack_name"
aws cloudformation deploy \
  --stack-name "$stack_name" \
  --template-file infra/application.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides "Environment=$environment" "ImageUri=$repo_uri@$lookup" \
    "RepositoryArn=$repo_arn" "AllowedCidr=$allowed_cidr" \
  --no-fail-on-empty-changeset

aws cloudformation describe-stacks --stack-name "$stack_name" \
  --query 'Stacks[0].Outputs[*].[OutputKey,OutputValue]' --output table
