"""Profile an Anthropic call through the same proxy.

One proxy handles both providers. For Anthropic, set the base URL with no `/v1`
suffix; llmprof auto-routes `/v1/messages` and reads Anthropic's cache-aware
usage (input tokens exclude cached, so the prompt total adds them back in).

Run:
    pip install anthropic
    llmprof up                       # in another terminal
    export ANTHROPIC_API_KEY=sk-ant-...
    python examples/anthropic_quickstart.py
"""

import os

from anthropic import Anthropic

# Anthropic base URL: the proxy root, no /v1 (it auto-routes /v1/messages).
client = Anthropic(
    base_url=os.environ.get("LLMPROF_URL", "http://localhost:4000"),
    api_key=os.environ["ANTHROPIC_API_KEY"],
)

resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=128,
    system="You are a terse assistant.",
    messages=[{"role": "user", "content": "In one sentence, what is a flame graph?"}],
)

print(resp.content[0].text)
print("\nProfiled. Open http://localhost:4000 for the flame graph and cost.")
