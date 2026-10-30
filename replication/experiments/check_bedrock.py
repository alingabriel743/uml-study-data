"""5-token Bedrock check using the same credential loading as the runner (.env → env → Keychain).

  python -m experiments.check_bedrock [--model-id us.anthropic.claude-sonnet-4-6]
"""

import argparse
import json
import os
import urllib.error
import urllib.request

from experiments.run_experiment import _load_bedrock_credentials


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-id", default="us.anthropic.claude-sonnet-4-6")
    args = ap.parse_args()
    _load_bedrock_credentials(args.model_id)
    region = os.environ.get("APP_AWS_REGION", "us-east-1")
    body = json.dumps({"messages": [{"role": "user", "content": [{"text": "Say OK"}]}],
                       "inferenceConfig": {"maxTokens": 5}}).encode()
    req = urllib.request.Request(
        f"https://bedrock-runtime.{region}.amazonaws.com/model/{args.model_id}/converse", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ['AWS_BEARER_TOKEN_BEDROCK']}"})
    try:
        out = json.load(urllib.request.urlopen(req, timeout=60))
        print(f"OK {args.model_id} @ {region}: {out['output']['message']['content'][0]['text']!r} usage={out.get('usage')}")
    except urllib.error.HTTPError as e:
        print(f"FAILED {args.model_id} @ {region}: HTTP {e.code} {e.read().decode()[:200]}")


if __name__ == "__main__":
    main()
