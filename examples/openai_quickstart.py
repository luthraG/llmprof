"""Profile an OpenAI-compatible call by pointing the client at the proxy.

The only change from a normal script is `base_url`. Your API key passes straight
through to the upstream; llmprof tokenizes, attributes, prices, and flame-graphs
the call off the hot path. Open http://localhost:4000 to see it.

Run:
    pip install openai
    llmprof up                       # in another terminal
    export OPENAI_API_KEY=sk-...
    python examples/openai_quickstart.py
"""

import os

from openai import OpenAI

# Point at the proxy instead of api.openai.com. Nothing else changes.
client = OpenAI(
    base_url=os.environ.get("LLMPROF_URL", "http://localhost:4000") + "/v1",
    api_key=os.environ["OPENAI_API_KEY"],
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": "You are a terse assistant."},
        {"role": "user", "content": "In one sentence, what is a flame graph?"},
    ],
)

print(resp.choices[0].message.content)
print("\nProfiled. Open http://localhost:4000 for the flame graph and cost.")
