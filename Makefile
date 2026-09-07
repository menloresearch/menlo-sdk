# asimov-sdk — developer entry points. `uv` is the only tool assumed.
.PHONY: sync lint fmt typecheck test integration live check

sync:            ## create/refresh .venv from uv.lock
	uv sync --all-groups

lint:            ## ruff, the way CI runs it
	uv run ruff check . && uv run ruff format --check .

fmt:             ## fix what ruff can fix
	uv run ruff check --fix . && uv run ruff format .

typecheck:
	uv run mypy

test:            ## unit tests only (no robot, no edge checkout)
	uv run pytest -m "not integration and not live"

integration:     ## the real asimov-edge UdpConnector in-process; needs ASIMOV_EDGE_SRC=<edge>/src
	uv run pytest -m integration

live:            ## a robot or `menlo-studio up --container --sdk`; needs ASIMOV_SDK_LIVE_HOST
	uv run pytest -m live -s

vendor-protocol: ## re-vendor the generated bindings: make vendor-protocol REF=v1.1.0
	scripts/vendor_protocol.sh $(REF)

check: lint typecheck test
