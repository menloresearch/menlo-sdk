<p align="center">
  <a href="https://menlo.ai"><img src="https://docs.menlo.ai/menlo-logo.svg" alt="Menlo" width="160"></a>
</p>

# menlo-sdk

Drive an Asimov robot from Python. One `Robot`, five verbs, waits that read the robot's own
report, and camera, microphone and speaker on the same connection.

```bash
pip install menlo-sdk              # UDP on the LAN; Python 3.12+
pip install "menlo-sdk[livekit]"   # + video, audio and remote connections
```

## Quickstart

Once per machine, save a robot. The credential is minted on the robot
(`asimovctl sdk-token create --role control`, or its web UI's SDK page):

```bash
menlo login http://asimov.local --credential <credential>
```

Then a script is just:

```python
from menlo.asimov import Mode, Robot

with Robot().connect() as robot:  # the robot you logged in to
    if robot.state.mode is Mode.DAMP:  # wake up: STAND is the only way out of DAMP
        robot.stand()
        robot.wait_for(Mode.STAND, timeout=15)
    robot.set_velocity(vx=0.25, duration=4.0, wait=True)  # walk 0.25 m/s for 4 s, then zero
    robot.wait_for(Mode.MOVE)  # ends in MOVE at zero velocity: standing still
    print(robot.state.joint("L_Knee").pos, robot.state.battery)
    print(robot.camera.photo().to_jpeg()[:4])  # needs [livekit] and Pillow
```

`Robot()` finds the robot from `MENLO_MANAGER_URL` + `MENLO_CREDENTIAL`, else from
`~/.menlo/robots.toml` (`menlo robots`, `menlo use <name>`, `menlo logout <name>`).
Or say where it is: `Robot(ConnectionConfig(udp=UdpConfig("asimov.local"))).connect("udp")`.

## The verbs

| Verb | What it does |
|---|---|
| `set_velocity(vx, vy, vyaw, duration=, wait=)` | walk; held at 10 Hz until superseded, `stop()`, or `duration` |
| `stop()` | zero velocity; the robot keeps balancing in MOVE |
| `stand()` | stiffen into the standing pose; the wake-up verb, no balance loop |
| `damp()` | motors compliant now; a standing robot folds. The emergency verb |
| `goto(positions, duration=)` / `trajectory(positions)` | joint targets, radians, policy off |

Every verb returns a `Sent` at once. `wait_for(Mode.X)` and `wait_until(pred)` block on the
robot's report and raise `RobotFaultedError`, `StateStaleError` or `WaitTimeoutError`
instead of guessing. `robot.state` is the latest sample: mode, joints, IMU, alerts, battery.

## Three lanes, one API

| `connect(mode)` | control + state | video + audio | when |
|---|---|---|---|
| `"udp"` | UDP 8850 / 8851 | — | on the robot's LAN, no camera needed |
| `"hybrid"` | UDP | LiveKit | on the LAN, with camera |
| `"livekit"` | LiveKit | LiveKit | from anywhere the robot's manager is reachable |

`ManagerConfig(url, credential)` asks the robot's manager for the room and a fresh join
token on every connect; the SDK never holds a LiveKit secret.

## Read before you let go of the robot

- **A walk ends in MOVE at zero velocity, not in `stand()`.** STAND is a stiffen with no
  balance loop; asking a free-standing biped to stiffen after walking tips it over.
- **The SDK sends zero when your script stops.** `close()`, the end of a `with` block, a
  lost link and a fault-DAMP all release a held velocity. The edge DAMPs on its own about
  two seconds after the last command it heard.
- **`damp()` folds the robot.** It is never implied by anything else.
- Speeds are clamped client-side (0.6 m/s, 1.5 rad/s by default) and the clamp is visible
  on `Sent.clamped`.

## More

- [Reference](https://github.com/menloresearch/menlo-sdk/blob/main/docs/REFERENCE.md): every
  option, the safety model in full, the wire, errors, development.
- [SKILL.md](https://github.com/menloresearch/menlo-sdk/blob/main/docs/SKILL.md): the
  two-page brief for an agent writing a script against this SDK.
- [Examples](https://github.com/menloresearch/menlo-sdk/tree/main/examples) and the
  [changelog](https://github.com/menloresearch/menlo-sdk/blob/main/CHANGELOG.md).
- Versions follow PEP 440; pre-releases (`0.1.0rc1`) install only with `pip install --pre`.
  How releases are cut: [RELEASING.md](https://github.com/menloresearch/menlo-sdk/blob/main/RELEASING.md).

## License

MIT. The wire types come from [`asimov-protocol`](https://pypi.org/project/asimov-protocol/) (MIT).
