import types

import search_directory


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(output=[types.SimpleNamespace(content=[])])


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_run_on_directory_calls_openai_with_web_search(tmp_path):
    # Prepare input file and template
    inp = tmp_path / "file.txt"
    inp.write_text("hello")
    template = "You are helpful"

    client = FakeClient()
    result = search_directory.run_on_directory(str(tmp_path), template, client)

    assert "file.txt" in result
    call = client.responses.calls[0]
    assert call["model"] == "gpt-4o"
    assert call["tools"] == [{"type": "web_search"}]
    assert call["tool_choice"] == {"type": "web_search"}

    messages = call["input"]
    assert messages[0] == {"role": "system", "content": template}
    assert messages[1] == {"role": "user", "content": "hello"}
