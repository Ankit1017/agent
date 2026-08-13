# Local AI Gateway and Control Center

This deployment keeps Ollama native on Windows and reuses `gpt-oss:20b` from
`D:\ollama-models`. Docker runs LiteLLM, PostgreSQL, and SearXNG. The Harness GUI runs on the
Windows host so it can use approved PowerShell and selected workspaces. It never
stores, pulls, or copies the Ollama model.

The harness defaults new sessions to the LiteLLM alias `gpt-5.5` and allows switching to
`gpt-oss:20b`. Configure the exact selectable aliases with `HARNESS_MODELS`; adding a model to
LiteLLM alone does not automatically expose it in the harness.

The host GUI also exposes workspace-local evaluation metrics and controlled candidate review.
Evaluation records remain on Windows under `.harness/evaluations`, outside Docker. Offline suites
need no network or model call; explicit candidate proposals use the configured model but never edit
source code or Git.

## Request path

```text
Harness browser / terminal clients / optional legacy Open WebUI
                 |
                 v
      LiteLLM gateway :4000
                 |
                 v
       Native Ollama :11434
                 |
                 v
       D:\ollama-models\gpt-oss:20b
```

Use LiteLLM's endpoint for every application that must be observed. Direct
requests to port 11434 work, but necessarily bypass the gateway and its logs.

The harness searches through the private SearXNG instance at <http://127.0.0.1:8080>.
SearXNG and page extraction run locally, but queries still reach the configured Brave,
DuckDuckGo, and Bing engines and page requests reach external websites.

## One-click controls

- `Setup Local AI.cmd` downloads the pinned application images once and starts everything.
- `Start Local AI.cmd` starts Docker Desktop when needed, Ollama, SearXNG, LiteLLM, and the host Harness GUI without pulling.
- `Stop Local AI.cmd` stops the complete stack and releases model RAM/VRAM.
- `Deep Stop Local AI.cmd` also stops Docker Desktop and therefore every other Docker workload.
- `Restart Local AI.cmd` restarts without downloading images or models.
- `Open Local AI.cmd` opens the Harness GUI at <http://127.0.0.1:3000>.
  It uses a dedicated, versioned Chrome profile so service workers cached by the retired Open
  WebUI installation cannot replace the Harness interface with its legacy error page.
- `Open Legacy WebUI.cmd` starts preserved Open WebUI at <http://127.0.0.1:3001>.
- `Open Observability.cmd` opens LiteLLM at <http://localhost:4000/ui>.
- `Show Gateway Credentials.cmd` displays local UI/API credentials from the ignored `.env` file.
- `Test Observed Call.cmd` sends a short real model request through the gateway.
- `Local AI Status.cmd` shows health, memory limits, context, loaded models, and GPU information.

After initial setup, start and restart use `--pull never`. The optional legacy
Open WebUI profile also has automatic model and update downloads disabled.

## Calling the observed API

Run `Show Gateway Credentials.cmd`, then configure applications as follows:

```text
Provider: OpenAI Compatible
Base URL: http://localhost:4000/v1
API key:  the local sk-local-* key displayed by the controller
Model:    gpt-oss:20b
```

PowerShell/curl example:

```powershell
$key = "sk-local-your-key"
curl.exe http://localhost:4000/v1/chat/completions `
  -H "Authorization: Bearer $key" `
  -H "Content-Type: application/json" `
  -d '{"model":"gpt-oss:20b","messages":[{"role":"user","content":"Hello"}],"stream":false}'
```

## Observability

Open <http://localhost:4000/ui> and sign in with the credentials printed by
`Show Gateway Credentials.cmd`. LiteLLM persists request, model, token, latency,
error, user/key, and budget/accounting information in its PostgreSQL volume.
The harness and optional legacy Open WebUI are preconfigured to send inference
calls through this gateway.

Full content logging is enabled deliberately. New spend-log rows include the
request messages, complete response (including reasoning when the provider
returns it), proxy request parameters, token usage, timestamps, duration,
status, provider/model identifiers, user, metadata, tags, session information,
requesting address, and errors where applicable. In the LiteLLM UI, open
**Logs** and select a row to inspect its request and response.

Detailed spend logs are retained for 30 days and cleaned daily. Because prompts
and responses may contain source code, personal data, or secrets, do not send
sensitive content unless you intend it to be stored locally. Direct calls to
Ollama on port 11434 remain outside LiteLLM and cannot be logged by this layer.
Individual stored request/response strings are allowed up to 1 MB to avoid
truncating normal coding-agent prompts. Exceptionally large payloads can still
be truncated as a database safety measure.

The live hardware/context view is available through `Local AI Status.cmd` or:

```powershell
curl.exe http://localhost:11434/api/ps
```

Ollama's runtime context is 8192 tokens to control laptop memory; the model's
native maximum is 131072. Increasing the runtime context increases RAM/VRAM use.

## Resource and security policy

- PostgreSQL is capped at 256 MB RAM and 0.5 CPU.
- LiteLLM is capped at 1.25 GB RAM and 1.5 CPUs.
- Optional legacy Open WebUI is capped at 2.5 GB RAM and 2 CPUs when started.
- SearXNG is capped at 512 MB RAM and 0.75 CPU and is bound to localhost port 8080.
- Ollama loads the model on demand and unloads it after two idle minutes.
- Setup downloads `embeddinggemma` once for local project-memory vectors. Normal Start never pulls
  it; if it is missing, the harness reports lexical fallback instead of failing startup.
- All browser/API ports are bound to localhost.
- Secrets are generated locally in `local-ai/.env`, which Git ignores.
- The model remains in `D:\ollama-models`; no Docker model volume is created.

If the legacy UI is enabled, its first account becomes administrator. Disable
new signups after creating that account if no additional users are needed.
