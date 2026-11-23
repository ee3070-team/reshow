# Staff preflight throttling remediation — 3 November 2026

This is an infrastructure-only change. The working source was copied from the
v0.4.1 permissions release and its ten backend Python/JSON/HTML files were
compared byte-for-byte with the deployed Lambda archive. The runtime package,
records, JWT authorizer, application permissions and device protocol are unchanged.
The source template's resources also matched the current CloudFormation template.

## Evidence and scope

The HTTP API `urauw4ckw1` has an automatically deployed `$default` stage. Both
record URLs and `/staff-api/session` match the same unauthenticated
`OPTIONS /staff-api/{proxy+}` route. Actual staff requests use the existing JWT
`ANY /staff-api/{proxy+}` route with the `reshow/staff` scope. The entire stage
inherited a 2 requests/second, burst-5 budget with no route overrides.

At 13:58, 13:59 and 14:00 HKT the API counted 202, 214 and 134 requests/minute,
with 80, 88 and 31 client errors. Lambda admitted 122, 126 and 103 invocations
and had zero errors or throttles in those three incident minutes. Admissions
near 120/minute match the stage's 2/second budget. The account's regional quota
was 10,000/second and burst 5,000. Lambda's direct OPTIONS path returns 204
before authentication/storage; it has no application preflight limiter. The
429s therefore originate at the gateway's restrictive budget, rather than an
OPTIONS authentication requirement. The traffic's exact client composition was
not measured.

API access logging was disabled during the incident. Historical request IDs
cannot be individually reconstructed from access logs; the supplied HTTP probes,
effective configuration and independent minute-level metrics establish the
correlation. Separate one-per-minute Lambda throttles appeared later and remain
a reason to observe normal browser traffic after remediation. They do not
explain the original window's gateway 429s.

## Configuration

| Setting | Before | After |
| --- | --- | --- |
| Default rate / burst | 2/s / 5 | 2/s / 5 |
| JWT staff proxy and root rate / burst | Inherit 2/s / 5 | 5/s / 10 |
| Public staff OPTIONS proxy and root rate / burst | Inherit 2/s / 5 | 10/s / 20 |
| Lambda reserved concurrency | 2 | 2 |
| Gateway access logs | Disabled | Safe correlation fields, 14-day retention |

The root `OPTIONS /staff-api` route is made explicitly public, matching the
existing public proxy preflight route. The stage explicitly depends on all four
routes so CloudFormation cannot configure a budget for a route before creating
it. Only the staff routes receive new budgets; public/producer defaults stay
bounded at their existing values.

One adapter starts requests at most about 1.82/s (550 ms spacing), with five-second
polls and separate 30-second/focus session checks. The 5/s staff budget provides
headroom for normal paced snapshots, polling/session overlap and short navigation
bursts; the 10/s preflight budget accounts for additional uncached browser
preflights. These are bounded route budgets shared by callers, not a promise of
capacity for unlimited tabs/users. The incident's approximately 3.57 total
requests/second and 15–19 ms average / approximately 60 ms p95 Lambda duration
support this modest increase. Post-deployment observation is still required.

The CORS allowlist remains exactly `http://localhost:8000` and
`https://dkbznu9f5nh9n.cloudfront.net`. Allowed methods remain GET, POST, PATCH,
PUT and OPTIONS; headers remain Authorization, Content-Type and If-Match;
exposed response headers remain Location, Content-Disposition and ETag.

The HTTP API's gateway-generated 429 response has no service-controlled
`Retry-After` customization in this patch. No Retry-After value is invented, and
no application limiter is added. Browser JavaScript cannot read a failed
preflight's status/body as a normal fetch response. If the application later
adds a retry interval, its Retry-After header must also be added to the gateway
and application exposed-header lists.

Access logs include request ID/time, method, route key, response status, Lambda
service status, application status, gateway error type and latencies. They omit
headers, token claims, raw path/query, request/response bodies, IP addresses and
free-form integration error text. The log group is retained on stack removal;
individual events expire after 14 days.

## Safe preparation and drift prevention

Use this helper for this configuration-only update:

```sh
python3 scripts/prepare_gateway_fix.py --output-file /absolute/work/gateway-change-set.json
```

It checks the AWS account, reads the actual stack template and refuses changes
outside the reviewed stage fields and two added gateway/log resources. Every
existing parameter uses `UsePreviousValue`, including BackendKey and the NoEcho
API token; no credential is read or copied. It never packages/uploads Lambda
code, alters media settings, executes a change set or writes application data.
It examines every change-set page and rejects Lambda, EC2, data-resource,
replacement or unrelated changes, including an AMI change caused by SSM parameter
re-resolution. Review its concrete change set before authorized execution.

Keep the route budgets/logging in `infra/template.json`; a console-only update
would be overwritten by the next stack deployment. Do not deploy the older
v0.4.0 backend package: the current runtime contains newer permission and read
handling. The general deployment helper packages code and is intentionally not
used for this infrastructure-only remediation.

## Verification and remaining work

The focused suite checks preflight handling without authentication/storage,
exact CORS boundaries, JWT scopes, bounded route settings, safe log fields and
the preparation helper's parameter/resource guards. AWS template validation
checks the CloudFormation syntax. These local/configuration checks do not prove
deployed record loading or the gateway's live token-bucket behavior.

After authorized deployment, verify exact showcase/exhibit-profile URLs,
session and all six tabs' endpoint preflights from both origins; verify their
actual method/header combinations. Check missing/invalid credentials still
fail on actual requests and preserve Viewer/Operator/Administrator checks.
Use an existing Administrator session for read-only Labels/Exhibits and all
other tabs, observing at least two minutes of normal navigation, five-second
polls and 30-second session checks. Record timestamps, sanitized request IDs,
response statuses/volumes and any gateway/Lambda throttles. Do not create
records, invitations, commands or acknowledgements for this test.

Exercise deliberate throttling/recovery only on an isolated API with a bounded
request count. Do not load-test the production gateway. A remaining frontend
change should stop error-triggered full-snapshot cascades, implement shared
cooldown/backoff and improve failure wording; uncertain mutations must not be
automatically retried.

## Rollback

Before execution, save the current CloudFormation template and stage readback.
To roll back, prepare a new gateway-only change set using that saved template
and `UsePreviousValue` for all current parameters. It restores the inherited
2/s, burst-5 staff budgets, removes the new explicit root OPTIONS route and
disables stage access logging. Review all change-set pages to ensure no Lambda,
EC2 or data changes. Retain the log group and its 14-day expiry to preserve
investigation evidence. Restoring the old budgets can reproduce the incident.

`infra/gateway-rollback.json` is the exact saved pre-change CloudFormation
template. `infra/gateway-previous-parameters.json` contains only parameter names
and `UsePreviousValue: true`, with no values. Prepare a rollback for review with:

```sh
aws cloudformation create-change-set --profile my-aws --region us-east-1 \
  --stack-name ee3070-reshow-demo --change-set-name reshow-gateway-rollback \
  --change-set-type UPDATE --template-body file://infra/gateway-rollback.json \
  --parameters file://infra/gateway-previous-parameters.json --capabilities CAPABILITY_IAM
```

Use a fresh change-set name when repeating this command. Creating the change
set does not execute it. If the stack changed after this incident, compare that
current template first; do not overwrite later unrelated changes with this
historical rollback file.

The forward-only preparation helper deliberately rejects removals and therefore
does not execute or prepare this rollback automatically. Do not change the
Lambda artifact key, token, Cognito settings, origins or stored records during
rollback.

AWS references: [HTTP API throttling](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html),
[access logging](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-logging.html),
[supported log variables](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-logging-variables.html),
[CloudFormation stage settings](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-apigatewayv2-stage.html).
