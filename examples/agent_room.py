"""A LiveKit agent and this SDK, in the robot's own room.

The point of the room convention: a voice/vision agent needs no glue from this SDK. The
robot publishes its camera and microphone as ordinary LiveKit tracks, so an agent
framework subscribes to them the way it subscribes to a human's webcam, and the SDK joins
the same room as one more participant to do the driving.

    ┌──────────────┐       LiveKit room "robot-<robot serial>"       ┌──────────────┐
    │ robot's edge │  video+audio tracks ─────────────────────────> │ agent        │
    │              │  <──────────── data topic "commands" ───────── │ (this file)  │
    │              │  ─────────────  data track "state" ──────────> │ + menlo.asimov │
    └──────────────┘                                                └──────────────┘

The room convention
-------------------
* **room**: one per robot, ``robot-<sanitized serial>`` — every participant that may drive it
  is admitted to that room and nowhere else. The manager tells the SDK the name.
* **identity**: a claim INSIDE each participant's token (``sub``), not something a client
  chooses — the edge's is its serial, an SDK session's is ``sdk-<credential id>[-<label>]``
  as the manager mints it. The edge's arbiter tracks who is driving by identity, so the
  manager mints one token per participant. The SDK has no ``identity`` argument; it reads
  the token's back and reports it (``ManagerConfig(label=...)`` names your session).
* **data topic "commands"**: one bare serialized ``asimov.io.RobotCommand`` per packet,
  reliable, exactly the protobuf the UDP lane sends. ``menlo.asimov`` writes these for you.
* **data track "state"**: one bare serialized ``asimov.io.RobotState`` per frame, pushed
  by the edge at 10 Hz, delivered in order; each frame's ``user_timestamp`` is the edge's
  receive clock (``State.edge_timestamp_us``). ``robot.state`` is the decoded latest one.
* **tracks**: the robot's camera is a video track, its microphone an audio track. Anything
  the SDK plays through ``robot.speaker`` is an audio track published back.

There is no envelope and no type tag on either topic: the topic identifies the type.

Why there is no model glue in this SDK
--------------------------------------
``livekit-plugins-google``'s ``RealtimeModel(video_input=True)`` already samples the
subscribed video track to roughly 1 fps of JPEG and the audio track to 16 kHz PCM, which
is what Gemini Live wants. Adding a converter here would be a second, worse copy of that.
The SDK's job is the robot: verbs, state, waits, safety.

Running it
----------
    pip install "menlo-sdk[livekit]" "livekit-agents[google]~=1.0"
    # the agent FRAMEWORK's LiveKit credentials — never the SDK's:
    export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
    # the SDK's: the robot's manager and a credential from its /sdk page
    export MENLO_MANAGER_URL=http://<robot> MENLO_CREDENTIAL=...   # or: menlo login
    python examples/agent_room.py dev

``LIVEKIT_API_KEY``/``LIVEKIT_API_SECRET`` belong to the agent framework, which is a server
process. **The SDK never sees them, or a token**: with ``ManagerConfig`` it asks the robot's
manager for the room and a fresh join token on every connect, and has no parameter for a
secret anywhere.
"""

from __future__ import annotations

import os

from menlo.asimov import ConnectionConfig, ManagerConfig, Mode, Robot

# The robot's manager, and a credential from its /sdk page. No LiveKit token anywhere.
MANAGER = os.environ.get("MENLO_MANAGER_URL", "http://127.0.0.1:8080")
CREDENTIAL = os.environ.get("MENLO_CREDENTIAL", "")


def drive_from_the_same_room() -> None:
    """The SDK half: join the robot's room, look, and walk. This is all of it."""
    cfg = ConnectionConfig(livekit=ManagerConfig(url=MANAGER, credential=CREDENTIAL, label="agent"))
    with Robot(cfg).connect("livekit") as robot:
        # endpoint reads "<room>@<url> as <identity>" — the identity the TOKEN claimed.
        print(robot.info)  # transport=livekit, dof, capabilities from the tracks that arrived

        if robot.has("camera"):
            photo = robot.camera.photo(timeout=5.0)  # ONE fresh rgb8 frame
            print(f"saw a {photo.width}x{photo.height} {photo.encoding} frame")
            clip = robot.camera.capture_clip(2.0, audio=robot.has("microphone"))
            print(f"{len(clip.frames)} frames at {clip.fps:.1f} fps")
            if clip.audio:
                print("wrote", clip.save_wav("/tmp/asimov-clip.wav"))

        robot.stand()
        robot.wait_for(Mode.STAND, timeout=15)
        robot.set_velocity(vx=0.25, duration=3.0)  # held at 10 Hz, then zeroed
        robot.wait_for(Mode.MOVE)
        robot.stand()
        robot.wait_for(Mode.STAND)


def agent_entrypoint(ctx: object) -> None:
    """The livekit-agents half, in outline. Uncomment once the plugins are installed::

        from livekit.agents import Agent, AgentSession
        from livekit.plugins import google

        async def entrypoint(ctx):
            await ctx.connect()                      # the SAME room as the robot's edge
            session = AgentSession(
                llm=google.beta.realtime.RealtimeModel(
                    model="gemini-2.0-flash-exp",
                    video_input=True,                # subscribes the robot's camera track
                )
            )
            await session.start(
                agent=Agent(instructions="You are the robot's eyes. Describe what you see."),
                room=ctx.room,
            )

    The plugin does the sampling: about 1 fps of JPEG from the video track and 16 kHz PCM
    from the audio track. Drive with ``menlo.asimov`` from a tool the model can call, or from
    a thread beside the session — ``Robot`` is thread-safe and every verb returns at once.
    """
    raise NotImplementedError("install livekit-agents and paste the block above")


if __name__ == "__main__":
    if not CREDENTIAL:
        raise SystemExit(
            "set MENLO_CREDENTIAL to a credential from the robot's manager (/sdk page). "
            "The SDK asks the manager for the room and a LiveKit token itself."
        )
    drive_from_the_same_room()
