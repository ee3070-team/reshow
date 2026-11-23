"""Gateway policy and prepare-only safety checks; these do not simulate AWS throttling."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))
import app
import prepare_gateway_fix as gateway


def template():
    return json.loads((ROOT / "infra/template.json").read_text())


def original_template():
    value = template()
    for name in gateway.ADDITIONS:
        value["Resources"].pop(name)
    stage = value["Resources"]["ApiStage"]
    stage.pop("DependsOn")
    stage["Properties"].pop("RouteSettings")
    stage["Properties"].pop("AccessLogSettings")
    return value


class GatewayPolicyTests(unittest.TestCase):
    def test_public_preflights_have_separate_bounded_budget_and_actual_staff_routes_keep_jwt(self):
        resources = template()["Resources"]
        stage = resources["ApiStage"]
        self.assertEqual(stage["Properties"]["DefaultRouteSettings"], {
            "ThrottlingRateLimit": 2.0, "ThrottlingBurstLimit": 5})
        expected = {}
        for name in ("StaffApiRoute", "StaffApiRootRoute"):
            route = resources[name]["Properties"]
            self.assertEqual(route["AuthorizationType"], "JWT")
            self.assertEqual(route["AuthorizerId"], {"Ref": "StaffJwtAuthorizer"})
            self.assertEqual(route["AuthorizationScopes"], ["reshow/staff"])
            expected[route["RouteKey"]] = {"ThrottlingRateLimit": 5.0, "ThrottlingBurstLimit": 10}
        for name in ("StaffApiOptionsRoute", "StaffApiRootOptionsRoute"):
            route = resources[name]["Properties"]
            self.assertEqual(route["AuthorizationType"], "NONE")
            self.assertNotIn("AuthorizerId", route)
            expected[route["RouteKey"]] = {"ThrottlingRateLimit": 10.0, "ThrottlingBurstLimit": 20}
        self.assertEqual(stage["Properties"]["RouteSettings"], expected)
        self.assertEqual(set(stage["DependsOn"]), {
            "StaffApiRoute", "StaffApiRootRoute", "StaffApiOptionsRoute", "StaffApiRootOptionsRoute"})
        self.assertEqual(resources["ApiFunction"]["Properties"]["ReservedConcurrentExecutions"], 2)

    def test_cors_preserves_exact_origins_methods_headers_and_exposed_headers(self):
        cors = template()["Resources"]["HttpApi"]["Properties"]["CorsConfiguration"]
        self.assertEqual(set(cors["AllowOrigins"]), {
            "http://localhost:8000", "https://dkbznu9f5nh9n.cloudfront.net"})
        self.assertEqual(set(cors["AllowMethods"]), {"GET", "POST", "PATCH", "PUT", "OPTIONS"})
        self.assertEqual({v.lower() for v in cors["AllowHeaders"]}, {"authorization", "content-type", "if-match"})
        self.assertEqual({v.lower() for v in cors["ExposeHeaders"]}, {"location", "content-disposition", "etag"})
        self.assertEqual(cors["MaxAge"], 600)

    def test_access_logs_have_short_retention_and_only_safe_correlation_fields(self):
        resources = template()["Resources"]
        self.assertEqual(resources["ApiAccessLogGroup"]["Properties"]["RetentionInDays"], 14)
        self.assertEqual(resources["ApiAccessLogGroup"]["DeletionPolicy"], "Retain")
        settings = resources["ApiStage"]["Properties"]["AccessLogSettings"]
        self.assertEqual(settings["DestinationArn"], {"Fn::GetAtt": ["ApiAccessLogGroup", "Arn"]})
        fields = json.loads(settings["Format"])
        self.assertEqual(set(fields.values()), {
            "$context.requestId", "$context.requestTimeEpoch", "$context.httpMethod", "$context.routeKey",
            "$context.status", "$context.integration.integrationStatus", "$context.integration.status",
            "$context.error.responseType", "$context.integration.latency", "$context.responseLatency"})

    def test_staff_preflights_do_not_reach_auth_storage_or_provider(self):
        paths = ("/staff-api", "/staff-api/session", "/staff-api/showcases", "/staff-api/exhibit-profiles",
                 "/staff-api/commands", "/staff-api/workflow-activity", "/staff-api/staff",
                 "/staff-api/analytics/event-report", "/staff-api/accounts", "/staff-api/report-snapshots")
        with patch.object(app, "get_table", side_effect=AssertionError("preflight reached storage")), \
                patch.object(app.auth, "authorize", side_effect=AssertionError("preflight reached auth")):
            for origin in ("http://localhost:8000", "https://dkbznu9f5nh9n.cloudfront.net"):
                for path in paths:
                    for method in ("GET", "POST", "PATCH", "PUT"):
                        event = {"rawPath": path, "headers": {"origin": origin,
                                 "access-control-request-method": method,
                                 "access-control-request-headers": "authorization,content-type,if-match"},
                                 "queryStringParameters": {"limit": "100"},
                                 "requestContext": {"http": {"method": "OPTIONS"}}}
                        result = app.lambda_handler(event, None)
                        self.assertEqual(result["statusCode"], 204, (origin, path, method))
                        self.assertEqual(result["body"], "")


class PreparationSafetyTests(unittest.TestCase):
    def test_template_scope_accepts_only_gateway_patch_and_rejects_stale_runtime_or_auth_changes(self):
        before, after = original_template(), template()
        gateway.verify_scope(before, after)
        for name, change in (
                ("ApiFunction", lambda r: r["Properties"].update(ReservedConcurrentExecutions=10)),
                ("StaffApiRoute", lambda r: r["Properties"].update(AuthorizationType="NONE")),
                ("SimulatorInstance", lambda r: r["Properties"].update(ImageId="different")),
                ("ApiStage", lambda r: r["Properties"].update(DefaultRouteSettings={}))):
            changed = copy.deepcopy(after)
            change(changed["Resources"][name])
            with self.subTest(resource=name), self.assertRaises(ValueError):
                gateway.verify_scope(before, changed)

    def test_preview_rejects_unsafe_resource_change_on_later_page(self):
        class Aws:
            def __init__(self):
                self.calls = []

            def call(self, *args):
                self.calls.append(args)
                if "--next-token" in args:
                    return {"Changes": [{"ResourceChange": {"LogicalResourceId": "ApiFunction",
                            "ResourceType": "AWS::Lambda::Function", "Action": "Modify", "Replacement": "False"}}]}
                return {"NextToken": "next", "Changes": [{"ResourceChange": {"LogicalResourceId": "ApiStage",
                        "ResourceType": "AWS::ApiGatewayV2::Stage", "Action": "Modify", "Replacement": "False"}}]}
        aws = Aws()
        with self.assertRaisesRegex(ValueError, "ApiFunction"):
            gateway.preview_changes(aws, "test")
        self.assertEqual(len(aws.calls), 2)

    def test_preparation_reuses_every_parameter_and_never_packages_or_executes(self):
        class Aws:
            def __init__(self):
                self.calls = []

            def call(self, *args):
                self.calls.append(args)
                if args[0] == "sts":
                    return {"Account": "835822826851"}
                if args[1] == "describe-stacks":
                    return {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}
                if args[1] == "get-template":
                    return {"TemplateBody": original_template()}
                if args[1] == "create-change-set":
                    value = json.loads(Path(args[args.index("--parameters") + 1][7:]).read_text())
                    self.parameters = value
                    return {"Id": "review-only"}
                if args[1] == "describe-change-set":
                    return {"Status": "CREATE_COMPLETE", "Changes": [{"ResourceChange": {
                        "LogicalResourceId": "ApiStage", "ResourceType": "AWS::ApiGatewayV2::Stage",
                        "Action": "Modify", "Replacement": "False"}}]}
                if args[1] in ("validate-template", "wait"):
                    return {}
                raise AssertionError(args)
        aws = Aws()
        with tempfile.TemporaryDirectory() as working:
            result = gateway.prepare(SimpleNamespace(expected_account="835822826851", stack_name="test",
                template=ROOT / "infra/template.json", output_file=Path(working) / "result.json"), aws)
        self.assertFalse(result["executed"])
        self.assertEqual(aws.parameters, [{"ParameterKey": key, "UsePreviousValue": True}
                                         for key in original_template()["Parameters"]])
        self.assertNotIn("execute-change-set", [args[1] for args in aws.calls])


if __name__ == "__main__":
    unittest.main()
