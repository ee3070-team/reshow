# ReShow EE3070 backend

The staff and device API runs on AWS Lambda behind API Gateway, with Cognito
for staff sign-in, DynamoDB for records and private S3 for media. The physical
device credential currently supports telemetry uploads. The studio API and web
dashboard use their existing, separate release pipeline.

- [Live API documentation](https://urauw4ckw1.execute-api.us-east-1.amazonaws.com/docs)
- [Frontend integration](docs/FRONTEND-MIGRATION-v0.4.md)
- [Device responsibilities](docs/PRODUCER-RESPONSIBILITIES-v0.4.md)
- [Staff image uploads](docs/STAFF-IMAGE-UPLOAD.md)
- [Authentication and account setup](AUTH_INTEGRATION.md)

## GitHub Actions

Backend pull requests run the Python 3.13 unit, contract, and AWS SDK stub tests,
check SonarQube for security findings, and build and smoke-test the Linux arm64
Lambda package. The build uploads the package and SHA-256 as a GitHub Actions
artifact. From this directory, run the same tests with:

```sh
python3.13 -m pip install -r requirements-test.txt
python3.13 -m unittest discover -s tests -v
```

Production deploys from `master` after those checks pass. An AWS administrator
must provision the dedicated role from `infra/github-actions-backend.json`
using the existing account GitHub OIDC provider, then set the repository Actions
variable `RESHOW_BACKEND_DEPLOY_ROLE_ARN` to the role ARN. Use **Run workflow**
on `master` to run the initial deployment. The role covers Lambda code for
`ee3070-reshow-demo-api` only. Deployments check the exact artifact digest and
function configuration, verify the public health and OpenAPI responses, and
restore the previous code package if those checks fail. The checks do not
exercise authenticated database operations. Infrastructure changes remain in
the separate CloudFormation workflow. If a later infrastructure release also
changes the Lambda package, use the same approved source revision.

