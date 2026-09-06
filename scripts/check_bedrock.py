#!/usr/bin/env python3
"""One command that names the piece that broke. Run this BEFORE writing agent code.

    make check-bedrock          # or: python scripts/check_bedrock.py

Checks, in order, the four things that actually go wrong:
  1. config — which .env was loaded, which model id, which regions we will try
  2. credentials — do they resolve, from WHERE, and who does AWS think you are
  3. the model is invocable IN A REGION, through the same `converse` call the
     agents use, so a pass here means the agents work
  4. the client seam the agents import (`repro.llm.bedrock`), once it exists

Region is the classic failure: the SSO region and the Bedrock client region are
different settings, and the two decks disagree about which region has model
access (Session 1 says ap-southeast-1, the AWS deck says us-east-1). This
settles it empirically in about five seconds and tells you what to put in .env.

Two behaviours worth knowing, both learned the hard way:

* **It loads .env.** DEPLOY.md and .env.example both tell you to put the three
  sandbox keys in .env, but nothing else in this codebase reads that file, so
  boto3 never saw them: a correctly-filled .env still produced
  NoCredentialsError. Anything you `export` still wins, because the 12-hour key
  routine in DEPLOY.md is to re-export fresh keys and those must beat a stale file.

* **A failing STS call is not fatal.** A sandbox account can deny
  sts:GetCallerIdentity while Bedrock works perfectly. Only step 3 decides.

Exit code 0 means every other agent call will work. Non-zero names the step.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_REGIONS = ["us-east-1", "ap-southeast-1"]

# Claude 4.5 has NO on-demand base model, so an inference profile id is
# mandatory, and which profiles you may invoke is an IAM question, not a model
# access question. When the configured id is refused we probe these, in order,
# and name the one that answers. Literal ids only — never assembled at runtime.
CANDIDATE_MODEL_IDS = [
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "apac.anthropic.claude-haiku-4-5-20251001-v1:0",
    "anthropic.claude-haiku-4-5-20251001-v1:0",
]

# Sandbox creds are temporary, so these codes mean the keys are the problem,
# which is a different fix from "no model access".
EXPIRED_CODES = {"ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId"}

# Same AWS error code, two very different mistakes. Say both, in this order:
# expiry is the likelier one on a 12-hour sandbox key.
STALE_KEY_ADVICE = (
    "          Either the session expired (sandbox keys last 12h), or the three\n"
    "          values do not belong to the same session. Copy ALL THREE together,\n"
    "          in one go, from the access portal -> Access keys, and replace them\n"
    "          together in .env — a token from an older paste fails exactly like this."
)


def bootstrap() -> str:
    """Make `repro` importable and `.env` effective regardless of the cwd.

    Returns a one-line description of where configuration came from. Run from
    scripts/, from the repo root, or from anywhere via an absolute path.
    """
    src = REPO_ROOT / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return f"no {env_path.name} at {REPO_ROOT} (relying on exported vars only)"
    try:
        from dotenv import load_dotenv
    except ImportError:
        return f"{env_path} NOT loaded: python-dotenv missing (pip install -r requirements.txt)"

    # override=False: an exported key beats the file, per the 12h re-export routine.
    load_dotenv(env_path, override=False)
    # dotenv turns `AWS_ACCESS_KEY_ID=` into an empty string, and a half-empty
    # credential triple produces a far more confusing botocore error than no
    # credentials at all. Treat blank as absent.
    blanked = [k for k, v in list(os.environ.items()) if k.startswith("AWS_") and not v.strip()]
    for key in blanked:
        del os.environ[key]
    note = f" (ignored {len(blanked)} blank AWS_* entr{'y' if len(blanked) == 1 else 'ies'})"
    return f"{env_path} loaded{note if blanked else ''}"


def regions_to_try(explicit: list[str]) -> list[str]:
    """Explicit --region wins; otherwise the configured region first, then the rest."""
    if explicit:
        return list(dict.fromkeys(explicit))
    configured = os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION")
    ordered = ([configured] if configured else []) + CANDIDATE_REGIONS
    return list(dict.fromkeys(r for r in ordered if r))


def probe(region: str, model: str, *, quiet: bool = False) -> tuple[bool, dict, str]:
    """Try one `converse` call. Returns (worked, usage, error_code).

    Prints AWS's OWN message, not a paraphrase. That message is the difference
    between "enable model access" and "your role may not invoke this profile",
    and the old version of this script discarded it.
    """
    import boto3
    from botocore.exceptions import ClientError, EndpointConnectionError

    try:
        client = boto3.client("bedrock-runtime", region_name=region)
        resp = client.converse(
            modelId=model,
            messages=[{"role": "user", "content": [{"text": "reply with the word ready"}]}],
            inferenceConfig={"temperature": 0, "maxTokens": 8},
        )
    except EndpointConnectionError:
        if not quiet:
            print(f"---  3/4  {region}: cannot reach the endpoint — network/proxy, not Bedrock")
        return False, {}, "EndpointConnectionError"
    except ClientError as exc:
        err = exc.response.get("Error", {})
        code, message = err.get("Code", "?"), err.get("Message", "")
        if not quiet:
            hint = {
                "AccessDeniedException": "credentials are fine; this ROLE/account may not "
                                     "invoke this id — read the message below, an "
                                     "'explicit deny in a service control policy' "
                                     "is a guardrail no console toggle overrides",
                "ValidationException": "wrong id for this region, or the id needs "
                                       "an inference profile",
                "ResourceNotFoundException": "no such model in this region",
                "ThrottlingException": "throttled — retry shortly, this is not a config error",
            }.get(code, "")
            if code in EXPIRED_CODES:
                hint = "credentials rejected — expired, or values from different sessions"
            print(f"---  3/4  {region}: {code}{' — ' + hint if hint else ''}")
            if message:
                print(f"          AWS says: {message[:500]}")
        return False, {}, code

    said = resp["output"]["message"]["content"][0]["text"].strip()
    usage = resp.get("usage", {})
    if not quiet:
        print(f"OK   3/4  {region}: model replied {said!r} "
              f"(in={usage.get('inputTokens', 0)} out={usage.get('outputTokens', 0)})")
    return True, usage, ""


def find_working_id(regions: list[str], tried: str) -> tuple[str, str] | None:
    """Probe the known id forms and return the first (region, id) that answers.

    Refused calls are not billed, and a successful one costs about 16 tokens, so
    this is effectively free. It exists so that "AccessDeniedException" becomes
    "put THIS in .env" without anyone having to read the Bedrock docs.
    """
    print("\n          Searching the other known id forms...")
    for region in regions:
        for model in CANDIDATE_MODEL_IDS:
            if model == tried:
                continue
            ok, _, code = probe(region, model, quiet=True)
            status = "WORKS" if ok else code
            print(f"          {status:26} {region}  {model}")
            if ok:
                return region, model
    return None


def check_agent_seam() -> None:
    """Step 4, advisory: the class the agents import must exist and look right.

    Never fatal. It is legitimately absent until Engineer C lands it, and this
    script has to stay useful before that.
    """
    try:
        from repro.llm.bedrock import BedrockLLM
    except ImportError as exc:
        print(f"---  4/4  repro.llm.bedrock not usable yet ({exc.__class__.__name__}: {exc})")
        print("          Expected while it is unwritten. Steps 1-3 are what gate the agents.")
        return
    missing = [m for m in ("complete", "structured") if not hasattr(BedrockLLM, m)]
    if missing:
        print(f"---  4/4  BedrockLLM is missing {', '.join(missing)} — it must satisfy LLMClient")
        return
    print("OK   4/4  repro.llm.bedrock imports and satisfies the LLMClient protocol")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bedrock preflight: credentials, region, model access.")
    ap.add_argument("--region", action="append", default=[],
                    help="Probe only this region. Repeatable. Default: configured, then both candidates.")
    ap.add_argument("--model", default=None, help="Override the model id (e.g. one copied from the console).")
    ap.add_argument("--all-regions", action="store_true",
                    help="Probe every candidate region instead of stopping at the first that works.")
    args = ap.parse_args(argv)

    config_note = bootstrap()

    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError
    except ImportError:
        print("FAIL 1/4  boto3 not installed — pip install -r requirements.txt")
        return 1

    # Model ids are constants in settings.py and are never built by hand.
    try:
        from repro.settings import HAIKU, usd_for
    except ImportError:
        HAIKU = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
        usd_for = None  # type: ignore[assignment]
    model = args.model or os.getenv("REPRO_MODEL_ID") or HAIKU
    regions = regions_to_try(args.region)

    print(f"OK   1/4  config: {config_note}")
    print(f"          model  {model}")
    print(f"          regions {', '.join(regions)}")

    session = boto3.Session()
    creds = session.get_credentials()
    if creds is None:
        print("FAIL 2/4  no credentials found by boto3.")
        print("          Sandbox keys expire every 12h. Paste the three export lines")
        print("          from the AWS access portal -> Access keys, or fill in .env:")
        print("            AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN")
        return 1
    print(f"OK   2/4  credentials resolve (source: {creds.method})")
    # Shape check before we spend a call. Prefix only — never log key material.
    prefix, has_token = (creds.access_key or "")[:4], bool(creds.token)
    if prefix == "ASIA" and not has_token:
        print("FAIL 2/4  temporary key (ASIA...) with no AWS_SESSION_TOKEN.")
        print("          All three values are required. Paste the session token too.")
        return 1
    if prefix == "AKIA" and has_token:
        print("          (long-term key (AKIA...) sent with a session token — unusual;")
        print("           if AWS rejects it, clear AWS_SESSION_TOKEN)")
    try:
        ident = boto3.client("sts").get_caller_identity()
        print(f"          caller {ident.get('Arn', '?')}")
    except (ClientError, NoCredentialsError) as exc:
        code = exc.response.get("Error", {}).get("Code", "?") if hasattr(exc, "response") else "?"
        if code in EXPIRED_CODES:
            print(f"FAIL 2/4  AWS rejected these credentials ({code}).")
            print(STALE_KEY_ADVICE)
            return 1
        print(f"          (STS says {code} — not fatal, only step 3 decides)")

    working: list[str] = []
    spend = 0.0
    for i, region in enumerate(regions):
        ok, usage, _ = probe(region, model)
        if ok:
            working.append(region)
            if usd_for is not None:
                spend += usd_for(model, usage.get("inputTokens", 0), usage.get("outputTokens", 0))
            # One working region is all the agents need. Probing the rest only
            # prints alarming lines about regions we have already ruled out —
            # ask for --all-regions when you actually want the full picture.
            if not args.all_regions and i + 1 < len(regions):
                skipped = ", ".join(regions[i + 1:])
                print(f"          (not tried: {skipped} — pass --all-regions to include them)")
            if not args.all_regions:
                break

    if not working:
        print(f"FAIL 3/4  {model} does not work in any region tried.")
        found = find_working_id(regions, model)
        if found is None:
            print("\n          Nothing worked. Two different causes, in likelihood order:")
            print("          1. this role lacks bedrock:InvokeModel — ask the organisers;")
            print("             an AccessDenied naming an inference profile ARN is IAM,")
            print("             NOT the console's Model access page.")
            print("          2. model access genuinely not granted: Bedrock console ->")
            print("             Model access -> enable Claude Haiku 4.5, then re-run.")
            return 1
        good_region, good_model = found
        print("\nFIX IT   use this pair — it answered just now:")
        print(f"           AWS_DEFAULT_REGION={good_region}")
        print(f"           REPRO_MODEL_ID={good_model}")
        print("         Put both in .env, and put the id in src/repro/settings.py so the")
        print("         constant matches. Then re-run this check.")
        return 1

    check_agent_seam()
    print(f"\nOK — put AWS_DEFAULT_REGION={working[0]} in .env and post it in the team channel.")
    print(f"     This check cost about ${spend:.6f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
