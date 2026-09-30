<p align="center">
  <a href="https://menlo.ai"><img src="https://docs.menlo.ai/menlo-logo.svg" alt="Menlo" width="160"></a>
</p>

# menlo-sdk

Python SDK for Menlo robots: Asimov 1.

You drive the robot with one `Robot` class and the robot's own verbs: `stand`, `set_velocity`, `stop`, `damp`, `goto` and `trajectory`.

## Installation

```bash
pip install menlo-sdk
# or, with uv
uv add menlo-sdk
```

## Quickstart

Stand the robot up, walk forward for 3 s, and stop. The robot must be on its feet, hanging from its gantry hook, with 2 m of clear floor ahead.

```python
from menlo.asimov import ConnectionConfig, Mode, Robot, UdpConfig

config = ConnectionConfig(udp=UdpConfig(host="192.168.22.32"))  # the robot's address

with Robot(config).connect("udp") as robot:
    if robot.state.mode is Mode.DAMP:
        robot.wait_ready("stand")  # fresh state, no fault, battery and actuators ok
        robot.stand()
        robot.wait_for(Mode.STAND)
    robot.wait_ready("move")  # armed: STAND held upright for 0.5 s
    robot.set_velocity(vx=0.3, duration=3.0, wait=True)
    robot.stop()  # zero velocity: the robot stays in MOVE and balances in place
```

To keep the address out of the code, set `MENLO_UDP_HOST=192.168.22.32` and use `Robot().connect()`.

The udp connection mode needs two Asimov Edge parameters, set in Asimov Manager: `udp-control` on, and `udp-state-host` set to your computer's address.

## Examples

The [examples](https://github.com/menloresearch/menlo-sdk/tree/main/examples) are short scripts, one purpose each. Settings are constants at the top of each file. Every script that moves the robot checks it first, with `require_ready()` from `check.py`.

```bash
git clone https://github.com/menloresearch/menlo-sdk
cd menlo-sdk
export MENLO_UDP_HOST=192.168.22.32   # or save the robot with `menlo setup`
python examples/check.py              # is the robot ready? sends nothing
python examples/stand.py              # DAMP to STAND, then wait until armed
python examples/walk.py               # walk forward for 3 s, then stop
python examples/damp.py               # asks first; the robot must be supported
```

- [check.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/check.py): readiness to stand, walk or run a trajectory.
- [connect.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/connect.py): the three connection modes side by side.
- [read_state.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/read_state.py): a tour of the robot's state.
- [stand.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/stand.py), [walk.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/walk.py), [damp.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/damp.py): one verb each.
- [keyboard.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/keyboard.py): drive from the keyboard in short steps.
- [move_joints.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/move_joints.py): bend an elbow with `goto()`.
- [camera_and_audio.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/camera_and_audio.py): a photo, a clip and a tone.
- [record_and_replay.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/record_and_replay.py): record state to a file and read it back.
- [livekit_raw/](https://github.com/menloresearch/menlo-sdk/tree/main/examples/livekit_raw): the robot's LiveKit room without the SDK.
- [apps/](https://github.com/menloresearch/menlo-sdk/tree/main/examples/apps): follow a ball, and tools for an agent.

The [examples page](https://docs.menlo.ai/guides/python-sdk/examples) walks through each one.

## Safety

Read the [safety page](https://docs.menlo.ai/guides/python-sdk/safety) before you run a script on a robot.

- Nothing in the SDK is an emergency stop. Use the E-Stop in Asimov Manager, or cut power at the battery unit.
- Call `stand()` only from DAMP. End a walk with `stop()`: the robot stays in MOVE and keeps balancing.
- Keep the robot supported, hanging from its gantry hook or seated on a bench, for `damp()` and for joint control (`goto`, `trajectory`).
- The firmware caps velocity at 0.4 m/s forward and sideways and 0.8 rad/s turning.
- `robot.preflight(action)` reports what stands in the way of a stand, a walk or a trajectory, and sends nothing.

## Connection modes

`connect(mode)` selects how commands, state and media reach the robot. The API is the same in every mode.

| Mode | Carries | Needs |
|---|---|---|
| `"udp"` | control and state over UDP | the robot's address |
| `"hybrid"` | control and state over UDP, camera and audio over LiveKit | the robot's address, the Asimov Manager URL and an SDK credential |
| `"livekit"` | everything through the robot's LiveKit room | the Asimov Manager URL and an SDK credential |

The SDK credential comes from the **Developer** page of Asimov Manager. [connect.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/connect.py) shows each mode.

## Command line

The `menlo` command saves robots, shows readiness and runs the basic verbs. `Robot()` and the examples use the saved robot when no `MENLO_*` variable says otherwise.

```bash
menlo setup                          # save a robot: name, connection mode, live checks
menlo status --watch                 # READY, NOT READY or FAULTED; sends nothing
menlo stand                          # DAMP to STAND, then wait until armed; asks first
menlo walk --vx 0.3 --duration 3     # walk, then stop(); asks first
menlo stop                           # zero velocity, only in MOVE
menlo damp                           # every actuator limp; asks first
```

Every command and flag is on the [command line page](https://docs.menlo.ai/guides/python-sdk/cli).

## Documentation

- [Python SDK on Asimov 1](https://docs.menlo.ai/asimov/1/program/sdk): install, run the examples, operate from the command line.
- [Python SDK guide](https://docs.menlo.ai/guides/python-sdk): connection modes, moving, joints, state, media, recording, safety and reference.
- [docs/REFERENCE.md](https://github.com/menloresearch/menlo-sdk/blob/main/docs/REFERENCE.md): every option, the protocol and the errors.
- [docs/SKILL.md](https://github.com/menloresearch/menlo-sdk/blob/main/docs/SKILL.md): a brief for agents writing scripts against this SDK.
- [CHANGELOG.md](https://github.com/menloresearch/menlo-sdk/blob/main/CHANGELOG.md)

## Requirements

- Python 3.12 or newer.
- An Asimov 1 robot. For hybrid and livekit, an SDK credential from Asimov Manager.
- Optional, only for the calls that use them: `Pillow` for `Frame.to_jpeg()`, `numpy` for `Frame.to_numpy()`, `opencv-python` for `Clip.save_mp4()`.

## Contributing

Issues and pull requests are welcome at https://github.com/menloresearch/menlo-sdk. [CONTRIBUTING.md](https://github.com/menloresearch/menlo-sdk/blob/main/CONTRIBUTING.md) describes the development setup and the checks a change must pass.

## Security

Report a vulnerability privately to security@menlo.ai, not in a public issue. See [SECURITY.md](https://github.com/menloresearch/menlo-sdk/blob/main/SECURITY.md).

## License

MIT. See [LICENSE](https://github.com/menloresearch/menlo-sdk/blob/main/LICENSE).
