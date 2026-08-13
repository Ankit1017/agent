# ADR 0031: Local Wake-Word Speech Input

## Status

Accepted.

## Context

The speaking page needs optional hands-free input without cloud speech APIs, agent tools, audio
persistence, or a second model call. Continuous browser microphone audio requires tighter protocol
bounds than ordinary JSON requests.

## Decision

Add provider-neutral wake detection and transcription ports behind `SpeechInputService`. Compose
Sherpa-ONNX open-vocabulary English keyword spotting for fixed “Hey Buddy” and Faster Whisper small
multilingual CPU-INT8 transcription for English/Hindi. Models are checksum-pinned, installed only
by `scripts/setup-speech-input.ps1`, and preloaded only when explicitly enabled.

Capture uses a repository-owned AudioWorklet to produce 16 kHz mono s16le and a same-origin
WebSocket authenticated by browser cookie, exact Origin, and an initial CSRF control frame. Enforce
one global session, bounded frames/rate/silence/duration, disconnect cleanup, and pause during model
generation and TTS. Audio and partial recognition are memory-only and never logged or persisted.
Only the irreversibly redacted final transcript returns to the browser and enters the existing
one-call, no-tools voice-conversation endpoint.

## Consequences

Users opt in once per page visit. Wake-plus-command and wake-then-command are supported through
bounded pre-roll and a follow-up window; tap-to-talk remains available. Auto-submit favors latency
over transcript review. Chrome/Chromium on localhost and CPU inference are the v1 target. Barge-in,
arbitrary wake phrases, cloud STT, background/mobile listening, speaker identification, and
persisted audio remain deferred.
