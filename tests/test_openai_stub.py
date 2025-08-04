import json
import os
import types
import unittest
from unittest import mock

import codexdispatch.dispatcher as dispatcher


class TestCallOrchestrator(unittest.TestCase):
    def test_uses_openai_stub(self):
        item = types.SimpleNamespace(
            type="tool_call",
            name="orchestrator_decision",
            arguments=json.dumps({"conclusion": "valid", "summary": "done"}),
        )
        msg = types.SimpleNamespace(content=[item])
        mock_response = types.SimpleNamespace(output=[msg])

        with mock.patch(
            "codexdispatch.openai_stub.openai_generate_response",
            return_value=mock_response,
        ) as mock_gen:
            result = dispatcher.call_orchestrator("prompt")

        self.assertEqual(result, {"conclusion": "valid", "summary": "done"})
        mock_gen.assert_called_once()
        _, kwargs = mock_gen.call_args
        self.assertEqual(kwargs["model"], "o3")
        self.assertEqual(kwargs["service_tier"], "flex")
        self.assertEqual(kwargs["reasoning_effort"], "high")

    def test_env_is_passed(self):
        captured = {}

        def fake_generate_response(**kwargs):
            captured["val"] = os.environ.get("TEST_KEY")
            return types.SimpleNamespace(output=[])

        with mock.patch(
            "codexdispatch.openai_stub.openai_generate_response",
            side_effect=fake_generate_response,
        ):
            dispatcher.call_orchestrator("prompt", env={"TEST_KEY": "VAL"})

        self.assertEqual(captured.get("val"), "VAL")
        self.assertNotIn("TEST_KEY", os.environ)
