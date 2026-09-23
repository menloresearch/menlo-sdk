# AGENTS.md — how to work in menlo-sdk

Read this before changing anything. It is short on purpose.

## What this is

A Python SDK that drives an Asimov robot **through its edge**. It is a client of the
edge's arbiter and safety layer, never a bypass. The import is `menlo`, one subpackage per
robot: everything that speaks the Asimov wire lives in `menlo.asimov`; the top level holds
`__version__` and the `menlo` console script. One `Robot`; transports implement
`transport/base.py`. Nothing in `robot.py` may know which wire it is on.

## Rules

- **Verbs are the wire's verbs.** `set_velocity`, `stand`, `damp`, `stop`, `trajectory` —
  the names the edge, the protocol and the robot's other controllers already use. Do not
  invent synonyms.
- **No synchronous refusal.** A command returns a `Sent`; the verdict arrives as an
  `Outcome`. `Unknown` is never success and never refusal.
- **State is the truth about effect.** Waits read `robot.state`, never infer from what was
  sent. Every wait refuses to succeed on a stale stream, and a fault is reported before a
  predicate is evaluated.
- **Caller bugs are builtins** (`ValueError`, `KeyError`, `RuntimeError`); robot and link
  errors subclass `MenloError` and end in `Error`.
- **Nothing is guessed.** A field the robot does not report is `None`. A capability the
  transport does not carry raises `UnsupportedError`. A joint table carries its provenance
  (`robots.py`). A protocol version mismatch is `ProtocolMismatchError`, not a warning.
- **Describe what the code does.** No roadmap, no "yet", no review history in this repo.
  Behaviour that depends on the edge or firmware is stated as the fact it is.
- **Tests fail without the fix.** Every behavioural change ships a test that goes red when
  the change is reverted. The fake edge in `tests/conftest.py` speaks the real wire; if you
  change the wire, update the fake AND run `make integration` against the real connector.
- **Bindings are a dependency.** `asimov-protocol>=1.2,<2` from PyPI; `menlo.asimov._proto`
  imports it lazily. Never copy generated `_pb2` files into this repo.

## Dependencies

Runtime core: `protobuf` only — `pip install menlo-sdk` with no extra must drive a robot
(the UDP lane). The media lane adds one extra, `[livekit]`, and every `livekit` import in
the SDK is lazy and lives in `transport/_livekit_client.py`; nothing else may import it,
and `robot.connect("udp")` must never reach it. Optional at call time, never at import time:
numpy, Pillow and OpenCV are named in an error, never depended on.

Dev: ruff, mypy (strict), pytest. Simulator for live tests:
`menlo-studio up --container --sdk`. A LiveKit server for `make livekit`:
`livekit-server --dev`.
