.PHONY: install test lint check-bedrock demo eval record api serve

install:
	python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

test:
	LLM_PROVIDER=fake pytest -q

lint:
	ruff check src tests --fix

check-bedrock:            ## Names the piece that broke, before anyone writes agent code
	python scripts/check_bedrock.py

demo:                     ## Full run on a seeded bug, replayed, zero cost
	LLM_PROVIDER=fake python -m repro.cli run --repo fixtures/demo_repos/shopcart \
		--report "$$(sed -n 's/.*Client wrote:\*\* //p' fixtures/BUGS.md | head -1)"

record:                   ## Re-record cassettes against real Bedrock (COSTS MONEY)
	REPRO_RECORD=1 LLM_PROVIDER=bedrock python -m repro.cli run --repo fixtures/demo_repos/shopcart

eval:
	LLM_PROVIDER=fake python eval/run_eval.py --dataset eval/dataset.yaml

api:
	uvicorn repro.api.main:app --reload --port 8000

serve:                    ## AgentCore contract, locally, free
	python src/repro/agentcore/agent.py
