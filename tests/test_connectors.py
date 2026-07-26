"""Tests for the separate Claude and Codex subscription connectors."""

import json
import io
import subprocess
import unittest
from unittest.mock import patch

from connectors import claude_subscription, codex_subscription


class TestClaudeSubscriptionConnector(unittest.TestCase):
    @patch("connectors.claude_subscription.subprocess.run")
    def test_returns_only_sanitized_account_fields(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "loggedIn": True,
                    "email": "andrew@example.com",
                    "subscriptionType": "max",
                    "authMethod": "claude.ai",
                    "accessToken": "must-not-leak",
                }
            ),
            stderr="",
        )
        result = claude_subscription.read_subscription()
        self.assertTrue(result["available"])
        self.assertEqual(result["account"]["plan"], "max")
        self.assertNotIn("accessToken", json.dumps(result))
        self.assertEqual(result["windows"], {})

    @patch("connectors.claude_subscription.subprocess.run")
    def test_reports_logged_out_state(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not logged in"
        )
        result = claude_subscription.read_subscription()
        self.assertFalse(result["available"])
        self.assertEqual(result["error"], "Claude CLI is not logged in")

    @patch("connectors.claude_subscription.subprocess.run")
    def test_distinguishes_unsupported_cli_version(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="unknown option: --json",
        )
        result = claude_subscription.read_subscription()
        self.assertFalse(result["available"])
        self.assertIn("does not support", result["error"])
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    @patch("connectors.claude_subscription.subprocess.run")
    def test_reports_malformed_json(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{not-json", stderr=""
        )
        result = claude_subscription.read_subscription()
        self.assertFalse(result["available"])
        self.assertIsNotNone(result["error"])

    @patch("connectors.claude_subscription.subprocess.run")
    def test_reports_timeout(self, run):
        run.side_effect = subprocess.TimeoutExpired(cmd=["claude"], timeout=0.01)
        result = claude_subscription.read_subscription(timeout=0.01)
        self.assertFalse(result["available"])
        self.assertEqual(result["error"], "Claude account lookup timed out")


class TestCodexSubscriptionConnector(unittest.TestCase):
    def test_extracts_supported_windows_and_plan(self):
        account, windows, reset_credits = codex_subscription._extract_rate_limits(
            {
                "rateLimits": {
                    "planType": "pro",
                    "primary": {
                        "usedPercent": 25,
                        "windowDurationMins": 10080,
                        "resetsAt": 1785508626,
                    },
                    "secondary": {
                        "usedPercent": 10,
                        "windowDurationMins": 300,
                    },
                }
            }
        )
        self.assertEqual(account, {"plan": "pro"})
        self.assertEqual(windows["seven_day"]["remaining_percent"], 75)
        self.assertEqual(windows["five_hour"]["remaining_percent"], 90)
        self.assertEqual(reset_credits, {})

    def test_keeps_reset_credit_count_and_earliest_expiry_only(self):
        _, _, reset_credits = codex_subscription._extract_rate_limits(
            {
                "rateLimits": {},
                "rateLimitResetCredits": {
                    "availableCount": 3,
                    "credits": [
                        {
                            "id": "must-not-leak",
                            "status": "available",
                            "expiresAt": 1782604800,
                        },
                        {
                            "id": "also-must-not-leak",
                            "status": "available",
                            "expiresAt": 1782000000,
                        },
                    ],
                },
            }
        )
        self.assertEqual(reset_credits["available_count"], 3)
        self.assertIn("expires_at", reset_credits)
        self.assertNotIn("must-not-leak", json.dumps(reset_credits))

    def test_rejects_unknown_account_fields(self):
        safe = codex_subscription._safe_account(
            {
                "account": {
                    "email": "andrew@example.com",
                    "planType": "pro",
                    "accessToken": "must-not-leak",
                }
            }
        )
        self.assertEqual(safe, {"email": "andrew@example.com", "plan": "pro"})
        self.assertNotIn("accessToken", safe)

    def test_ignores_implausible_reset_timestamp(self):
        parsed = codex_subscription._safe_window(
            {
                "usedPercent": 12,
                "windowDurationMins": 10080,
                "resetsAt": 42,
            }
        )
        self.assertIsNotNone(parsed)
        _, window = parsed
        self.assertNotIn("resets_at", window)

    @patch("connectors.codex_subscription._reader")
    @patch("connectors.codex_subscription.subprocess.Popen")
    def test_reports_rate_limit_timeout(self, popen, reader):
        class FakeProcess:
            def __init__(self):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO()
                self.returncode = None

            def poll(self):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

        def enqueue_initialize(_stream, messages):
            messages.put({"id": 1, "result": {}})

        popen.return_value = FakeProcess()
        reader.side_effect = enqueue_initialize
        result = codex_subscription.read_subscription(timeout=0.01)
        self.assertFalse(result["available"])
        self.assertEqual(result["error"], "Codex subscription lookup timed out")


if __name__ == "__main__":
    unittest.main()
