# Visual Eyes MCP

**Give AI agents the ability to see, understand, and search through videos.**

Visual Eyes MCP is a Python-based [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that connects AI agents to video understanding capabilities powered by Google's Gemini API.

It allows compatible AI clients to ask questions about videos, find specific moments, and inspect individual frames without manually watching the entire recording.

> **Status:** Work in progress. The project is under active development. No stable release is available yet.

## Features

- **Video understanding:** Ask natural-language questions about an entire video.
- **Timestamped answers:** Get approximate timestamps for relevant moments.
- **Moment search:** Find scenes or events using natural-language descriptions.
- **Time-range analysis:** Focus questions on a specific section of a video.
- **Local video support:** Analyze videos stored in explicitly allowed directories.
- **YouTube support:** Analyze publicly accessible YouTube videos without downloading them.
- **Frame extraction:** Extract local frames for closer inspection, including on-screen text.
- **SQLite caching:** Reuse cached analysis results and reduce unnecessary API requests.
- **Quota-aware requests:** Check usage limits before sending requests to Gemini.

## How It Works

Visual Eyes MCP acts as a bridge between an MCP-compatible AI client and Google's Gemini API.

```text
You
 │
 ▼
AI Client
 │
 ▼
Visual Eyes MCP Server
 │
 ├── Video source validation
 ├── Cache and quota checks
 ├── FFmpeg / FFprobe
 ├── Gemini API
 └── Timestamped results
 │
 ▼
AI Client
```

The server handles video validation, processing, caching, and communication with Gemini, then returns structured results to the AI client.

## Requirements

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- FFmpeg and FFprobe
- A Google Gemini API key
- An MCP-compatible client

## Installation

Clone the repository:

```bash
git clone https://github.com/RMXHM/visual-eyes-mcp.git
cd visual-eyes-mcp
```

Install dependencies:

```bash
uv sync
```

Make sure FFmpeg and FFprobe are available on your system:

```bash
ffmpeg -version
ffprobe -version
```

## Configuration

Visual Eyes MCP reads its Gemini API key from the process environment and from
the project-root `.env` file. Copy the placeholder file once, then edit it:

```bash
cp .env.example .env
# edit .env and replace the placeholder key
```

The server and integration test load `.env` automatically, so you do not need to
`export` the key for local development. Variables already present in the shell
take precedence over values in `.env`.

`GOOGLE_API_KEY` is also supported as a fallback when `GEMINI_API_KEY` is unset or
blank. `GEMINI_API_KEY` remains the first choice.

**Security notes:**
- Never commit API keys or other credentials.
- Keep local secrets in the ignored `.env` file or your environment.
- `.env` is ignored by Git; `.env.example` contains placeholders only.
- API keys are not accepted through the TOML configuration file.
- Logs and provider errors are designed to redact credential material.

Additional settings can be configured through the project's TOML configuration and environment overrides. See [ARCHITECTURE.md](ARCHITECTURE.md) and [TECHNICAL_DECISIONS.md](TECHNICAL_DECISIONS.md).

## Development

Run the test suite:

```bash
uv run pytest
```

Run lint checks:

```bash
uv run ruff check src tests
```

The ordinary test suite uses fake providers and does not make real Gemini API calls.

### Gemini integration test

A separate integration test exercises the real Gemini provider.

It requires two explicit opt-ins:

1. A non-blank `GEMINI_API_KEY` in the process environment or project-root `.env`.
2. `VISUAL_EYES_RUN_INTEGRATION=1` in the process environment.

The key is loaded automatically from `.env` if you have not exported it. The run
flag is read before `.env` is loaded, so a local `.env` cannot accidentally turn
an ordinary test run into a paid call.

Run it with:

```bash
VISUAL_EYES_RUN_INTEGRATION=1 uv run pytest tests/test_gemini_integration.py -m integration -q -s
```

The test creates a short local video and makes at most two Gemini generation requests. Provider-level transient retries are disabled for this test.

Files API upload and polling requests are separate from generation requests. Actual API usage may incur charges depending on your Gemini account and model.

The integration model can be overridden using `VISUAL_EYES_INTEGRATION_MODEL`.

## Project Structure

```text
visual-eyes-mcp/
├── src/
│   └── visual_eyes_mcp/
│       ├── analysis.py
│       ├── cache.py
│       ├── config.py
│       ├── errors.py
│       ├── gemini.py
│       ├── media.py
│       ├── models.py
│       ├── prompts.py
│       ├── server.py
│       ├── sources.py
│       └── tools.py
├── tests/
├── ARCHITECTURE.md
├── MVP.md
├── ROADMAP.md
├── TECHNICAL_DECISIONS.md
├── pyproject.toml
└── uv.lock
```

## Roadmap

Visual Eyes MCP is still evolving. Planned work and technical decisions are documented in:

- [MVP.md](MVP.md)
- [ROADMAP.md](ROADMAP.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [TECHNICAL_DECISIONS.md](TECHNICAL_DECISIONS.md)

## Contributing

Contributions, bug reports, and suggestions are welcome, subject to the project's license.

Please open an issue before starting significant changes so proposed work can be discussed.

## License

This project is licensed under the **PolyForm Noncommercial License 1.0.0**.

You may use, study, modify, and share the software for noncommercial purposes under the license terms. Commercial use requires separate permission from the copyright holder.

See [LICENSE](LICENSE) for the complete terms.

---

**Built by [RMXHM](https://github.com/RMXHM)**
