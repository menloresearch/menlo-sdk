# menlo-sdk — developer entry points. `uv` is the only tool assumed.
.PHONY: sync lint fmt typecheck test integration live livekit check vendor-protocol check-vendor

sync:            ## create/refresh .venv from uv.lock
	uv sync --all-groups

lint:            ## ruff, the way CI runs it
	uv run ruff check . && uv run ruff format --check .

fmt:             ## fix what ruff can fix
	uv run ruff check --fix . && uv run ruff format .

typecheck:
	uv run mypy

test:            ## unit tests only (no robot, no edge checkout, no livekit)
	uv run pytest -m "not integration and not live and not livekit"

integration:     ## the real asimov-edge UdpConnector in-process; needs ASIMOV_EDGE_SRC=<edge>/src
	uv run pytest -m integration

live:            ## a robot or `menlo-studio up --container --sdk`; needs ASIMOV_SDK_LIVE_HOST
	uv run pytest -m live -s

livekit:         ## real livekit.rtc vs `livekit-server --dev`. Needs ASIMOV_SDK_LIVEKIT_URL and
                 ## TWO tokens for one room (an identity is a claim inside the JWT, so one
                 ## token is one participant): ASIMOV_SDK_LIVEKIT_TOKEN (identity `sdk`) and
                 ## ASIMOV_SDK_LIVEKIT_EDGE_TOKEN (identity `fake-edge`). See the test module.
	uv run --extra livekit pytest -m livekit -s

vendor-protocol: ## re-vendor the generated bindings: make vendor-protocol REF=v1.1.0
	scripts/vendor_protocol.sh $(REF)

check-vendor:    ## fail if the vendored bindings drift from the tag pinned in _vendor/VENDORED.md
	scripts/vendor_protocol.sh --check

check: lint typecheck test
