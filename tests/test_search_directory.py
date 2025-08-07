import json

import search_directory


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        # Return something JSON serializable
        return {"output": [{"content": []}]}


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_run_on_directory_calls_openai_with_web_search(tmp_path):
    # Prepare input file and template
    inp = tmp_path / "file.txt"
    inp.write_text("hello")
    template = "You are helpful"

    client = FakeClient()
    result = search_directory.run_on_directory(
        str(tmp_path), template, client=client
    )

    assert "file.txt" in result
    call = client.responses.calls[0]
    assert call["model"] == "o3"
    assert call["service_tier"] == "flex"
    assert call["reasoning"] == {"effort": "high"}
    assert call["tools"] == [{"type": "web_search"}]
    assert call["tool_choice"] == {"type": "web_search"}

    messages = call["input"]
    assert messages[0] == {"role": "system", "content": template}
    assert messages[1] == {"role": "user", "content": "hello"}


def test_run_on_directory_writes_output(tmp_path):
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    input_dir.mkdir()
    (input_dir / "a.txt").write_text("hi")

    client = FakeClient()
    search_directory.run_on_directory(
        str(input_dir), "template", output_dir=str(output_dir), client=client
    )

    out_file = output_dir / "a_response.txt"
    assert out_file.is_file()
    data = out_file.read_text()
    assert data == ""
