"""Record 3 s of robot state to a JSON-lines file, then read it back and summarise it.

Sends nothing. Connection mode: the saved robot's. Run: python examples/10_record_and_replay.py
"""

import time
from collections import Counter

from menlo.asimov import Robot
from menlo.asimov.recording import load

PATH = "run.jsonl"

# region record
with Robot().connect() as robot, robot.record(PATH) as recording:
    time.sleep(3.0)
print(f"recorded {recording.samples} states and {recording.commands_written} commands")
# endregion

# region replay
lines = list(load(PATH))
states = [line for line in lines if line["kind"] == "state"]
print("robot modes seen:", dict(Counter(s["mode"] for s in states)))
if len(states) > 1:
    rate = (len(states) - 1) / (states[-1]["t"] - states[0]["t"])
    print(f"state rate {rate:.0f} Hz")
# endregion
