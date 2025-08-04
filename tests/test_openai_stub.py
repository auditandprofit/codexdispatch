import json
import os
import time
import types
import unittest
from unittest import mock

import openai

import codexdispatch.dispatcher as dispatcher
import codexdispatch.openai_stub as openai_stub


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
            return types.SimpleNamespace(output=[types.SimpleNamespace(content=[])])

        with mock.patch(
            "codexdispatch.openai_stub.openai_generate_response",
            side_effect=fake_generate_response,
        ), mock.patch(
            "codexdispatch.openai_stub.openai_parse_function_call",
            return_value=("orchestrator_decision", {"inquiry": "ok"}),
        ):
            dispatcher.call_orchestrator("prompt", env={"TEST_KEY": "VAL"})

        self.assertEqual(captured.get("val"), "VAL")
        self.assertNotIn("TEST_KEY", os.environ)

    def test_retry_exits_after_failures(self):
        with mock.patch.object(dispatcher, "BACKOFF_BASE", 0), mock.patch(
            "codexdispatch.openai_stub.openai_generate_response",
            side_effect=openai.OpenAIError("boom"),
        ) as mock_gen:
            with self.assertRaises(SystemExit):
                dispatcher.call_orchestrator("prompt")
        self.assertEqual(mock_gen.call_count, dispatcher.MAX_RETRIES)

    def test_retry_recovers_after_failure(self):
        item = types.SimpleNamespace(
            type="tool_call",
            name="orchestrator_decision",
            arguments=json.dumps({"inquiry": "more info"}),
        )
        msg = types.SimpleNamespace(content=[item])
        mock_response = types.SimpleNamespace(output=[msg])
        side_effects = [openai.OpenAIError("fail"), mock_response]
        with mock.patch.object(dispatcher, "BACKOFF_BASE", 0.01), mock.patch(
            "codexdispatch.openai_stub.openai_generate_response",
            side_effect=side_effects,
        ) as mock_gen:
            start = time.time()
            result = dispatcher.call_orchestrator("prompt")
            elapsed = time.time() - start
        self.assertEqual(result, {"inquiry": "more info"})
        self.assertEqual(mock_gen.call_count, 2)
        self.assertGreaterEqual(elapsed, 0.01)


class TestGenerateResponse(unittest.TestCase):
    def test_tools_payload_schema(self):
        captured = {}

        class FakeResponses:
            def create(self, **kwargs):
                captured["tools"] = kwargs.get("tools", [])
                return types.SimpleNamespace(output=[])

        fake_client = types.SimpleNamespace(responses=FakeResponses())

        with mock.patch(
            "codexdispatch.openai_stub.openai_configure_api",
            return_value=fake_client,
        ):
            messages = [{"role": "user", "content": "hi"}]
            funcs = [{"name": "helper", "parameters": {}}]
            openai_stub.openai_generate_response(
                messages=messages, functions=funcs
            )

        tools = captured.get("tools", [])
        self.assertEqual(tools[0], {"type": "web_search"})
        self.assertEqual(tools[1]["type"], "function")
        self.assertEqual(tools[1]["function"]["name"], "helper")
        self.assertNotIn("name", tools[1])
