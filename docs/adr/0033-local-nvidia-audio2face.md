# ADR 0033: Local NVIDIA Audio2Face Animation Boundary

## Status

Accepted.

## Context

The independent Speech page needs optional 3D facial animation driven by the exact local speech
audio. NVIDIA Audio2Face requires CUDA, TensorRT, a native SDK, and separately licensed model
artifacts. Passing browser paths or commands to that stack, sharing request outputs, retaining
generated audio, or importing the SDK into domain/application code would violate Harness isolation
and privacy boundaries. User-supplied character assets also require explicit rights and structural
validation before a browser can render them.

## Decision

Add provider-neutral facial-animation/avatar values plus `FaceAnimator` and `FaceAvatarRepository`
application ports. `AnimatedSpeechService` first uses the existing redacting `SpeechService`, bounds
the result to 60 seconds, converts mono s16le PCM to 16 kHz in memory, and invokes the animation port.
Only `bootstrap.py` composes the NVIDIA process adapter and fixed avatar repository.

The adapter invokes a repository-owned native bridge with a fixed executable, model, argument array,
working root, and timeout. It accepts no browser path, command, flag, or model selection. Per-request
WAV, JSON, and packed binary files exist only in a request-unique protected directory and are removed
before completion. A shared animation-output file is prohibited. Raw process output and paths are
never returned. One animated synthesis runs globally at a time.

The bridge uses NVIDIA's host blendshape executor with neutral emotion, one track, and 60 fps. It
requires the Mark model's exactly 52 named skin controls. The 16 tongue controls are optional and
are returned only when present; application code further intersects them with matching avatar
morphs. The response contains validated frame-major float32-le weights plus derived jaw/eye values
for SVG fallback. Piper/Web Audio remains the authoritative playback timeline.

Avatars are operator supplied and installed separately with an explicit rights acknowledgement.
The infrastructure validator accepts only self-contained GLB 2.0 and requires all 52 canonical
ARKit controls. It rejects external resources and limits the file to 50 MB, 32 meshes, 500,000
vertices, 128 primitives, 80 morph targets per mesh, 64 materials, 32 textures/images, 256 nodes,
and depth 32. An atomic checksum manifest records only safe metadata. The legacy avatar remains the
default, while additional assets live below exact bounded IDs in a protected catalog. Browser
requests select only those IDs and cannot submit paths or replace assets. Three.js maps named
controls and releases GPU
resources; setup, GLB, WebGL, or runtime failure falls back visibly to SVG.

The browser uses adaptive orthographic presenter framing, the imported GLB's real materials, and
bounded alias-based humanoid poses without changing facial morph weights. Embedded GLB images are
decoded by Three.js through same-document `blob:` URLs. CSP therefore permits `blob:` only in
`connect-src` and `img-src`, while retaining same-origin scripts, APIs, models, and all existing
localhost restrictions. An asset with no assigned material texture is rejected visibly instead of
being rendered as a misleading white mannequin.

Installation is explicit and never occurs at startup. `scripts/setup-audio2face.ps1` validates the
pinned SDK commit, Visual C++, Python 3.10, CUDA 12.8/12.9, TensorRT 10.13, and Git LFS before it can
fetch dependencies, build the bridge, or install the Mark model. Both license switches and prior
Hugging Face authentication are required. `scripts/setup-audio2face-avatar.ps1` never downloads or
converts an avatar. The separate rights-confirmed Character Creator converter invokes only the
pinned local Blender, extracts one exact FBX archive entry without executing it, maps known facial
controls, bounds presentation geometry/textures, converts dense morph accessors to standard sparse
glTF accessors, and passes the result through the same validator.

## Consequences

The browser can switch among validated characters without restarting or changing the Audio2Face
model. The browser can request one bounded response containing PCM plus synchronized animation and can stop
it with `AbortController`. This path has higher first-playback latency and browser memory use than
streaming Piper. It is available in Direct TTS and after the single LLM call in Voice Conversation;
facial generation never adds a model call. Only the current utterance is retained. The feature stays
visibly unavailable until inference and avatar artifacts are valid. NVIDIA GPU, CUDA/TensorRT,
SDK/model terms, and avatar rights must be reviewed independently.

This decision covers a high-quality 3D face only. Full-body JSON actions such as walking, turning,
and gesturing require a later, separately bounded body-animation controller.
