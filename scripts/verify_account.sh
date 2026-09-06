#!/usr/bin/env bash
# Which AWS account is this shell about to bill? Run it before any agentcore command.
#
# There are two identities on this machine and they are easy to confuse:
#   715615725424  the hackathon sandbox lease (the $20 budget, the one we use)
#   497902501735  an SSO "workshop" profile with AdministratorAccess (not ours)
#
# A deployment to the wrong one does not fail. It succeeds, bills an account
# nobody is watching, and survives the hackathon because `agentcore destroy`
# looks in whichever account the shell points at.
#
#   bash scripts/verify_account.sh            # expects the sandbox
#   REPRO_AWS_ACCOUNT=<id> bash scripts/verify_account.sh
set -uo pipefail

EXPECTED="${REPRO_AWS_ACCOUNT:-715615725424}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
status=0

echo "expected account   $EXPECTED"

# Deliberately NOT loading .env: the agentcore CLI does not read it either, so
# this has to report what agentcore will actually use, not what our own scripts
# would resolve after python-dotenv has run.
if [ -n "${AWS_PROFILE:-}" ]; then
  echo "AWS_PROFILE        $AWS_PROFILE"
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ]; then
    echo "  NOTE: AWS_ACCESS_KEY_ID is also set and WINS over AWS_PROFILE."
  fi
fi

identity="$(aws sts get-caller-identity --query '[Account,Arn]' --output text 2>&1)"
if [ $? -ne 0 ]; then
  # Three different failures, three different fixes. Saying "no credentials"
  # for all of them sends you to reload a .env whose keys expired hours ago.
  echo "FAIL  no usable credentials in this shell."
  case "$identity" in
    *ExpiredToken*|*expired*)
      echo "      The credentials exist but have EXPIRED."
      echo "      Sandbox keys last 12h. Re-paste all three together from the AWS"
      echo "      access portal -> Access keys into .env, then: set -a; . ./.env; set +a" ;;
    *LoginTokenLoadError*|*login\ session*|*reauthenticate*)
      echo "      An 'aws login' session is the default identity here and it has lapsed."
      echo "      That is the OTHER account. If its teardown is finished, clear it for good:"
      echo "          aws logout && rm -rf ~/.aws/login/cache" ;;
    *)
      echo "      ${identity}" | head -2 ;;
  esac

  # Say whether .env would even help, rather than suggesting it blindly.
  if [ -f "$ROOT/.env" ]; then
    env_id="$(bash -c "set -a; . '$ROOT/.env'; set +a; aws sts get-caller-identity --query Account --output text" 2>&1 | tail -1)"
    case "$env_id" in
      "$EXPECTED") echo "      .env has working keys for $EXPECTED: set -a; . ./.env; set +a" ;;
      *Expired*|*expired*) echo "      .env will NOT help: its keys are expired too. Re-paste them first." ;;
      *) echo "      .env resolves to: $env_id" ;;
    esac
  fi
  exit 1
fi

account="$(echo "$identity" | awk '{print $1}')"
arn="$(echo "$identity" | awk '{print $2}')"
echo "this shell is       $account"
echo "                    $arn"

if [ "$account" != "$EXPECTED" ]; then
  echo "FAIL  wrong account. Anything you deploy now bills $account."
  echo "      Clear the other identity first:  unset AWS_PROFILE AWS_ACCESS_KEY_ID \\"
  echo "                                             AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN"
  echo "      Then load the sandbox keys:      set -a; . ./.env; set +a"
  status=1
else
  echo "OK    shell points at the intended account"
fi

# The deployment config outlives the shell, so it gets checked separately: a
# stale one sends `agentcore destroy` looking in an account that has nothing.
config="$ROOT/.bedrock_agentcore.yaml"
if [ ! -f "$config" ]; then
  echo "OK    no .bedrock_agentcore.yaml — nothing is deployed from this checkout"
else
  found="$(grep -oE '[0-9]{12}' "$config" | sort -u | tr '\n' ' ')"
  echo "config references   ${found:-none}"
  for id in $found; do
    if [ "$id" != "$EXPECTED" ]; then
      echo "FAIL  .bedrock_agentcore.yaml still points at $id."
      echo "      Destroy there first (that account's credentials), then delete the"
      echo "      config: rm .bedrock_agentcore.yaml && rm -rf .bedrock_agentcore/"
      status=1
    fi
  done
  [ $status -eq 0 ] && echo "OK    config references only the intended account"
fi

exit $status
