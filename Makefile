.PHONY: install test test-fast lint lint-fix check-bedrock demo eval record api serve

# --- how these recipes find Python -----------------------------------------
# Recipes run under /bin/sh with whatever PATH you happen to have. Two traps
# this has already sprung:
#   * on Ubuntu there is no `python`, only `python3`, so a bare `python` fails
#     with `/bin/sh: 1: python: not found`;
#   * if you have not activated the venv, a bare `python` or `pytest` silently
#     runs the SYSTEM interpreter, which has none of our dependencies.
# So resolve the interpreter ONCE, preferring the venv, and invoke every tool as
# `$(PY) -m <tool>` instead of trusting PATH.
PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

# `repro` is NOT pip-installed into .venv, so src/ must be on the path for
# `python -m repro...`, uvicorn and eval/run_eval.py. Pinning it here also stops
# an inherited PYTHONPATH from leaking in: with a ROS 2 workspace sourced, its
# pytest plugins crash pytest with `unknown hook
# 'pytest_launch_collect_makemodule'` before a single test is collected.
export PYTHONPATH := src

install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test:                     ## The whole suite, including the wall-clock waits
	LLM_PROVIDER=fake $(PY) -m pytest

test-fast:                ## The inner loop: everything except the wall-clock waits
	LLM_PROVIDER=fake $(PY) -m pytest -m "not slow"

lint:                     ## EXACTLY what CI runs. Read-only: never edits your tree
	$(PY) -m ruff check src tests

lint-fix:                 ## The same rules, applying the safe fixes
	$(PY) -m ruff check src tests --fix

check-bedrock:            ## Names the piece that broke, before anyone writes agent code
	$(PY) scripts/check_bedrock.py

demo:                     ## Full run on a seeded bug, replayed, zero cost
	LLM_PROVIDER=fake $(PY) -m repro.cli run --repo fixtures/demo_repos/shopcart \
		--report "$$($(PY) scripts/demo_report.py)"

record:                   ## Re-record cassettes against real Bedrock (COSTS MONEY)
	REPRO_RECORD=1 LLM_PROVIDER=bedrock $(PY) scripts/record_cassettes.py

eval:
	LLM_PROVIDER=fake $(PY) eval/run_eval.py --dataset eval/dataset.yaml

api:
	$(PY) -m uvicorn repro.api.main:app --reload --port 8000

serve:                    ## AgentCore contract, locally, free
	$(PY) src/repro/agentcore/agent.py
