import json
import types
import unittest
from unittest import mock

import codexdispatch.dispatcher as dispatcher


class TestCallOrchestrator(unittest.TestCase):
    def test_uses_openai_stub(self):
        fc = types.SimpleNamespace(
            name="orchestrator_decision",
            arguments=json.dumps({"conclusion": "valid", "summary": "done"}),
        )
        msg = types.SimpleNamespace(function_call=fc)
        choice = types.SimpleNamespace(message=msg)
        mock_response = types.SimpleNamespace(choices=[choice])

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
