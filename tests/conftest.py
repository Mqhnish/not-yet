import json


class FakeLLM:
    """Replays canned replies in order and records the prompts it saw."""

    label = "fake-gemma"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def complete(self, prompt, *, system=None, max_tokens=2048, temperature=0.2):
        self.prompts.append(prompt)
        r = self.replies.pop(0) if self.replies else "Rest."
        return json.dumps(r) if isinstance(r, dict) else r
