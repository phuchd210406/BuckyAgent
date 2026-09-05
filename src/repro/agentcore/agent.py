"""
Bedrock AgentCore entrypoint. OWNER: Engineer A.

Session 2, LAB 06: this file is the graph plus four lines. Nothing about the
graph changes. Run it locally first (POST http://localhost:8080/invocations)
and only deploy once, for the recording.
"""
from __future__ import annotations

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # type: ignore

app = BedrockAgentCoreApp()


@app.entrypoint
def handler(payload: dict) -> dict:
    from repro.graph.build import build_graph
    from repro.llm.bedrock import BedrockLLM

    graph = build_graph(BedrockLLM())
    result = graph.invoke(
        {"report": payload["report"], "clarify_rounds": 0, "repro_count": 0, "fix_count": 0}
    )
    return {"verdict": result["verdict"], "handover": result.get("handover")}


if __name__ == "__main__":
    app.run()
