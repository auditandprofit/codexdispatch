import os
import sys

import pytest
import openai
from unittest import mock

# Ensure project root is on sys.path for imports
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def _mock_openai_responses(request):
    if request.node.get_closest_marker("openai_live") or os.environ.get("OPENAI_LIVE"):
        yield
        return
    with mock.patch.object(openai.OpenAI, "responses") as stub:
        stub.create.side_effect = RuntimeError("stubbed")
        yield

