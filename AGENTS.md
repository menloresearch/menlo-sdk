# AGENTS.md — how to work in asimov-sdk

Read this before changing anything. It is short on purpose.

## What this is

A Python SDK that drives an Asimov robot **through its edge**. It is a client of the
edge's arbiter and safety layer, never a bypass. Two transports share one `Robot`:
the direct LAN lane (shipped) and the cloud lane (planned). Keep that split honest —
nothing in `robot.py` may know which wire it is on.

## Rules

- **Verbs are the wire's verbs.** `set_velocity`, `stand`, `damp`, `stop`, `trajectory`
  — the names the edge, the protocol and the other controllers already use. Do not invent
  synonyms (`walk`, `halt`, `estop` were tried and rejected).
- **No synchronous refusal.** A command returns a `Sent`; the verdict arrives as an
  `Outcome`. `Unknown` is never success and never refusal.
- **State is the truth about effect.** Waits read `robot.state`, never infer from what was
  sent. Every wait refuses to succeed on a stale stream.
- **Caller bugs are builtins** (`ValueError`, `KeyError`); robot/link errors subclass
  `AsimovError`.
- **Nothing is guessed.** A field the robot does not report is `None`. A joint table
  carries its provenance (`robots.py`). A protocol version mismatch is a `ProtocolMismatch`,
  not a warning.
- **Tests fail without the fix.** Every behavioural change ships a test that goes red when
  the change is reverted. The fake edge in `tests/conftest.py` speaks the real wire; if you
  change the wire, update the fake AND run `make integration` against the real connector.
- **No RSL in the public surface yet.** Signing lands with the cloud transport.

## Commands

```bash
make check         # lint + mypy --strict + unit tests — must be green before a PR
make integration   # ASIMOV_EDGE_SRC=<asimov-edge>/src
make live          # ASIMOV_SDK_LIVE_HOST=<robot or rig ip>
```

## Dependencies

Runtime: `asimov-protocol` only, by git URL at the tag the edge pins (bump together with
`tests/integration/edge.pin`). Dev: pytest, ruff, mypy. Nothing else without a reason in
the PR.

## Where things live upstream

- Wire: `menloresearch/asimov-protocol` (`v1/asimov_command.proto`, `asimov_state.proto`).
- Robot side: `menloresearch/asimov-edge` (`src/edge/connectors/udp_connector.py`, `arbiter/`).
- Simulator: `menloresearch/mono` → `packages/py/menlo-studio` (`up --container --sdk`).
