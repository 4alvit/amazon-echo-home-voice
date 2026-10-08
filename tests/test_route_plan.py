import builtins
from contextlib import redirect_stderr
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("route_plan", Path(__file__).parents[1] / "scripts/prepare-tunnel-route.py")
route_plan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(route_plan)


class RoutePlanTests(unittest.TestCase):
    def test_preserves_every_existing_rule_and_top_level_setting(self):
        old = {"warp-routing": {"enabled": True}, "ingress": [
            {"hostname": "igw.example.com", "service": "http://igw:8080", "originRequest": {"access": {"required": True}}},
            {"service": "http_status:404"},
        ]}
        updated, index = route_plan.candidate_config(old, "alexa.example.com", "http://127.0.0.1:8091")
        self.assertEqual(index, 1)
        self.assertEqual(updated["ingress"][:index] + updated["ingress"][index + 2:], old["ingress"])
        self.assertTrue(updated["warp-routing"]["enabled"])
        self.assertEqual(len(old["ingress"]), 2)
        self.assertEqual(updated["ingress"][1]["path"], "^/alexa$")
        self.assertEqual(updated["ingress"][2], {"hostname": "alexa.example.com", "service": "http_status:404"})

    def test_places_specific_route_before_a_matching_wildcard(self):
        old = {"ingress": [{"hostname": "*.example.com", "service": "http://existing:80"}, {"service": "http_status:404"}]}
        updated, index = route_plan.candidate_config(old, "alexa.example.com", "http://127.0.0.1:8091")
        self.assertEqual(index, 0)
        self.assertEqual(updated["ingress"][2:], old["ingress"])

    def test_refuses_existing_hostname_and_nonstandard_catch_all(self):
        for old in (
            {"ingress": [{"hostname": "alexa.example.com", "service": "http://old:80"}, {"service": "http_status:404"}]},
            {"ingress": [{"service": "http://default:80"}]},
        ):
            with self.subTest(old=old), self.assertRaises(route_plan.PlanError):
                route_plan.candidate_config(old, "alexa.example.com", "http://127.0.0.1:8091")

    def test_refuses_global_access_and_nonlocal_or_credentialed_origin(self):
        old = {"ingress": [{"service": "http_status:404"}]}
        for origin in ("http://other-host:8091", "http://user:pass@127.0.0.1:8091", "http://127.0.0.1:8091/alexa"):
            with self.subTest(origin=origin), self.assertRaises(route_plan.PlanError):
                route_plan.candidate_config(old, "alexa.example.com", origin)
        with self.assertRaises(route_plan.PlanError):
            route_plan.candidate_config(old | {"originRequest": {"access": {"required": True}}}, "alexa.example.com", "http://127.0.0.1:8091")

    def test_missing_project_is_reported_before_credentials_or_io(self):
        original_import = builtins.__import__

        def missing_project(name, *args, **kwargs):
            if name == "amazon_echo_home_voice.tls_policy":
                raise ModuleNotFoundError("synthetic missing installation")
            return original_import(name, *args, **kwargs)

        argv = ["prepare-tunnel-route.py"]
        for name, value in (
            ("account-id", "a" * 32), ("zone-id", "b" * 32),
            ("tunnel-id", "a" * 8 + "-" + "-".join(["a" * 4] * 3) + "-" + "a" * 12),
            ("connector-id", "b" * 8 + "-" + "-".join(["b" * 4] * 3) + "-" + "b" * 12),
            ("hostname", "alexa.example.com"), ("state-dir", "unused state directory"),
        ):
            argv.extend(("--" + name, value))
        errors = io.StringIO()
        with patch("builtins.__import__", side_effect=missing_project), \
                patch.object(route_plan.sys, "argv", argv), \
                patch.object(route_plan, "auth_headers") as credentials, \
                patch.object(route_plan, "api_get") as request, \
                patch.object(route_plan.Path, "mkdir") as mkdir, redirect_stderr(errors):
            self.assertEqual(route_plan.main(), 1)
        credentials.assert_not_called()
        request.assert_not_called()
        mkdir.assert_not_called()
        self.assertEqual(errors.getvalue(), "Run this script from the installed project environment\n")
