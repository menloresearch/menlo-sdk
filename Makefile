# menlo-sdk: developer entry points. `uv` is the only tool assumed.
.PHONY: sync lint fmt typecheck test integration live livekit check

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

integration:     ## Asimov Edge's real UdpConnector in-process; needs ASIMOV_EDGE_SRC=<edge>/src
	uv run pytest -m integration

live:            ## a robot; needs MENLO_SDK_LIVE_HOST
	uv run pytest -m live -s

livekit:         ## real livekit.rtc vs `livekit-server --dev`. Needs MENLO_SDK_LIVEKIT_URL and
                 ## TWO tokens for one room (an identity is a claim inside the JWT, so one
                 ## token is one participant): MENLO_SDK_LIVEKIT_TOKEN (identity `sdk`) and
                 ## MENLO_SDK_LIVEKIT_EDGE_TOKEN (identity `fake-edge`). See the test module.
	uv run pytest -m livekit -s



check: lint typecheck test
