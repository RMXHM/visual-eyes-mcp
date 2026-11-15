# visual-eyes-mcp — MVP definition (v0.1)

**Status:** Phase 1.6. Decisions approved; no code written.
**Decision log:** `TECHNICAL_DECISIONS.md` §1 (approved, deferred, unverified).
**Goal:** the smallest server that lets a coding agent (a) understand what is in a video and
(b) *see* specific frames — without wasting tokens, exceeding the user's quota, or reading files
the user did not authorise.

Four tools, one provider, one cache, one configuration file.

---

## 1. Version-one features

| # | Feature | Why it is in v1 |
| --- | --- | --- |
| F1 | Local video files (allowed directories only) | The cheapest, most reliable path; no platform terms apply |
| F2 | Public YouTube URLs (passthrough; opt-out available) | The main real-world source for "explain this talk"; no download |
| F3 | Whole-video Q&A with timestamps | Core use case |
| F4 | Time-range Q&A | Cheap drill-down; clipping for local files if verified |
| F5 | Natural-language moment search | "Where does he configure the database?" |
| F6 | Local frame extraction returned as images | Read slides/UI/code with zero model tokens |
| F7 | Metadata/probe tool | Prevents blind expensive calls; local only |
| F8 | Transparent cache with `refresh` | Primary cost control |
| F9 | Quota-aware preflight + configurable limits | Respects the caller's actual plan; no hardcoded quotas |
| F10 | Structured, schema-validated results | Agents can rely on the shape |
| F11 | Tiered model selection (`normal` / `high` / configured default) | Cheap by default, escalate on demand |
| F12 | Fail-closed local file policy | Explicit allowed roots, no HOME trust, no implicit uploads |

Deliberately one analysis verb family: "summarise", "describe", "explain" are questions, not tools.

---

## 2. Proposed MCP tools

Naming: verb-first, snake_case, no provider names in the public surface.

### 2.1 `video_info`

> Inspect a source and report local facts plus the caller's configured limits. No model call.

| Param | Type | Required | Notes |
| --- | --- | --- | --- |
| `source` | string | yes | YouTube URL or path inside an allowed directory |

Output (`structuredContent`):

```json
{
  "source_kind": "local",
  "canonical_id": "/home/u/Videos/demo.mp4",
  "duration_seconds": 1873.4,
  "width": 1920, "height": 1080, "fps": 30.0,
  "has_audio": true, "size_bytes": 412000000, "mime_type": "video/mp4",
  "cached_analyses": 3,
  "limits": {
    "model_default": "gemini-3.5-flash-lite",
    "model_high": "gemini-3.8-flash",
    "max_video_seconds": 1800,
    "max_input_tokens_per_call": 200000,
    "rpd": null, "rpm": null, "tpm": null,
    "used_today": {"requests": 2, "input_tokens": 121400}
  },
  "notes": ["duration from ffprobe"]
}
```

YouTube variant: `duration_seconds: null`, with a note that the server does not resolve YouTube
metadata, and a note when the caller's config has YouTube disabled. `limits` values are `null`
when the user has not configured throttling — never a hidden default.

### 2.2 `analyze_video`

> Answer a question about a video, or a `start_seconds`–`end_seconds` range, with approximate
> timestamps.

| Param | Type | Default | Constraints |
| --- | --- | --- | --- |
| `source` | string | — | required |
| `question` | string | — | 3–500 chars |
| `start_seconds` | float | null | ≥ 0, < end |
| `end_seconds` | float | null | > start |
| `detail` | enum `low` \| `normal` \| `high` | `normal` | resolves to model + sampling; `high` requires `model_high` |
| `refresh` | bool | false | bypass cache read |

`detail` semantics:

| Level | Model | Processing | Intent |
| --- | --- | --- | --- |
| `low` | configured default | static, fps 0.5 where supported | cheap triage over long footage |
| `normal` | configured default | static, default sampling | default |
| `high` | `model_high` (3.8 Flash) | static/agentic if supported | ambiguous visual reasoning |

Output:

```json
{
  "answer": "…2–6 sentence direct answer…",
  "key_points": ["…"],
  "timestamps": [{"seconds": 123.0, "label": "02:03", "note": "terminal shows `uv sync`"}],
  "segment": {"start_seconds": 120.0, "end_seconds": 240.0},
  "confidence": "high",
  "model": "gemini-3.5-flash-lite",
  "detail": "normal",
  "cache": {"hit": false},
  "usage": {"input_tokens": 61400, "output_tokens": 812, "thought_tokens": 0},
  "warnings": ["timestamps are approximate (±1–2 s) at the default sampling rate"]
}
```

`usage` fields are confirmed in the SDK types `[verified]`; values are populated from the
interaction response, and omitted if the provider returns none — never faked.

### 2.3 `find_moments`

> Find the parts of a video matching a description. Returns ranked segments, not prose.

| Param | Type | Default | Constraints |
| --- | --- | --- | --- |
| `source` | string | — | required |
| `description` | string | — | 3–300 chars |
| `max_results` | int | 5 | 1–10 |
| `start_seconds` / `end_seconds` | float | null | optional search window |
| `detail` | enum | `normal` | as above |
| `refresh` | bool | false | |

```json
{
  "moments": [
    {"start_seconds": 412.0, "end_seconds": 438.0, "title": "Pip install of torch",
     "reason": "Terminal shows `pip install torch==2.4`", "confidence": 0.82}
  ],
  "searched_range": {"start_seconds": 0.0, "end_seconds": 1873.4},
  "model": "gemini-3.5-flash-lite",
  "cache": {"hit": true},
  "warnings": ["timestamps are approximate (±1–2 s); segment bounds are model-estimated"]
}
```

### 2.4 `extract_frames`

> Extract stills locally and return them as images. No upload, no tokens.

| Param | Type | Default | Constraints |
| --- | --- | --- | --- |
| `source` | string | — | local file inside an allowed root |
| `timestamps` | float[] | null | 1–10, exclusive with `interval_seconds` |
| `interval_seconds` | float | null | ≥ 5, expands to at most `max_frames` |
| `max_frames` | int | 4 | 1–10 |
| `width` | int | 960 | 320–1600; height auto |
| `start_seconds` / `end_seconds` | float | null | optional window for interval mode |

Output: one image content block per frame (JPEG) plus a metadata block:

```json
{"frames": [{"seconds": 123.0, "label": "02:03",
             "path": ".../frames/ab12….jpg", "width": 960, "height": 540}],
 "source_kind": "local", "truncated": false}
```

YouTube is rejected here: frame extraction would require downloading, which v1 refuses.

---

## 3. Tool count rationale

- `video_info` is free and prevents wasteful calls; it is the only way to ask "how long is this and
  can I afford it?" without spending money. It also exposes the caller's configured limits.
- `analyze_video` and `find_moments` are separate because their output contracts differ (one
  answer vs ranked list). They share one internal pipeline.
- `extract_frames` is the only local visual capability and is strictly cheaper than asking the
  model to describe pixels.
- **Not added:** `summarize`, `describe`, `transcribe`, `read_text`, `compare_videos`, `get_cache`,
  `set_model`. Each duplicates a question, duplicates Gemini's internal transcript use, or turns
  configuration into agent-controlled product surface.

---

## 4. Expected limitations (v1)

| Limitation | Consequence |
| --- | --- |
| YouTube: public only | Private/unlisted/members-only/age-restricted fail with a clear message |
| YouTube: preview feature | May be priced or gated without notice; 8 h/day free-tier upload cap `[verified]` |
| YouTube: duration unknown | Cost preflight uses the model-reported timeline or the configured token cap, not a known duration |
| YouTube clipping | **Unverified**; range queries may process the whole video and say so in `warnings` |
| `store=false` with video | **Unverified**; preferred privacy default, verified in M5 before relying on it |
| Agentic video on 3.5 FL / 3.8 Flash | Documentation conflict; static is the fallback |
| Timestamps | Approximate (±1–2 s), never frame-exact |
| `.mov` handling | Mime naming ambiguous across docs; may require remux to mp4 |
| Local formats | ffmpeg-decodable for frames; upload needs Gemini mimes; MKV rejected with a remux hint |
| No transcript output | Gemini consumes audio internally; no subtitle/transcript tool |
| No async jobs | One call, one deadline; long videos may exceed client timeouts |
| Cache is per-user, per-machine | No cross-machine sharing |
| Model quality | Not benchmarked; the default may change after M5 |

---

## 5. Limits and security defaults

### 5.1 Processing limits

| Setting | Default | Reason |
| --- | --- | --- |
| `max_video_seconds` | 1800 | Applies to **processed** duration; 30 min at ~100 tok/s ≈ 180k tokens |
| `max_input_tokens_per_call` | 200_000 | Headroom under a 250K TPM budget (prompt + output included) |
| `max_file_size_mb` | 1024 | Below the 2 GB provider ceiling |
| `max_frames_per_call` | 10 (default 4) | Each image also costs the *client* tokens |
| `max_frame_width` | 1600 | Quality/size balance |
| `max_answer_chars` | 4000 | Keeps responses usable in context |
| `analysis_timeout_seconds` | 120 | Fits common client tool timeouts |
| `probe_timeout_seconds` | 20 | ffprobe on a hostile file must not hang |
| `frame_timeout_seconds` | 30 | Per-frame extraction |
| `max_concurrent_calls` | 1 | Serialises paid work; protects quota and CPU |
| `cache_ttl_seconds` | 604800 | 7 days |
| `max_cache_mb` | 512 | Frame pruning threshold |

### 5.2 Local file policy

- **Allowed roots are explicit.** `paths.allowed_dirs` in TOML; optional process CWD
  (`include_cwd = true`) as a development convenience. HOME is **not** trusted implicitly.
- No roots → local tools return `AccessDenied` with configuration guidance (fail-closed).
- Resolve → containment check → regular file → extension allowlist → ffprobe validation → size cap.
- Symlinks are checked after resolution; escapes are rejected.
- Only `analyze_video` / `find_moments` upload; the tool description and server instructions state
  that the file leaves the machine.

### 5.3 Quota settings

Per-model `rpm` / `rpd` / `tpm` live **only** in the user's local TOML file. The public package
ships with none, so no throttling occurs unless configured. Preflight order: RPD → per-call token
cap → TPM/RPM → concurrency. RPD exhaustion is never retried.

---

## 6. Configuration surface

Single TOML file at `$XDG_CONFIG_HOME/visual-eyes-mcp/config.toml` (default
`~/.config/visual-eyes-mcp/config.toml`), parsed with stdlib `tomllib`; environment variables
override file values.

```toml
# Example only — copy locally. Model IDs are public; quotas are not.
[models]
default = "gemini-3.5-flash-lite"
high = "gemini-3.8-flash"        # used by detail = "high"; "" disables
experimental = "gemini-4-hydrogen"  # only used if explicitly chosen as default/high

[paths]
allowed_dirs = ["/home/me/Videos"]
include_cwd = true

[limits]
max_video_seconds = 1800
max_input_tokens_per_call = 200000
max_file_size_mb = 1024
max_frames_per_call = 10
max_concurrent_calls = 1

[privacy]
store_interactions = false        # preferred; verified in M5

[features]
youtube = true                    # set false to disable URL sources entirely

# Per-model budget — enter the values YOUR dashboard shows. Not shipped.
# [limits."gemini-3.5-flash-lite"]
# rpm = 15
# rpd = 500
# tpm = 250000
```

Environment overrides: `VISUAL_EYES_MODEL`, `VISUAL_EYES_MODEL_HIGH`, `VISUAL_EYES_ALLOWED_DIRS`
(path list), `VISUAL_EYES_YOUTUBE`, `VISUAL_EYES_MAX_VIDEO_SECONDS`,
`VISUAL_EYES_MAX_INPUT_TOKENS_PER_CALL`, `VISUAL_EYES_CONFIG`. Precedence: env > TOML > built-in
default. `GEMINI_API_KEY` / `GOOGLE_API_KEY` stay in the environment and are never written to TOML.

---

## 7. Explicitly excluded from v1

- Downloading YouTube or any platform media, at any quality, by any tool.
- Authentication-gated, DRM-protected, or paywalled sources.
- Streamable HTTP transport, any listening socket, any auth scheme.
- Additional providers (OpenAI, Anthropic, local models) and provider-selection UI.
- Transcript/subtitle extraction, OCR pipelines, scene detection, object tracking, embeddings.
- Video editing, clip export, annotation, GIF generation.
- Batch API, background jobs, progress UI, MCP Apps.
- Multi-video comparison, playlists, channels, live streams.
- Docker images, systemd units, hosted deployment, telemetry.
- A project license (deferred until before public release).

---

## 8. Success criteria for v1

1. A new user can install, configure one env var and an allowed directory, and get a correct answer
   about a local video and a public YouTube video.
2. Repeating a question costs **zero** tokens and returns in <100 ms.
3. `extract_frames` lets the agent read on-screen text with no Gemini call.
4. A path outside the allowed roots is refused with an actionable message; a symlink escaping a
   root is refused; a non-video file is refused before upload.
5. A configured RPD limit is respected locally: exhaustion costs zero requests, and no retry storm
   follows.
6. Every paid result reports token usage; a missing key disables only paid tools.
7. The codebase stays reviewable in one sitting (< ~1,500 lines excluding tests).
