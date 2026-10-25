.PHONY: setup lint fmt typecheck test test-integration test-integration-docker contract verify-contract transcript build test-all

PY ?= python
RUNTIME_IMAGE ?= ghcr.io/multiagentcoordinationprotocol/macp-runtime:0.9.0
RUNTIME_PORT ?= 50051

setup:
	$(PY) -m pip install -e ".[dev]"

lint:
	ruff check src/ tests/ examples/
	ruff format --check src/ tests/ examples/

fmt:
	ruff format src/ tests/ examples/

typecheck:
	mypy src/macp_mcp/

# Unit tests + coverage gate (config in pyproject [tool.coverage]).
test:
	pytest tests/unit/ -v --cov

# Needs a runtime at MACP_RUNTIME_TARGET (default 127.0.0.1:50051); skips if none.
test-integration:
	pytest tests/integration/ -m integration --no-cov -v -rs

# CI's integration gate locally: start the pinned runtime image, run, clean up.
test-integration-docker:
	docker run -d --rm --name macp-mcp-it -p $(RUNTIME_PORT):50051 \
		-e MACP_ALLOW_INSECURE=1 -e MACP_MEMORY_ONLY=1 -e MACP_BIND_ADDR=0.0.0.0:50051 \
		$(RUNTIME_IMAGE)
	@for i in $$(seq 1 30); do nc -z localhost $(RUNTIME_PORT) && break; sleep 1; done
	MACP_RUNTIME_TARGET=127.0.0.1:$(RUNTIME_PORT) $(MAKE) test-integration; \
		status=$$?; docker rm -f macp-mcp-it >/dev/null; exit $$status

# Regenerate the cross-language tool contract after an intentional surface
# change, then review the diff. The TypeScript twin vendors this file.
contract:
	$(PY) -m macp_mcp._contract > contract/tools.json
	$(PY) -m macp_mcp.gate._contract > contract/gate/context-id-vectors.json

# CI drift gate: the advertised surface must equal contract/tools.json.
verify-contract:
	pytest tests/unit/test_contract.py tests/unit/test_gate_contract.py -v --no-cov

# Re-record examples/decision-via-mcp.md against a running runtime.
transcript:
	$(PY) examples/decision_via_mcp.py --out examples/decision-via-mcp.md

build:
	rm -rf dist/
	$(PY) -m build
	twine check dist/*

test-all: lint typecheck test test-integration
