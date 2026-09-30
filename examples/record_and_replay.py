"""Record a few seconds of robot state to a JSON-lines file, then read it back.

Sends nothing. Connects to the saved robot (`menlo setup`), or to the robot the MENLO_*
environment variables describe.
Run: python examples/record_and_replay.py
"""

import time
from collections import Counter

from menlo.asimov import Robot
from menlo.asimov.recording import load

PATH = "run.jsonl"
SECONDS = 3.0

# region main
# Every state sample and every command sent while the block runs goes to PATH.
with Robot().connect() as robot, robot.record(PATH) as recording:
    time.sleep(SECONDS)
print(f"recorded {recording.samples} states and {recording.commands_written} commands")

# Each line is one JSON object; "kind" says whether it is a state or a command.
lines = list(load(PATH))
states = [line for line in lines if line["kind"] == "state"]
print("robot modes seen:", dict(Counter(s["mode"] for s in states)))
if len(states) > 1:
    rate = (len(states) - 1) / (states[-1]["t"] - states[0]["t"])
    print(f"state rate {rate:.0f} Hz")
# endregion
