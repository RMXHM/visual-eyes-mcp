# visual-eyes-mcp — Technical decisions

**Status:** Phase 1.6 — decisions approved; no application code exists.
**Canonical decision log:** §1. Other documents reference these IDs.
**Research date:** 2026-11-14.

Labels used throughout:

- `[verified]` — confirmed in official documentation or in the pinned SDK's type definitions.
- `[unverified]` — plausible but backend-dependent, or documentation conflict. Must be tested in
  M5 before any document claims it works.
- `[decision]` — a product/architecture choice, independent of provider behaviour.

Type-level acceptance is **not** proof that a combination works. The Interactions API surface is
newer than most published examples, so `[verified]` on a request field only means the SDK accepts
it.

---

## 1. Decision log

### 1.1 Approved

| ID | Decision | Notes / gate |
| --- | --- | --- |
| D1 | Official MCP Python SDK v2, `mcp.server.MCPServer` | Pin `mcp>=2.3,<3`; serves both protocol revisions |
| D2 | `uv` for environment, lockfile, and tooling | **Not installed yet.** Installation requires explicit owner approval (M1) |
| D3 | `requires-python = ">=3.11"`, verify on the owner's 3.14.8 | Floor set by `google-genai` 3.3.0 `[verified]` |
| D4 | TOML config via stdlib `tomllib`, environment overrides | No `pydantic-settings`, no dotenv |
| D5 | Default model `gemini-3.5-flash-lite` | Cheap, video-capable, 500 RPD on the owner's free tier |
| D6 | High-detail model `gemini-3.8-flash` | Used only for `detail = "high"` |
| D7 | Experimental model `gemini-4-hydrogen` | Selectable, never a default; opt-in via config |
| D8 | Model selection configurable; **no personal quotas in public defaults** | Model IDs may ship; limits must not |
| D9 | Quota-aware configuration + local preflight | RPD → token cap → TPM/RPM → concurrency |
| D10 | YouTube URL passthrough is a primary feature, with documented opt-out | `youtube = false` disables URL sources |
| D11 | `store=false` preferred, verified in M5 before relying on it | Fallback documented; local cache unaffected |
| D12 | SQLite for cache/usage; direct `ffmpeg`/`ffprobe` subprocess | No ORM, no bindings, no bundled binaries |
| D13 | License deferred until before public release | Deferred, not forgotten (§1.3) |
| D14 | No GitHub repository, no PyPI/registry publication yet | Owner instruction; M9 builds, M10 revises |
| D15 | Explicit allowed directories; no implicit HOME access | Fail-closed when none resolve; CWD optional dev convenience |
| D16 | No media downloading (no yt-dlp or equivalent) | YouTube stays URL-passthrough only |
| D17 | No HTTP transport in v1 | Local stdio only; remote is designed for, not built |
| D18 | Timestamps are approximate, never frame-exact | Default sampling ~1 FPS; warnings on every paid result |
| D19 | No dollar estimates in tool output | Report tokens and configured limits only |
| D20 | Cache-first with explicit `refresh` | `refresh` is the only path to a forced paid re-run |

D11 is approved *as a preference*, not as an established capability: see U2.

### 1.2 Experimentally unverified

| ID | Claim | Why unverified | Resolved by |
| --- | --- | --- | --- |
| U1 | `start_offset`/`end_offset` clip YouTube URLs | Fields are documented for uploads; YouTube path unstated | M5 |
| U2 | `store=false` works with video / agentic processing | New interaction surface; no example combines them | M5 |
| U3 | Agentic video works on 3.5 Flash-Lite and 3.8 Flash | Video guide lists them; only Hydrogen's model page badges it | M5 |
| U4 | `.mov` mime is `video/mov` or `video/quicktime` | Docs disagree; SDK type enum vs file-input guide | M5 |
| U5 | Video part + `response_format` work together | Both exist in types; combination untested | M5 |
| U6 | Model quality ordering (Hydrogen > 3.8 Flash > 3.5 FL) | Vendor positioning, no benchmark | M5 fixed-clip comparison |
| U7 | Offsets reduce **billed** tokens for local uploads | Token accounting not observed | M5 usage measurement |
| U8 | Free-tier RPD resets at midnight Pacific for every project | Documented, but per-project behaviour varies | Observed via 429 shapes in M5/M7 |

### 1.3 Deferred

| Item | Defer until | Consequence now |
| --- | --- | --- |
| Project license | Before any public release (M10 gate) | No `LICENSE` file; README says "license pending" |
| GitHub owner / registry namespace (`io.github.<owner>/visual-eyes`) | Owner supplies it (blocks M9 registry metadata) | `server.json` name is a placeholder; no repo created |
| PyPI name reservation (`visual-eyes-mcp`, `visual-eyes`) | Optional, after license | Names were unclaimed on 2026-11-14 (HTTP 404); not reserved |
| Build backend: `hatchling` vs `uv_build` | M1, once `uv` is installed | Both work; pick one and document it |
| Streamable HTTP, auth, hosted quotas | After v0.1, if ever | Not implemented; `ARCHITECTURE.md` §9 keeps the seam |
| Additional providers | After a second provider is actually wanted | No ABC/registry (§10.1) |
| Reserved names on TestPyPI | Optional before M9 dry run | Not done |

### 1.4 Rejected

| Option | Why |
| --- | --- |
| Standalone FastMCP 4.2.0 | Extra layers; different wire engine; features we do not need (§2) |
| `yt-dlp` / any downloader | Terms-of-service and redistribution exposure; URL passthrough avoids it |
| Inline base64 video as the main path | Duplicates bytes per request; tiny size ceiling |
| GCS registration / generic HTTPS / HLS | Auth, egress, trust cost; no MVP need |
| Redis / Postgres / Celery / Docker | Infrastructure without a demonstrated need |
| Provider ABC + registry + entry points | Abstraction theatre for one provider (§10.1) |
| Per-call `model` parameter exposed to the agent | Invites expensive choices; breaks cache predictability |
| Hardcoded quota values in shipped defaults | Wrong for every other user; violates D8 |
| Dollar estimates in results | Guaranteed to go stale; pricing changes 2026-12-01/2027-01-01 |
| Frame-perfect timestamp claims | Sampling cannot support the claim |

---

## 2. MCP framework: official SDK vs standalone FastMCP

### 2.1 What the options are

| | Official MCP Python SDK | FastMCP (standalone) |
| --- | --- | --- |
| Package | `mcp` **2.3.0** (2026-10-02) | `fastmcp` **4.2.0** (2026-11-13) |
| High-level API | `MCPServer` (renamed from `FastMCP` in v2) | `FastMCP` |
| Protocol revisions | Both 2025-11-25 and 2026-07-28 from one server `[verified]` | Own engine |
| Extra runtime weight | `mcp-types`, `pydantic`, `anyio`, `httpx2`, `starlette`, `jsonschema`, `pyjwt`, `opentelemetry-api` | `rich`, `pydantic-settings`, `python-dotenv`, `platformdirs` |
| Differentiators | Reference implementation, in-memory test client | Apps/UI, gateway, proxy, OpenAPI, auth backends |

Both are current; this is not abandoned-vs-maintained.

### 2.2 Why the official SDK

1. **Fewest layers** for a stdio server: schema generation, structured output, transport, and an
   in-memory client are all we need.
2. **Protocol alignment**: the reference implementation is where the 2026-07-28 changes land
   first; a server meant to work in many clients should sit closest to the spec.
3. **Debuggability**: one project owns the SDK, the client, and the spec; upstream reports go to
   the right place.
4. **No duplicated ecosystem**: FastMCP 4 no longer depends on `mcp`, so its wire behaviour is a
   third-party reimplementation. Acceptable in general; not a bet this project needs.

### 2.3 Cost accepted

- ~40 lines of settings loading instead of `pydantic-settings`.
- No `.env` autoloading (env vars come from the MCP client config or the shell — better hygiene).
- Must track SDK v2 renames (`MCPServer`, snake_case fields, transport options). Pin `<3`.

---

## 3. Runtime and development environment

- **Python `>=3.11`** `[decision]`: `google-genai` 3.3.0 declares `>=3.11` `[verified]`; `mcp`
  2.3.0 declares `>=3.10` with classifiers through 3.14 `[verified]`.
- **Owner environment:** Python **3.14.8**, ffmpeg **9.0.2**, SQLite **3.54.0** `[verified]`.
  M1 runs a matrix on 3.14.8; CI (later) adds 3.11 and 3.12.
- **`uv` is approved but not installed** `[decision]`. Arch ships `extra/uv 0.13.8`; installation
  is a separate approval step. Until then, documents describe the `uv` workflow without running
  it.
- Local tooling: `pytest`, `ruff`; optional `pyright`. No `poetry`, no `pipenv`, no tox.

---

## 4. Configuration model

**D4.** One TOML file parsed by stdlib `tomllib`, with environment variables overriding file
values. Precedence: **environment > TOML > built-in defaults**.

Resolution order for the file:

1. `VISUAL_EYES_CONFIG` (absolute path),
2. `$XDG_CONFIG_HOME/visual-eyes-mcp/config.toml`,
3. `~/.config/visual-eyes-mcp/config.toml`.

A missing file is not an error: built-in defaults apply (no quotas, no extra allowed roots). An
unreadable or malformed file is a `ConfigError` naming the file and key — paid tools fail, local
tools still work where possible.

### 4.1 Keys

| Section | Keys | Notes |
| --- | --- | --- |
| `[models]` | `default`, `high`, `experimental` | Defaults 3.5 FL / 3.8 Flash / 4 Hydrogen; empty `high` disables `detail="high"` |
| `[paths]` | `allowed_dirs` (list of absolute paths), `include_cwd` (bool, default `true`) | §11 |
| `[limits]` | `max_video_seconds`, `max_input_tokens_per_call`, `max_file_size_mb`, `max_frames_per_call`, `max_frame_width`, `max_concurrent_calls`, timeouts, `max_cache_mb` | Scalars |
| `[limits."<model-id>"]` | `rpm`, `rpd`, `tpm` | **Local only**; absent means unthrottled |
| `[privacy]` | `store_interactions` (default `false`) | D11 |
| `[features]` | `youtube` (default `true`) | D10 opt-out |
| `[cache]` | `data_dir`, `ttl_seconds`, `max_mb` | Default `${XDG_CACHE_HOME:-~/.cache}/visual-eyes-mcp` |

### 4.2 Environment overrides

`GEMINI_API_KEY` / `GOOGLE_API_KEY` (secret, env only, never TOML), `VISUAL_EYES_CONFIG`,
`VISUAL_EYES_MODEL`, `VISUAL_EYES_MODEL_HIGH`, `VISUAL_EYES_MODEL_EXPERIMENTAL`,
`VISUAL_EYES_ALLOWED_DIRS` (`:`-separated), `VISUAL_EYES_INCLUDE_CWD`, `VISUAL_EYES_YOUTUBE`,
`VISUAL_EYES_STORE_INTERACTIONS`, `VISUAL_EYES_MAX_VIDEO_SECONDS`,
`VISUAL_EYES_MAX_INPUT_TOKENS_PER_CALL`, `VISUAL_EYES_MAX_FILE_SIZE_MB`,
`VISUAL_EYES_MAX_CONCURRENT_CALLS`, `VISUAL_EYES_DATA_DIR`.

Per-model quotas are **TOML-only**: encoding nested per-model values in env names produces
unreadable strings, and quotas are structured data, not secrets. If the API key were ever placed
in TOML, the loader rejects the file with a pointed error.

### 4.3 Validation behaviour

- Known keys are type/range checked; a bad value fails startup for paid features with the key name.
- Unknown keys produce a startup warning on stderr and are ignored (forward compatibility).
- Relative `allowed_dirs` entries are rejected — an allowlist anchored to the process CWD is a
  confused-deputy bug.
- The API key is a masked type: `repr`/`str` return `***`; only the adapter sees the value.
- The startup banner (stderr) reports counts and flags, never values: `allowed_dirs=2`,
  `api_key_present=true`, `youtube=true`, `quotas_configured=1`.

---

## 5. Model strategy

**D5–D8.**

| Tier | Model | Role | Input/1M | Output/1M |
| --- | --- | --- | --- | --- |
| Default | `gemini-3.5-flash-lite` | `detail=low\|normal`; bulk work | $0.30 | $2.50 |
| High | `gemini-3.8-flash` | `detail=high`; ambiguous visual reasoning | $0.75 (through 2026-12-31) | $3.75 |
| Experimental | `gemini-4-hydrogen` | Opt-in only, never a shipped default | $1.50 | $7.50 |

All three are video-capable with 1M context `[verified for model capability]`; the **agentic
video** mode is `[unverified]` on the two Flash tiers (U3).

### 5.1 `detail` mapping

| Level | Model | Processing | Intent |
| --- | --- | --- | --- |
| `low` | `[models].default` | static, reduced fps where supported | cheap triage |
| `normal` | `[models].default` | static, default sampling | default |
| `high` | `[models].high` | static; agentic only if U3 resolves positively | ambiguous reasoning |

The agent cannot name a model directly. It chooses `detail`; configuration chooses models. This
keeps cache keys predictable and prevents `gemini-4-carbon` from appearing in a tool call because
it sounded better.

### 5.2 Why not Hydrogen as the default

Hydrogen costs 5× Flash-Lite input and 3× output, with 20 RPD on the owner's tier. A default that
exhausts a day's quota in twenty questions is hostile. Start cheap, escalate deliberately.

### 5.3 Quality is not proven

Vendor framing (flagship, pro, lite) is positional marketing, not a benchmark. M5 runs the same
fixed clip and question set through all three tiers and records differences in a table. The
default may change if the result is decisive; until then, no doc claims a quality ordering.

---

## 6. Video input strategy

**D12, D16.** Documented input methods `[verified]`: Files API, GCS registration, inline data,
public YouTube URLs, public/pre-signed HTTPS URLs (15 MB/file).

| Source | v1 | Why not the alternative |
| --- | --- | --- |
| Local file | Files API; upload once, reuse URI until its 48 h expiry | Inline base64 duplicates the file per request and has a small ceiling |
| Public YouTube | URL passthrough as a video part | Downloading breaks the ToS posture and adds a fragile dependency |
| Other URLs / GCS / HLS | Rejected with an actionable message | Auth, egress, and trust cost; no MVP need |

Mechanics that must be implemented:

- Poll `files.get` until `ACTIVE`; timeout → `TransientError`; `FAILED` → `ProviderError`.
- Cache `name`/`uri` with `expires_at`; expiry triggers re-upload, never a 404 at call time.
- Accepted mimes `[verified]`: mp4, mpeg, mov, avi, x-flv, mpg, webm, wmv, 3gpp. MKV is rejected
  with a "remux to mp4" message. `.mov` naming is ambiguous (U4): probe, normalise, prefer mp4.
- Video part before the text prompt `[verified recommendation]`.
- `processing: {type: "static", start_offset, end_offset, fps}` exists `[verified type-level]` with
  duration strings such as `"1200s"`. Effects are U1/U7.
- `store=false` is sent by default (D11); U2 decides whether it survives contact with video.

### 6.1 Range requests

For local uploads, pass offsets when the range is valid and let the API decide; verify token
reduction in M5 (U7). For YouTube, do **not** rely on offsets (U1). Instead:

1. send the full-video prompt with an explicit time window and instructions to answer only within
   it, and
2. attach a warning: "the full video was processed; timestamps outside the requested range may
   appear and are discarded".

If M5 shows offsets work, remove the warning and document the behaviour. If they do not, the
estimated-token cap may refuse long-video range queries — refusing is better than silently billing
a whole hour.

### 6.2 No downloading

Nothing in this project fetches media. `extract_frames` is local-file-only; YouTube frame
extraction is out of scope because it would require downloading. That is a feature boundary, not
a missing capability to be worked around later.

---

## 7. YouTube posture

**D10.** YouTube is a primary source; the server sends the canonical URL and lets Gemini fetch it.

- Accepted hosts only: `youtube.com`, `www.youtube.com`, `m.youtube.com`, `youtu.be`. No
  userinfo, no ports, no IDN lookalikes, no `http` upgrade surprises.
- Extract `[A-Za-z0-9_-]{11}` and **rebuild** `https://www.youtube.com/watch?v=<id>`; never forward
  the user's string. Playlists, channels, live URLs, and `list=` parameters are rejected or
  dropped explicitly (documented either way).
- Public videos only; private/unlisted/age-gated fail with a message that names the cause, not a
  provider stack trace.
- Opt-out: `[features] youtube = false` (or `VISUAL_EYES_YOUTUBE=false`) makes every URL source a
  validation error explaining that YouTube support is disabled. Local files keep working.
- Preview status: pricing/availability may change without notice; the 8 h/day free-tier upload
  cap is `[verified]` and is not something the server can measure per account. Document it.

---

## 8. FFmpeg integration

**D12.**

| Option | Verdict |
| --- | --- |
| `ffmpeg-python` / `python-ffmpeg` | Another dependency over the same binary |
| PyAV / OpenCV | Heavy wheels and decoding code we do not need |
| **Direct subprocess** | Zero Python deps, exact control, easy to test |

Invocation rules:

- argv list, never `shell=True`; `-nostdin -hide_banner -loglevel error`; numeric coercion for
  every interpolated value.
- `ffprobe -show_format -show_streams -of json`, timeout 20 s.
- Frames: `-ss <t> -i <file> -frames:v 1 -vf scale=<w>:-2 -q:v 3 <tmp>.jpg`, timeout 30 s.
- Local inputs only, `-protocol_whitelist file`; no network protocols, ever.
- stderr captured, truncated, and path-scrubbed before it reaches a tool result.
- Binary discovery at startup via `shutil.which`; missing ffmpeg produces an install hint
  (`sudo pacman -S ffmpeg` on Arch) and disables local visual tools, not the server.
- No bundled ffmpeg, no Python fallback decoder, no memory-safety claims. The call is bounded,
  which is the actual control.

---

## 9. Cache and quota accounting

**D12, D20.** Stdlib `sqlite3`, WAL, `busy_timeout`, single writer discipline.

| Concern | Decision |
| --- | --- |
| Cache key | Hash of source fingerprint, tool, normalised params, resolved model, detail, `PROMPT_VERSION`, `SCHEMA_VERSION` |
| TTL | Analyses 7 days, uploads 48 h, frames 30 days idle — all configurable |
| Invalidation | Prompt/schema bump invalidates; `refresh: true` bypasses reads only |
| Stored content | Validated JSON results and derived frames only; **never video bytes** |
| Usage counters | `usage_counters(pacific_date, model, requests, input_tokens, output_tokens)` |
| Reset boundary | `America/Los_Angeles` via `zoneinfo`; UTC fallback plus a warning if tzdata is missing |
| Atomicity | Result + usage written in one transaction after a successful call |
| Permissions | DB `0600` inside a `0700` directory |

### 9.1 Preflight order

1. RPD for the resolved model and Pacific date.
2. Per-call estimated-token cap (duration × ~100 tok/s, or the configured cap when duration is
   unknown).
3. TPM/RPM sliding window over the last 60 s, including in-flight reservations.
4. `max_concurrent_calls`.

Reservations are estimated before the call and reconciled with actual usage after it; with
concurrency 1 the accounting stays simple. Cached results are returned **before** any of this, so
quota exhaustion never blocks a cache hit.

### 9.2 Why RPD is never retried

A 429 caused by a daily quota is not transient: retrying spends time and may deepen a rate-limit
penalty. The adapter inspects the error's quota metric identifier; daily-quota errors map to
`QuotaExhausted` (no retry, reset window stated). If the identifier is absent or ambiguous, the
call is **not** retried and the message says the cause was not distinguishable. This trades a
little resilience for predictability — the right trade for a free tier.

---

## 10. Dependency set

| Package | Range | Why | Without it |
| --- | --- | --- | --- |
| `mcp` | `>=2.3,<3` | Server + in-memory test client | — |
| `google-genai` | `>=3.3,<4` | Files API, `interactions.create`, structured output | Hand-rolled HTTP client |
| `pydantic` | `>=2.12,<3` | Tool/result schemas, config validation | Hand-written JSON Schema |

Not added: `pydantic-settings`, `python-dotenv`, `rich`, `typer`/`click`, `requests`, `jinja2`,
`tenacity`, `yt-dlp`, `opencv-python`, `av`, `Pillow`, `numpy`, `loguru`. `httpx` arrives
transitively if needed.

Dev: `pytest`, `ruff`, optional `pyright`. System: `ffmpeg` + `ffprobe` (present). No `tomli`
backport — `tomllib` is stdlib at 3.11+.

### 10.1 Provider seam

| Option | Assessment |
| --- | --- |
| ABC + registry + plugin entry points | Solves a problem we do not have; forces every capability through a lowest-common-denominator interface |
| Direct SDK calls inside tools | Provider concepts (file handles, usage, safety blocks) leak into the tool layer and every test |
| **One provider-neutral model layer + one adapter module** | Small, testable, replaceable; no ceremony |

**Decision:** `models.py` defines `VideoAnswer`, `Moment`, `FrameRef`, `Usage`. `analysis.py`
builds provider-neutral requests and validates results. `gemini.py` is the only module importing
`google.genai`. A second provider later means a second adapter plus one factory branch — not an
architecture rewrite. Do not add the branch until a second provider exists.

---

## 11. Security decisions

**D15.** Full policy in `ARCHITECTURE.md` §7; the enforceable decisions:

| # | Rule |
| --- | --- |
| S1 | No unrestricted filesystem access. Roots come from `paths.allowed_dirs`; CWD only as an explicit dev convenience (`include_cwd`, default true) |
| S2 | If no root resolves, local tools fail closed with configuration guidance |
| S3 | Resolve paths (`strict=True`), then containment-check via `commonpath`; reject `..` escapes |
| S4 | Symlinks are checked after resolution — an escape is rejected; dangling links and links to non-regular files are rejected |
| S5 | `stat.S_ISREG` only; extension allowlist **and** ffprobe-confirmed video stream |
| S6 | ffprobe validation before any upload: container, stream, duration, dimensions, fps, size |
| S7 | Uploads happen only in `analyze_video` / `find_moments`; tool description, server instructions, README, and an INFO log line say so |
| S8 | Enforce file-size, duration, frame-count, token, and processing-time limits |
| S9 | Never log API keys, auth headers, or raw request/response bodies; redaction filter plus masked types |
| S10 | Secrets live in env only; the loader rejects a TOML file containing key material |
| S11 | Same-user TOCTOU is accepted and documented: this is not a sandbox |

These are test obligations, not aspirations: M8 owns the adversarial cases (traversal, symlink
escape, FIFO, oversized file, extension spoofing, no-roots mode, log redaction).

---

## 12. Packaging and distribution

**D2, D13, D14.**

| Decision | Choice |
| --- | --- |
| Layout | `src/visual_eyes_mcp/` |
| Build backend | `hatchling` or `uv_build` — pick one in M1 |
| Distribution name / import | `visual-eyes-mcp` / `visual_eyes_mcp` |
| Console script | `visual-eyes-mcp` → `visual_eyes_mcp.__main__:main` |
| Python floor | `>=3.11`; verified against 3.14.8 |
| Dependency spec | Upper-bounded ranges |
| Install | `uv tool install visual-eyes-mcp` (fallback `pipx`) |
| Client config | `{"command": "uvx", "args": ["visual-eyes-mcp"], "env": {...}}` |
| License | **Absent until M10**; README states "license pending" |
| Registry metadata | `server.json` prepared in M9, validated only — not published |
| stdio logging | stderr only; a stray stdout write corrupts the protocol stream |

### 12.1 Registry notes (for later, not now)

The MCP Registry hosts metadata only. Publishing needs a public package, an ownership marker
(`mcp-name: io.github.<owner>/visual-eyes`) in the published README, and an owner-approved
`mcp-publisher publish`. `description` is capped at 100 characters; the schema URL is currently
`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`. The GitHub
namespace is **not yet supplied**, so `server.json` uses a placeholder and M9 validates structure
only.

PyPI availability was checked on 2026-11-14: `visual-eyes-mcp` and `visual-eyes` both returned
404 `[verified]`. Availability is not reservation; names can be taken at any time.

---

## 13. Cost model

Documented tokenisation (static, default): ~66 tokens/frame at low media resolution (258 at high)
plus ~32 tokens/s audio plus metadata ≈ **~100 tokens per second** at low resolution, ~300 at
high. Thinking tokens bill as output.

Arithmetic only, not vendor quotes:

| Video | Approx. input tokens | 3.5 FL input | 3.8 Flash input | Hydrogen input |
| --- | --- | --- | --- | --- |
| 1 min | ~6k | ~$0.002 | ~$0.005 | ~$0.009 |
| 10 min | ~60k | ~$0.018 | ~$0.045 | ~$0.09 |
| 1 h | ~360k | ~$0.11 | ~$0.27 | ~$0.54 |

High media resolution ≈3×. Agentic mode trades input tokens for thought/tool-use tokens
(documented up to 88% fewer for long content; `[unverified]` by us). Prices change on
2026-12-01/2027-01-01 for several models — one more reason the server shows tokens, not dollars
(D19).

**Owner free-tier dashboard (local example only, never shipped):**

| Model | RPM | TPM | RPD |
| --- | --- | --- | --- |
| `gemini-3.5-flash-lite` | 15 | 250,000 | 500 |
| `gemini-3.1-flash-lite` | 15 | 250,000 | 500 |
| `gemini-3.8-flash` | 5 | 250,000 | 20 |
| `gemini-4-hydrogen` | 5 | 250,000 | 20 |
| `gemini-2.5-flash` | 5 | 250,000 | 20 |
| `gemini-2.5-flash-lite` | 10 | 250,000 | 20 |

These values belong in the user's TOML. The public package ships zero quota entries and therefore
throttles nothing; documentation explains how to read your own dashboard. TPM binds before RPM
for long videos: 250K ÷ 60K ≈ **4 requests/minute** for 10-minute videos, which is why preflight
counts tokens, not just requests.

---

## 14. Error handling and retries

| Failure | Behaviour |
| --- | --- |
| Missing API key | Paid tools fail fast; local tools unaffected |
| Bad TOML / no key / no roots | `ConfigError` or `AccessDenied` with the exact key/root to fix; server still starts |
| 400/403/404 (bad source, unsupported mime, safety) | No retry |
| 429 daily quota (RPD) | **No retry**; reset window stated (`QuotaExhausted`) |
| 429 RPM/TPM | ≤2 retries, exponential + jitter, honour `Retry-After`, inside the deadline |
| 5xx / reset | ≤2 retries, same budget |
| Structured-output parse failure | One re-ask with the validation error; then fail |
| Timeout | Abort at the deadline; never leave the MCP call open |
| Upload stuck / `FAILED` | Timeout or `ProviderError`; no silent fallback to inline data |
| ffmpeg/ffprobe failure | Truncated, path-scrubbed stderr + conversion hint |

Never retry a request that produced a billed response. Log token usage for every paid call at
INFO. `QuotaExhausted` and `AccessDenied` are deliberately distinguishable so the agent can tell
"come back tomorrow" from "fix your config".

---

## 15. Remaining owner input

Blocking:

1. **`uv` installation approval** before M1 can run its toolchain (`pacman -S uv`).
2. **License** before M10; blocks public release, not development.
3. **GitHub owner/namespace** before M9 registry metadata; no repository is created until asked.

Review triggers (decided, but expected to be revisited with evidence):

4. **Default model** — D5 selects 3.5 Flash-Lite; M5's comparison may reopen it.
5. **YouTube posture** — D10 keeps URL passthrough on with an opt-out; revisit if preview limits
   bite during dogfooding.

---

## Appendix A — Verification snapshot (2026-11-14)

**Runtimes**

| Item | Observed |
| --- | --- |
| `mcp` 2.3.0 | `requires-python >=3.10`; classifiers 3.10–3.14; conditional `anyio>=4.10` / `starlette>=0.48` on 3.14 |
| `google-genai` 3.3.0 | `requires-python >=3.11`; classifiers through 3.15 |
| `pydantic` 2.14.3 | current |
| Local | Python 3.14.8, ffmpeg 9.0.2, SQLite 3.54.0 |
| Local gaps | no `uv`, no `pipx`; `pip` present |

**SDK surface**

- `client.interactions.create` exists; `CreateModelInteractionParam` includes `response_format`,
  `background`, `previous_interaction_id`, `store`, `stream`.
- `VideoContentParam` includes `uri`, `data`, `mime_type`, `processing`, `resolution`.
- `StaticMediaProcessing` includes `start_offset`, `end_offset`, `fps`; durations are strings
  (`"1200s"`).
- `Interaction` exposes `output_text`, `usage`, `id`, `status`.
- Usage fields include `total_input_tokens`, `total_output_tokens`, `total_thought_tokens`,
  `total_tool_use_tokens`, `input_tokens_by_modality`.
- `TextResponseFormat` uses field `schema_` with alias `schema`; pass dicts as the docs show.

## Appendix B — Sources

- MCP Python SDK repository and "What's new in v2" documentation.
- MCP Registry quickstart and current `server.schema.json`.
- Gemini API: video understanding, Files API, structured outputs, models, pricing, rate limits.
- PyPI metadata for `mcp` 2.3.0, `google-genai` 3.3.0, `fastmcp` 4.2.0.
- FastMCP documentation (for the comparison only).
