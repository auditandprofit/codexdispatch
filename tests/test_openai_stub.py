import json
import os
import time
import types
import unittest
from unittest import mock

import openai

import phase_mode.dispatcher as dispatcher
import phase_mode.openai_stub as openai_stub


class TestCallOrchestrator(unittest.TestCase):
    def test_uses_openai_stub(self):
        item = types.SimpleNamespace(
            type="function_call",
            name="orchestrator_decision",
            arguments=json.dumps({"conclusion": "valid", "summary": "done"}),
        )
        mock_response = types.SimpleNamespace(output=[item])

        with mock.patch(
            "phase_mode.openai_stub.openai_generate_response",
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
            "phase_mode.openai_stub.openai_generate_response",
            side_effect=fake_generate_response,
        ), mock.patch(
            "phase_mode.openai_stub.openai_parse_function_call",
            return_value=("orchestrator_decision", {"inquiry": "ok"}),
        ):
            dispatcher.call_orchestrator("prompt", env={"TEST_KEY": "VAL"})

        self.assertEqual(captured.get("val"), "VAL")
        self.assertNotIn("TEST_KEY", os.environ)

    def test_retry_exits_after_failures(self):
        with mock.patch.object(dispatcher, "BACKOFF_BASE", 0), mock.patch(
            "phase_mode.openai_stub.openai_generate_response",
            side_effect=openai.OpenAIError("boom"),
        ) as mock_gen:
            with self.assertRaises(SystemExit):
                dispatcher.call_orchestrator("prompt")
        self.assertEqual(mock_gen.call_count, dispatcher.MAX_RETRIES)

    def test_retry_recovers_after_failure(self):
        item = types.SimpleNamespace(
            type="function_call",
            name="orchestrator_decision",
            arguments=json.dumps({"inquiry": "more info"}),
        )
        mock_response = types.SimpleNamespace(output=[item])
        side_effects = [openai.OpenAIError("fail"), mock_response]
        with mock.patch.object(dispatcher, "BACKOFF_BASE", 0.01), mock.patch(
            "phase_mode.openai_stub.openai_generate_response",
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
            "phase_mode.openai_stub.openai_configure_api",
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
        self.assertEqual(tools[1]["name"], "helper")
        self.assertEqual(tools[1]["parameters"], {})
        self.assertNotIn("function", tools[1])


class TestParseFunctionCall(unittest.TestCase):
    def test_parses_top_level_function_call(self):
        item = types.SimpleNamespace(
            type="function_call",
            name="orchestrator_decision",
            arguments=json.dumps({"inquiry": "info"}),
        )
        response = types.SimpleNamespace(output=[item])

        name, data = openai_stub.openai_parse_function_call(response)

        self.assertEqual(name, "orchestrator_decision")
        self.assertEqual(data, {"inquiry": "info"})

    def test_parses_legacy_tool_call(self):
        item = types.SimpleNamespace(
            type="tool_call",
            name="orchestrator_decision",
            arguments=json.dumps({"conclusion": "valid"}),
        )
        msg = types.SimpleNamespace(content=[item])
        response = types.SimpleNamespace(output=[msg])

        name, data = openai_stub.openai_parse_function_call(response)

        self.assertEqual(name, "orchestrator_decision")
        self.assertEqual(data, {"conclusion": "valid"})
