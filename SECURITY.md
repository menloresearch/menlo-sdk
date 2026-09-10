# Security

This SDK sends motion commands to a robot over plain UDP on a local network and trusts the
network it runs on. Report a vulnerability privately to the maintainers at
security@menlo.ai rather than in a public issue; include the version and a way to
reproduce. You will get an acknowledgement within a few days.

What the SDK does to limit damage on its own: speeds are clamped client-side and the clamp
is visible; a held velocity expires on the robot two seconds after the last packet; a lost
link sends a zero velocity and stops re-sending any held trajectory (the edge DAMPs it two
seconds later); state datagrams that do not look like the robot are dropped,
and `state_source=` restricts which address may send state.
