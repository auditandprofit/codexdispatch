class OpenAIError(Exception):
    pass


class _Responses:
    def create(self, **kwargs):
        raise RuntimeError("OpenAI stub does not support API calls")


class OpenAI:
    def __init__(self, *args, **kwargs):
        self.responses = _Responses()

