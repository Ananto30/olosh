.PHONY: generate-protos
generate-protos:
# 	.venv/bin/python -m grpc_tools.protoc -I. --python_out=./agent --grpc_python_out=./agent --pyi_out=./agent ./protos/agent_service.proto
# 	.venv/bin/python -m grpc_tools.protoc -I. --python_out=./orchestrator --grpc_python_out=./orchestrator --pyi_out=./orchestrator ./protos/agent_service.proto
	cd src && ../.venv/bin/python -m grpc_tools.protoc -I. --python_out=./ --grpc_python_out=./ --pyi_out=./ ./protos/agent_service.proto


.PHONY: install
install:
	uv venv
	uv sync
	.venv/bin/python -m pip install -e .