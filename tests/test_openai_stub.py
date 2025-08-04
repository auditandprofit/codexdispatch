import json
import types
import unittest
from unittest import mock

import codexdispatch.dispatcher as dispatcher


class TestCallOrchestrator(unittest.TestCase):
    def test_uses_openai_stub(self):
        def fake_generate_response(*args, **kwargs):
            fc = types.SimpleNamespace(
                name="orchestrator_decision",
                arguments=json.dumps({"conclusion": "valid"}),
            )
            msg = types.SimpleNamespace(function_call=fc)
            choice = types.SimpleNamespace(message=msg)
            return types.SimpleNamespace(choices=[choice])

        with mock.patch(
            "codexdispatch.openai_stub.openai_generate_response",
            side_effect=fake_generate_response,
        ):
            result = dispatcher.call_orchestrator("prompt")
        self.assertEqual(result, {"conclusion": "valid"})
