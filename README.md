<p align="center">
  <a href="https://menlo.ai"><img src="https://docs.menlo.ai/menlo-logo.svg" alt="Menlo" width="160"></a>
</p>

# menlo-sdk

Python SDK for Menlo robots. Supports Asimov 1.

## Requirements

- Python 3.12 or newer.
- Optional, only for the calls that use them: Pillow for `Frame.to_jpeg()`, NumPy for
  `Frame.to_numpy()`, OpenCV (`opencv-python`) for `Clip.save_mp4()`.

## Installation

```bash
pip install menlo-sdk
```

## Usage

Connect with the robot's address:

```python
from menlo.asimov import ConnectionConfig, Robot, UdpConfig

config = ConnectionConfig(udp=UdpConfig(host="192.168.22.32"))
```

Read the robot's state. This sends nothing:

```python
with Robot(config).connect() as robot:
    print(robot.get_state().mode.name)  # DAMP, STAND or MOVE
```

Put the robot in STAND: the actuators hold a standing pose, with no balancing. The robot
must hang from its gantry hook with both feet on the floor; from MOVE, hang it from its
gantry hook or seat it on a stool or bench first. `stand()` returns once the robot is in
STAND and armed:

```python
with Robot(config).connect() as robot:
    robot.stand()
```

Put the robot in MOVE: the walking policy balances it in place. `balance()` returns once the
robot reports MOVE:

```python
with Robot(config).connect() as robot:
    robot.balance()
```

Walk: in MOVE the robot follows the velocity you send, here 0.3 m/s forward for 3 s. A walk
needs 2 m of clear floor ahead, and Asimov Manager open at the E-Stop. `set_velocity()`
returns once the 3 s are over and zero velocity is sent. `balance()` keeps the robot in MOVE
at zero velocity, balancing in place:

```python
with Robot(config).connect() as robot:
    robot.set_velocity(vx=0.3, duration=3.0)
    robot.balance()
```

Put the robot in DAMP: every actuator stops holding its position, so a standing robot falls.
Only do this with the robot supported, hanging from its gantry hook or seated on a stool or
bench:

```python
with Robot(config).connect() as robot:
    robot.damp()
```

The SDK sends what you ask and reports what the robot says; it refuses a command only when
there is no live state (`NotReadyError` after the command's `timeout`, nothing sent; a
closed Robot raises `NotConnectedError`, a lost link `LinkLostError`). Safety is the firmware's job, and a guard is yours: read the facts
and decide before you send ([examples/guard.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/guard.py) is one to start from):

```python
with Robot(config).connect() as robot:
    s = robot.get_state()
    if s.faulted or any(j.temp is not None and j.temp >= 80 for j in s.joints):
        raise SystemExit("not walking: a latched fault or a hot actuator")
    robot.set_velocity(vx=0.3, duration=3.0)
```

## Agent Skill

The usage guide is also an agent skill, so a coding agent writes scripts the way this SDK
expects. In Claude Code:

```text
/plugin marketplace add menloresearch/menlo-sdk
/plugin install menlo-sdk@menlo
```

In Codex, pi and other agents that read Agent Skills:

```bash
npx skills add menloresearch/menlo-sdk
```

## Documentation

- [Python SDK on Asimov 1](https://docs.menlo.ai/asimov/1/program/sdk): installing the SDK and running the examples on Asimov 1.
- [Python SDK guide](https://docs.menlo.ai/sdk): the full documentation.
- [Safety](https://docs.menlo.ai/sdk/safety): what to check before a script moves the robot.
- [llms.txt](https://docs.menlo.ai/llms.txt): an index of the documentation for AI agents; [llms-full.txt](https://docs.menlo.ai/llms-full.txt) has every page in full.
- [Examples](https://github.com/menloresearch/menlo-sdk/tree/main/examples): demos of the SDK's features.
- [Changelog](https://github.com/menloresearch/menlo-sdk/blob/main/CHANGELOG.md): the changes in each release.

## Security

To report a vulnerability, see [SECURITY.md](https://github.com/menloresearch/menlo-sdk/blob/main/SECURITY.md).

## License

Apache License 2.0. See [LICENSE](https://github.com/menloresearch/menlo-sdk/blob/main/LICENSE).
