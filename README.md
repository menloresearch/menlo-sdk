<p align="center">
  <a href="https://menlo.ai"><img src="https://docs.menlo.ai/menlo-logo.svg" alt="Menlo" width="160"></a>
</p>

# menlo-sdk

Python SDK for Asimov robots: walking, joint control, robot state, camera and audio over UDP or LiveKit.

`menlo-sdk` drives an Asimov robot through Asimov Edge, the service on the robot, with one `Robot` class and the robot's own verbs: `stand`, `set_velocity`, `stop`, `damp`, `goto` and `trajectory`. The import is `menlo`; the Asimov robot lives in `menlo.asimov`.

## Installation

Python 3.12 or newer.

```bash
pip install menlo-sdk
# or, with uv
uv add menlo-sdk
```

## Quickstart

1. Save a robot on your machine. `menlo setup` asks for a name, a connection mode and only the fields that mode needs, checks them against the robot, and saves the result to `~/.menlo/robots.toml`:

   ```bash
   menlo setup
   ```

   Without a terminal, pass the fields as flags:

   ```bash
   menlo robots add lab --mode udp --udp 192.168.22.32 --no-input
   ```

2. Stand up, walk forward for 3 s, and stop:

   ```python
   from menlo.asimov import Mode, Robot

   with Robot().connect() as robot:
       if robot.state.mode is Mode.DAMP:
           robot.wait_ready("stand")  # fresh state, no fault, battery and actuators ok
           robot.stand()
           robot.wait_for(Mode.STAND)
       robot.wait_ready("move")  # armed: STAND held upright for 0.5 s
       robot.set_velocity(vx=0.2, duration=3.0, wait=True)
       robot.stop()  # zero velocity: the robot stays in MOVE and balances in place
   ```

`Robot()` reads the saved robot, and `connect()` with no argument uses its connection mode. Environment variables override both: `MENLO_ROBOT` picks a saved robot, `MENLO_MODE` the connection mode, and `MENLO_UDP_HOST` or `MENLO_MANAGER_URL` with `MENLO_CREDENTIAL` describe a robot without saving it.

Call `stand()` only from DAMP, after `wait_ready("stand")`. A walk ends with `stop()`: the robot stays in MOVE at zero velocity and keeps balancing. Joint control (`goto`, `trajectory`) ends with `damp()`, with the robot still supported.

## Connection modes

`connect(mode)` selects how commands, state and media reach the robot. The API is the same in every mode.

| Mode | Carries | Needs | Use when |
|---|---|---|---|
| `"udp"` | control and state over UDP (ports 8850 and 8851) | the robot's address | you are on the robot's network and need no camera |
| `"hybrid"` | control and state over UDP, camera and audio over LiveKit | the robot's address, the Asimov Manager URL and an SDK credential | you are on the robot's network and need media |
| `"livekit"` | everything through a LiveKit room | the Asimov Manager URL and an SDK credential | the robot is reachable only through its Asimov Manager |

The SDK credential comes from the **Developer** page of Asimov Manager, the robot's web console, with the **Control** role. To describe the connection in code instead of a saved robot:

```python
from menlo.asimov import ConnectionConfig, ManagerConfig, Robot, UdpConfig

config = ConnectionConfig(
    udp=UdpConfig(host="192.168.22.32"),
    livekit=ManagerConfig(url="http://192.168.22.32", credential="<SDK credential>"),
)

with Robot(config).connect("hybrid") as robot:
    print(robot.state.mode.name, robot.has("camera"))
```

## Check before you move

`robot.preflight(action)` reads the latest state and reports what stands in the way of a `"stand"`, a `"move"` (`set_velocity`) or a `"trajectory"`. It sends nothing. `robot.wait_ready(action)` blocks until the check passes and raises `NotReadyError` on timeout, or at once on a problem that waiting cannot clear.

```python
check = robot.preflight("move")
if not check.ok:
    print(check)  # "not ready to move:" and one line per problem

robot.wait_ready("move", timeout=5.0)
```

Each problem has a stable code: `stale_state`, `faulted`, `battery_low`, `joint_hot`, `wrong_mode` (for example, a walk asked for in DAMP) and `not_armed` block; `unknown_battery` and `unknown_joint_temp` are warnings for fields the robot does not report. The full list is on the [safety page](https://docs.menlo.ai/asimov/1/program/sdk/safety).

## Safety

Read the [safety page](https://docs.menlo.ai/asimov/1/program/sdk/safety) before you run a script on a robot.

- The Motion Control Board firmware caps velocity at 0.4 m/s forward and sideways and 0.8 rad/s turning. `Limits()` defaults to those caps and `Sent.clamped` reports when the SDK reduced a speed.
- The robot enters MOVE from STAND only once it is armed: STAND held upright for 0.5 s. A velocity sent earlier leaves the robot in STAND and nothing reports it. Call `wait_ready("move")` between `stand()` and the first `set_velocity()`.
- A critical alert (a fall, actuator over-temperature, battery protection, a joint limit, lost actuator communication, a watchdog) latches DAMP until the firmware restarts. `preflight()` reports `faulted`, `wait_ready()` raises `NotReadyError` with the code `faulted`, `wait_for()` and `wait_until()` raise `RobotFaultedError`, and nothing a script sends clears it.
- The SDK re-sends a held velocity at 10 Hz and sends zero velocity on `stop()`, and on `close()`, the end of a `with` block or a lost link while it holds a velocity. On udp and hybrid, Asimov Edge zeroes velocity 2 s after the last command it received; the robot stays in MOVE, balancing. A trajectory not re-sent for 2 s puts the robot in DAMP.
- Nothing in the SDK is an emergency stop. `damp()` makes every actuator compliant and a standing robot folds. For the physical stop, see [Stopping the robot](https://docs.menlo.ai/asimov/1/operate/safety/stopping).

## Examples

Each file in [`examples/`](https://github.com/menloresearch/menlo-sdk/tree/main/examples) is one short, runnable script. Files without a connection mode in their name use the saved robot. Each is walked through on the [examples page](https://docs.menlo.ai/asimov/1/program/sdk/examples).

Connect

- [examples/01_connect_udp.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/01_connect_udp.py): connect over udp and print the robot's state.
- [examples/02_connect_hybrid.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/02_connect_hybrid.py): UDP control with LiveKit camera and audio.
- [examples/03_connect_livekit.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/03_connect_livekit.py): everything through the LiveKit room, via Asimov Manager.

Move

- [examples/04_preflight.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/04_preflight.py): check readiness to stand, walk or run a trajectory.
- [examples/05_stand_and_walk.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/05_stand_and_walk.py): stand from DAMP, wait until armed, walk for 3 s, stop.
- [examples/06_stop_and_shutdown.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/06_stop_and_shutdown.py): end a walk early with `stop()`, and optionally finish in DAMP.
- [examples/08_move_joints.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/08_move_joints.py): move the head with `goto()`.

State, media and recording

- [examples/07_read_state.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/07_read_state.py): robot mode, arming, battery, actuator temperatures, mode changes and alerts.
- [examples/09_camera_and_audio.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/09_camera_and_audio.py): save a photo and a clip with sound, and play a tone on the speaker.
- [examples/10_record_and_replay.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/10_record_and_replay.py): record robot state to a JSON-lines file and read it back.
- [examples/11_raw_livekit.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/11_raw_livekit.py): the LiveKit room with `livekit` and `asimov-protocol` only, without the SDK.

Apps

- [examples/apps/agent_room.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/apps/agent_room.py): camera and walking as tools for an agent in the robot's room.
- [examples/apps/follow_the_ball.py](https://github.com/menloresearch/menlo-sdk/blob/main/examples/apps/follow_the_ball.py): steer towards a ball seen by the robot's camera.

## Command line

The `menlo` command saves robots, shows readiness and runs the basic verbs. Every flag is on the [command line page](https://docs.menlo.ai/asimov/1/program/sdk/cli).

```bash
menlo setup                          # save a robot: name, connection mode, checks
menlo robots                         # list saved robots
menlo robots add NAME [--mode MODE] [--udp HOST] [--manager URL] [--credential C]
menlo robots use NAME                # make NAME the default
menlo status --watch                 # READY, NOT READY or FAULTED, updated in place; sends nothing
menlo stand                          # DAMP to STAND, then wait until armed; asks first
menlo walk --vx 0.2 --duration 3     # walk, then stop(); duration is required, 10 s at most; asks first
menlo stop                           # zero velocity, only in MOVE; never asks
menlo damp                           # every actuator compliant; asks first; not an emergency stop
```

`stand`, `walk` and `damp` print one line with the plan (the robot, its connection mode and address, its state, and what will happen) and ask `Proceed? [y/N]`. Only `y` goes ahead. `-y` or `--yes` skips the question; with no terminal to ask on, pass `--yes` or the command stops with exit code `2`. When the robot cannot do it now, `stand` and `walk` print `Not feasible:` with the reason and the fix, and do not ask. `walk` checks again after you answer.

```console
$ menlo walk --vx 0.2 --duration 3
lab (hybrid, 192.168.22.32) · STAND, armed · battery 82 % → walk vx 0.20 m/s, vy 0.00 m/s, vyaw 0.00 rad/s for 3.0 s, then stop
Proceed? [y/N]
```

`--robot NAME` and `--mode udp|hybrid|livekit` work with every command. For scripts and agents: `menlo status --json` and `menlo robots --json` print machine-readable output, and `--yes` runs `stand`, `walk` and `damp` without a question. Exit codes: `0` done, `1` error, `2` usage, `3` not feasible, `4` cancelled (you answered no; nothing was sent), `130` interrupted.

## Documentation

- [SDK documentation](https://docs.menlo.ai/asimov/1/program/sdk): quickstart, connection modes, moving, joints, state, media, safety, command line and reference.
- [docs/REFERENCE.md](https://github.com/menloresearch/menlo-sdk/blob/main/docs/REFERENCE.md): every option, the protocol, the safety model, the errors and how to develop the SDK.
- [docs/SKILL.md](https://github.com/menloresearch/menlo-sdk/blob/main/docs/SKILL.md): a condensed brief for agents writing scripts against this SDK.
- [CHANGELOG.md](https://github.com/menloresearch/menlo-sdk/blob/main/CHANGELOG.md)

## Requirements

- Python 3.12 or newer.
- Dependencies, installed with the package: `asimov-protocol` (the protocol types), `protobuf`, `livekit` (hybrid and livekit modes), `questionary` and `rich` (the `menlo` command). `import menlo.asimov` and `connect("udp")` load none of the last three.
- Optional, imported only by the calls that need them: `Pillow` for `Frame.to_jpeg()`, `numpy` for `Frame.to_numpy()`, `opencv-python` for `Clip.save_mp4()`.
- An Asimov robot. For udp and hybrid, Asimov Edge needs `udp-control` on and `udp-state-host` set to your machine's address (the Asimov Edge parameters in Asimov Manager). For hybrid and livekit, an SDK credential from Asimov Manager. The SDK checks the protocol version at connect time and raises `ProtocolMismatchError` on a mismatch.

## Contributing

Issues and pull requests are welcome at https://github.com/menloresearch/menlo-sdk. [CONTRIBUTING.md](https://github.com/menloresearch/menlo-sdk/blob/main/CONTRIBUTING.md) describes the development setup and the checks a change must pass.

## Security

Report a vulnerability privately to security@menlo.ai, not in a public issue. See [SECURITY.md](https://github.com/menloresearch/menlo-sdk/blob/main/SECURITY.md).

## License

MIT. See [LICENSE](https://github.com/menloresearch/menlo-sdk/blob/main/LICENSE). The protocol types come from [`asimov-protocol`](https://pypi.org/project/asimov-protocol/) on PyPI, also MIT.
