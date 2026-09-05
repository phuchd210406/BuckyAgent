"""One command that names the piece that broke. Run this BEFORE writing agent code.

Mirrors Session 1's 00_check_bedrock.py. Checks, in order:
  1. credentials resolve at all
  2. who the caller is
  3. the model is invocable IN THIS REGION

Region is the classic failure: the SSO region and the Bedrock client region are
different settings, and the two decks disagree about which region has model
access. This script settles it empirically in about five seconds.
"""
from __future__ import annotations

import os
import sys

CANDIDATE_REGIONS = ["us-east-1", "ap-southeast-1"]
MODEL = os.getenv("REPRO_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")


def main() -> int:
    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError
    except ImportError:
        print("FAIL 0/3  boto3 not installed — pip install -r requirements.txt")
        return 1

    try:
        ident = boto3.client("sts").get_caller_identity()
        print(f"OK   1/3  credentials resolve: {ident['Arn'].split('/')[-1]}")
    except (NoCredentialsError, ClientError) as exc:
        print(f"FAIL 1/3  no usable credentials ({exc.__class__.__name__}).")
        print("          Sandbox keys expire every 12h. Re-copy the three export")
        print("          lines from the AWS access portal -> Access keys.")
        return 1

    working = []
    for region in CANDIDATE_REGIONS:
        try:
            client = boto3.client("bedrock-runtime", region_name=region)
            resp = client.converse(
                modelId=MODEL,
                messages=[{"role": "user", "content": [{"text": "reply with the word ready"}]}],
                inferenceConfig={"temperature": 0, "maxTokens": 8},
            )
            said = resp["output"]["message"]["content"][0]["text"].strip()
            u = resp["usage"]
            print(f"OK   2/3  {region}: model replied {said!r} "
                  f"(in={u['inputTokens']} out={u['outputTokens']})")
            working.append(region)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "?")
            print(f"---  2/3  {region}: {code} — no model access here")

    if not working:
        print("FAIL 3/3  no region works. Bedrock -> Model access -> enable Claude Haiku 4.5.")
        return 1

    print(f"OK   3/3  put AWS_DEFAULT_REGION={working[0]} in .env and tell the team.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
