.PHONY: generate-protos
generate-protos:
	.venv/bin/python -m grpc_tools.protoc -I. --python_out=./agent --grpc_python_out=./agent --pyi_out=./agent ./protos/agent_service.proto
	.venv/bin/python -m grpc_tools.protoc -I. --python_out=./orchestrator --grpc_python_out=./orchestrator --pyi_out=./orchestrator ./protos/agent_service.proto


.PHONY: orchestrator
orchestrator:
	cd orchestrator && ../.venv/bin/python server.py

.PHONY: agent
agent:
	cd agent && ../.venv/bin/python agent.py