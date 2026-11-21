# io_visage is the 2D avatar, and its code stays in the sibling repository 2D_Aletheia

The spec of `io_visage` was a VTube Studio controller. We replace VTube Studio by the 2D vector
avatar of the repository `2D_Aletheia`: SVG parts, a rig manifest, a browser engine, and a Python
server that bridges NATS. The browser source of OBS is the renderer.

**The code stays in `2D_Aletheia`.** `services/io_visage/` holds a README only. The entry
`io_visage` of `services/terminal/services.toml` has `cwd = "../2D_Aletheia"` and starts
`server/avatar_server.py --nats`. The engine, the parts, the manifest and the server use the same
command protocol and change together. In two repositories, one change of the protocol would need
two commits. The SVG parts, the reference images and the drawing tools have their own history,
which does not belong in this repository.

**The cost:** this repository depends on a sibling repository. A clone of `Aletheia-V3` alone has no
avatar, and the daemon fails to start `io_visage`. The two repositories must be side by side.

**Considered options:**

- Move only the server to `services/io_visage/`, and let it serve the engine from `2D_Aletheia`.
  Rejected: the protocol becomes split between the server in one repository and the engine that
  runs the commands in the other repository.
- Move all of `2D_Aletheia` into this repository. Rejected: it adds an art project, its assets and
  its history, for no gain at run time.

**Consequences:** the punctuation of the avatar (short gestures) comes from the text of each
fragment, in the engine, not from a tool of the LLM. A new tool would need new synthetic data and
a new training of the LoRA. When a tool `react` comes, it publishes on a new topic
`io.face.command`. The old planned topic `io.face.emotion` is removed from the contract.
