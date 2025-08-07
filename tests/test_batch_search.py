import json
from pathlib import Path
from unittest import mock

import batch_search


def test_batch_search_forces_web_search(tmp_path):
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "a.txt").write_text("hello")
    (input_dir / "b.txt").write_text("world")

    captured = []

    class FakeResponses:
        def create(self, **kwargs):
            captured.append(kwargs)
            return {"result": "ok"}

    fake_client = mock.MagicMock()
    fake_client.responses = FakeResponses()

    with mock.patch("batch_search.OpenAI", return_value=fake_client):
        batch_search.batch_search(str(input_dir), str(output_dir))

    # Ensure two files processed
    assert len(captured) == 2
    # Ensure tool forcing
    for kwargs in captured:
        assert kwargs["tools"] == [{"type": "web_search"}]
        assert kwargs["tool_choice"] == {"type": "web_search"}
    # Ensure outputs written
    assert (output_dir / "a_response.json").is_file()
    assert (output_dir / "b_response.json").is_file()
