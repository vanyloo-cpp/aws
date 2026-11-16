#!/usr/bin/env bash
set -euo pipefail

if (( $# != 1 )); then
  echo 'Usage: bash demo.sh STACK_NAME' >&2
  exit 2
fi
export AWS_REGION=${AWS_REGION:-${AWS_DEFAULT_REGION:-eu-central-1}}
export AWS_PAGER=""
outputs=$(aws cloudformation describe-stacks --stack-name "$1" --query 'Stacks[0].Outputs' --output json)
read -r api_url table_name worker_logs < <(
  python3 -c 'import json, sys
values = {output["OutputKey"]: output["OutputValue"] for output in json.load(sys.stdin)}
print(values["ApiUrl"], values["OrdersTableName"], values["WorkerLogGroup"])' <<<"$outputs"
)

echo 'ALB health check'
curl --fail --silent --show-error --max-time 20 "$api_url/health"
printf '\n\n'

order_id="sis3-$(date -u +%Y%m%d%H%M%S)-$RANDOM"
echo "Publishing order $order_id twice"
for attempt in 1 2; do
  code=$(curl --fail --silent --show-error --max-time 20 \
    --output /dev/null --write-out '%{http_code}' \
    --header 'Content-Type: application/json' \
    --data "{\"orderId\":\"$order_id\"}" "$api_url/orders")
  echo "Request $attempt returned $code"
  [[ $code == 202 ]]
done

echo 'Waiting for both processing and duplicate entries in CloudWatch'
for attempt in {1..20}; do
  logs=$(aws logs tail "$worker_logs" --since 5m --format short)
  if matching_logs=$(printf '%s\n' "$logs" | grep -F "\"orderId\": \"$order_id\"") &&
     [[ $matching_logs == *'"result": "processed"'* && $matching_logs == *'"result": "duplicate"'* ]]; then
    printf '%s\n' "$matching_logs"
    aws dynamodb get-item --table-name "$table_name" --consistent-read \
      --key "{\"orderId\":{\"S\":\"$order_id\"}}" --output json
    exit 0
  fi
  sleep 5
done
echo "Timed out waiting for both CloudWatch log entries for $order_id." >&2
exit 1
