#!/usr/bin/env bash
set -euo pipefail

sha=${1:?A commit SHA is required}
action=${2:-deploy}
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid commit SHA'; exit 1; }
case "$action" in
  deploy) document=EE3070-ReShow-DeployFrontend ;;
  validate) document=EE3070-ReShow-ValidateFrontend ;;
  adopt) document=EE3070-ReShow-AdoptFrontend ;;
  *) echo 'Unknown deployment action'; exit 1 ;;
esac

command_id=$(aws ssm send-command --instance-ids i-00301bb65c9133a63 --document-name "$document" --parameters "CommitSha=$sha" --query Command.CommandId --output text)
for attempt in $(seq 1 90); do
  status=$(aws ssm get-command-invocation --command-id "$command_id" --instance-id i-00301bb65c9133a63 --query Status --output text 2>/dev/null || true)
  case "$status" in
    Success|Failed|Cancelled|TimedOut|Cancelling) break ;;
  esac
  sleep 5
done
# Always expose the constrained helper's exact reason, including extraction errors.
aws ssm get-command-invocation --command-id "$command_id" --instance-id i-00301bb65c9133a63 --query '{Status:Status,Output:StandardOutputContent,Error:StandardErrorContent}'
test "$status" = Success
