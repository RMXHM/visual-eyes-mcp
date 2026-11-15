# visual-eyes-mcp

An MCP server that lets an AI agent understand video and look at specific frames.

Work in progress. No release has been published.

- Local video files inside explicitly allowed directories
- Public YouTube URLs (no downloading)
- Whole-video or time-range questions, with approximate timestamps
- Natural-language moment search
- Local frame extraction for on-screen text
- SQLite cache and quota-aware preflight
- Requires `ffmpeg`/`ffprobe` on `PATH`

License: pending (to be decided before public release).

See `ARCHITECTURE.md`, `MVP.md`, `TECHNICAL_DECISIONS.md`, and `ROADMAP.md` for the design.

## Authentication

The Gemini API key is read **only** from the process environment:

```sh
export GEMINI_API_KEY="..."   # your shell; do not commit or paste this into a file
uv run visual-eyes-mcp
```

`GOOGLE_API_KEY` is accepted as a fallback when `GEMINI_API_KEY` is unset. A TOML
file that looks like it contains key material is rejected at startup, and the
startup log line reports only `api_key_present=True/False`. Log output and provider
error messages redact the key and key-shaped strings.

## Tests

Unit and service tests use a fake provider; they never read a real key and never
make network calls:

```sh
uv run pytest
```

The real-provider test is marked `integration` and is skipped unless you opt in
twice — a key in the environment **and** an explicit flag:

```sh
export GEMINI_API_KEY="..."            # already set in your shell
export VISUAL_EYES_RUN_INTEGRATION=1   # second, explicit opt-in
uv run pytest tests/test_gemini_integration.py -m integration -q -s
```

That test generates a one-second local test video and makes at most two
generation requests (`detail="low"`): the initial call plus at most one
structured-output re-ask. Provider-level transient retries are disabled for this
test, so the request budget is predictable. It prints token usage without any
credential material but does not estimate cost in dollars. Do not run it until
you have approved the expected spend for your account and model.
`VISUAL_EYES_INTEGRATION_MODEL` overrides the model if the default tier is not
available to your key.

