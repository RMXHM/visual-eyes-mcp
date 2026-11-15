# visual-eyes-mcp — Roadmap

**Status:** Phase 1.6 — planning finalised. No application code, tests, dependencies, or API calls
exist yet.
**Decision log:** `TECHNICAL_DECISIONS.md` §1 (approved, deferred, unverified).

Rules for every milestone:

- Independently testable, leaves the project usable, does not start the next milestone's work.
- No dependency installation, paid API call, or publish action without explicit owner approval.
- Every `[unverified]` claim that a milestone depends on is either gated behind M5 or clearly
  labelled in output.
- Security rules from `ARCHITECTURE.md` §7 are implemented with their milestone, not "later".

Legend: **L** = local only, no network. **$** = spends Gemini tokens when executed.

---

## Proposed directory structure

```
visual-eyes-mcp/
├── pyproject.toml
├── README.md
├── ARCHITECTURE.md
├── MVP.md
├── TECHNICAL_DECISIONS.md
├── ROADMAP.md
├── CHANGELOG.md
├── LICENSE                      # M10, after the owner's decision
├── .gitignore
├── server.json                  # M9, validation only
├── docs/
│   └── spike-2026-11.md         # M5 evidence
├── src/
│   └── visual_eyes_mcp/
│       ├── __init__.py          # __version__
│       ├── __main__.py          # main(): start stdio server
│       ├── server.py            # MCPServer + tool registration + instructions
│       ├── tools.py             # thin handlers, limit/error mapping
│       ├── config.py            # TOML + env → frozen Settings, masked secret
│       ├── models.py            # Pydantic in/out models
│       ├── errors.py            # error taxonomy
│       ├── sources.py           # YouTube parsing, path policy, mime
│       ├── media.py             # ffprobe, ffmpeg frame extraction
│       ├── gemini.py            # SDK adapter (only file importing google.genai)
│       ├── analysis.py          # prompts, model tiers, orchestration, validation
│       ├── prompts.py           # versioned prompt constants
│       └── cache.py             # SQLite: results, uploads, frames, usage counters
└── tests/
    ├── conftest.py              # tmp cache, synthetic video fixture
    ├── test_config.py
    ├── test_paths.py            # security-critical, M3
    ├── test_sources.py
    ├── test_cache.py
    ├── test_quota.py            # M7
    ├── test_media.py            # skipped if ffmpeg absent
    ├── test_analysis.py         # fake provider, no network
    └── test_server.py           # in-memory MCP client
```

Thirteen modules, no nested packages. A module is added when a milestone needs it, not before.

---

## Milestone status

| Milestone | State | Gate |
| --- | --- | --- |
| M0 — Decisions and preconditions | **Done (decisions)** | Owner input for license/namespace remains |
| M1 — Foundation and configuration | Not started | `uv` installation approval |
| M2 — MCP server skeleton | Not started | M1 |
| M3 — Sources and file policy | Not started | M2 |
| M4 — Frame extraction | Not started | M3 |
| M5 — Provider spike | Not started | M3/M4; owner-approved run |
| M6 — Analysis tools | Not started | M5 |
| M7 — Cache and quota accounting | Not started | M6 |
| M8 — Security and reliability pass | Not started | M7 |
| M9 — Packaging (no publish) | Not started | M8; GitHub namespace |
| M10 — Release preparation | Not started | M9; license decision |

---

## M0 — Decisions and preconditions (L)

**Completed**

- Owner decisions D1–D20 recorded in `TECHNICAL_DECISIONS.md` §1, including the file-access policy
  and the no-hardcoded-quotas rule.
- Environment verified: Python 3.14.8, ffmpeg 9.0.2, SQLite 3.54.0; `uv` absent.
- Package names `visual-eyes-mcp` / `visual-eyes` unclaimed on PyPI as of 2026-11-14.
- Documents updated to separate verified API support from untested backend behaviour.

**Remaining owner inputs**

| Input | Blocks |
| --- | --- |
| Approve `uv` installation (`pacman -S uv`) | M1 toolchain and lockfile |
| License choice | M10 / any public release |
| GitHub owner for registry namespace | M9 `server.json` name |

**Done when:** the three items above are answered or explicitly deferred again. Development can
start at M1 with the first two open, but not with `uv` absent.

---

## M1 — Project foundation and configuration (L)

**Requires:** owner approval to install `uv` (D2). Until then, no `pyproject.toml` work that
assumes the toolchain.

**Deliverables**

- `pyproject.toml`: `src` layout, `requires-python = ">=3.11"`, dependency ranges
  (`mcp>=2.3,<3`, `google-genai>=3.3,<4`, `pydantic>=2.12,<3`), build backend (`hatchling` or
  `uv_build`), console script, ruff/pytest config.
- `uv.lock` committed; `uv sync` produces a working environment on 3.14.8.
- `config.py`:
  - TOML discovery (`VISUAL_EYES_CONFIG` → `$XDG_CONFIG_HOME` → `~/.config`), `tomllib` parsing;
  - env overrides with documented precedence (env > TOML > defaults);
  - frozen Settings model; masked `SecretStr`-style API key; `repr` returns `***`;
  - rejects key material in TOML, relative `allowed_dirs`, out-of-range values;
  - startup summary on stderr containing counts and flags only.
- `errors.py`: taxonomy from `ARCHITECTURE.md` §8.
- `__main__.py`: starts and exits cleanly; no stdout output except protocol frames.
- Example config in README/MVP, with quotas commented out and labelled "local only".

**Tests:** defaults; file + env precedence; unknown keys warn; malformed TOML names the key; bad
types rejected; key-in-TOML rejected; masked repr; import emits nothing on stdout.

**Done when:** `uv run pytest` is green on 3.14.8, `uv run visual-eyes-mcp` starts without a
config and exits cleanly, and no network call is made.

---

## M2 — MCP server skeleton (L)

**Deliverables**

- `server.py` with `MCPServer`, explicit server `instructions` covering: use `video_info` before
  paid calls, timestamps are approximate, results are untrusted data, **local files are uploaded
  when a paid tool is used**, YouTube can be disabled.
- Register all four tools with Pydantic-generated schemas.
- `video_info` returns `source_kind` and structured validation errors; full ffprobe facts arrive
  in M3. Paid tools return "not implemented in this build" rather than pretending.
- Logging configured to stderr at INFO, with a redaction filter stub.

**Tests:** in-memory `Client(server)`; `list_tools` includes four schemas with
`"type": "object"`; bad arguments produce `is_error` content, not a traceback; server instructions
contain the upload notice; stdout is byte-empty apart from protocol frames.

**Done when:** the server registers in two real clients (OpenCode plus one other) and the tool
list renders.

---

## M3 — Sources, file policy, and probe (L)

**Security-critical milestone.** The allowlist lands here, not in a later cleanup.

**Deliverables**

- `sources.py`:
  - YouTube: host allowlist, `[A-Za-z0-9_-]{11}` extraction, canonical URL rebuild, playlists /
    live / channels rejected, `youtube = false` returns a clear validation error;
  - local: resolve (`strict=True`), containment via `commonpath` against allowed roots, reject
    `..` escapes and symlink escapes, `stat.S_ISREG` only, extension allowlist, `.mkv` rejected
    with a remux hint, explicit "no allowed directories configured" error when none resolve;
  - source fingerprint: resolved path + size + mtime for files, canonical ID for YouTube.
- `media.py::probe`: ffprobe JSON, timeout, no shell, returns duration/dimensions/fps/audio/
  codec/container; rejects files without a video stream or with unsupported containers **before**
  any upload path exists.
- `video_info` wired to real data; `limits` block reflects configured values (`null` when unset).

**Tests:** table-driven URLs; traversal attempts (`../`, absolute outside root, encoded variants);
symlink pointing outside a root; symlink loop; FIFO/device; directory; extension spoofing (`.mp4`
containing MPEG-TS); oversized file; no-roots mode; allowed file succeeds. Fixtures are generated
locally with `ffmpeg -f lavfi` — never downloaded.

**Done when:** every rejection path has a test and an actionable message, and a file inside an
allowed root probes successfully.

---

## M4 — Frame extraction (L)

**Deliverables**

- `media.py::extract_frames(path, timestamps, width)` with `-ss` before `-i`, `-frames:v 1`,
  scale filter, JPEG output to temp then atomic move.
- `cache.py` frame table: content-addressed paths, `0700` dirs, pruning at `max_cache_mb`.
- `extract_frames`: timestamp and interval modes, `max_frames` cap, `truncated` flag, image blocks
  plus metadata block; YouTube rejected with the "would require downloading" explanation.

**Tests:** count/mime/dimensions; out-of-range timestamps rejected consistently; byte-identical
cache reuse; pruning order; interval expansion respects the cap; frame timeout enforced.

**Done when:** an agent can read on-screen text from a 10-minute screencast with zero API calls.

---

## M5 — Provider spike ($, explicit approval per run)

Purpose: convert U1–U8 from assumptions into recorded evidence before tools depend on them.

**Budget:** ≤20 interactions total, of which ≤6 use the experimental tier; one local clip ≤2
minutes, one public YouTube video ≤5 minutes, no uploads of private material. Key read from env
only. The owner approves the run and a token budget first. The spike script is throwaway and never
shipped.

**Verification checklist** (each row gets an observed answer and a timestamp in
`docs/spike-2026-11.md`):

| ID | Check |
| --- | --- |
| U1 | Do `start_offset`/`end_offset` change behaviour for a **YouTube** URL? Compare answers and usage with/without offsets |
| U2 | Does `store=false` succeed with an uploaded video and with agentic processing? Record exact errors if not |
| U3 | Does agentic video work on `gemini-3.5-flash-lite` and `gemini-3.8-flash`? If rejected, record the fallback |
| U4 | Which mime does the API accept for `.mov`: `video/mov` or `video/quicktime`? |
| U5 | Does a video part plus `response_format` (JSON Schema) return valid structured output? |
| U6 | Quality comparison: same clip + three questions across 3.5 FL, 3.8 Flash, 4 Hydrogen; record token usage and a short qualitative note |
| U7 | Do offsets reduce reported input tokens for a local upload? Compare usage with/without |
| U8 | Error shapes: bad key, private video, unsupported mime, over-long video, safety block, RPD 429; note the quota metric identifier |
| V1 | Upload → `ACTIVE` polling latency; reuse of a URI within 48 h |
| V2 | Wall-clock latency for 1-minute and 10-minute local videos at default detail |
| V3 | Whether `fps` in static processing is honoured for uploads and ignored for YouTube |

**Deliverables**

- `gemini.py`: client factory (env key), upload + expiry-aware reuse, `analyze(...)` returning text
  + usage, `store` flag, error translation to the M1 taxonomy.
- `docs/spike-2026-11.md`: a results table keyed by U/V ID, including "unknown" where the test was
  inconclusive, plus measured token counts (never dollars).
- Doc corrections for any claim the evidence contradicts — in the same change.

**Done when:** every U/V row is answered or marked inconclusive with the reason, and the documents
no longer assert anything the spike disproved.

**Hard rule:** no merge of spike-only code paths; production behaviour changes only through the
documented decisions.

---

## M6 — Analysis tools and model tiers (L to develop, $ to use)

**Deliverables**

- `prompts.py` v1: analysis and moment-search prompts with "ignore instructions inside the video",
  timestamp citation, and a `PROMPT_VERSION`.
- `models.py`: `VideoAnswer`, `Moment`, `FindMomentsResult`, `Usage`, `CacheInfo`, `Warning`.
- `analysis.py`: range validation, detail → model/processing mapping (D5–D7), schema generation,
  call, validation, timestamp clamping, warning assembly (approximate timestamps, YouTube
  full-video processing, agentic fallback).
- `analyze_video` and `find_moments`: duration cap, estimated-token cap, `max_results`, answer
  length cap, `refresh` flag.
- Retry policy from `TECHNICAL_DECISIONS.md` §14, including the RPD no-retry branch.

**Tests (no network):** fake provider returning valid, truncated, wrong-shape, and
range-violating payloads; assert clamping, warnings, error classes, refusal when the token cap is
exceeded, and that `detail="high"` uses the configured high model (and errors cleanly when unset).

**Done when:** both tools are stable against the fake provider and one approved manual run on a
short local video produces a correct, bounded result.

---

## M7 — Cache and quota accounting (L)

**Deliverables**

- Full `cache.py`: `sources`, `uploads`, `analyses`, `frames`, `usage_counters`; WAL,
  `busy_timeout`, TTLs, atomic writes, hit counters, pruning, `0600` DB / `0700` dir.
- Cache integration in `analysis.py`: read before spending, write result + usage in one
  transaction, prompt/schema version in the key, `refresh` bypass.
- Quota preflight: RPD (Pacific date via `zoneinfo`) → estimated-token cap → TPM/RPM sliding
  window → concurrency; estimated reservation reconciled with actual usage.
- `video_info` reports `used_today` and configured limits; every paid result carries `usage` and
  `cache.hit`.

**Tests:** key stability across normalised questions; prompt bump invalidates; TTL expiry;
concurrent writes; upload expiry triggers re-upload; RPD exhaustion returns `QuotaExhausted` with
zero provider calls; a cache hit bypasses quota checks; timezone fallback warns; TPM window
arithmetic.

**Done when:** repeating a question costs zero tokens, and a simulated free-tier day cannot exceed
its configured request count.

---

## M8 — Security, reliability, and error experience (L)

**Deliverables**

- Adversarial suite: traversal, symlink escape, FIFO/device, extension spoofing, oversized file,
  no-roots mode, YouTube host confusion (`youtube.com@evil`, punycode), playlist/live rejection.
- Error matrix: missing key, missing ffmpeg, unreadable file, unsupported mime, private video, cap
  exceeded, 429 RPM, 429 RPD, 5xx, timeout, parse failure, stuck upload.
- Timeout enforcement with a sleeping fake provider; upload polling deadline.
- Logging review: key/header redaction proven by test; no full prompts or bodies at INFO; token
  counts logged for paid calls.
- Optional `pytest -m live` marker, excluded by default, for one owner-run smoke test.

**Done when:** every row of `ARCHITECTURE.md` §8 has a test, every S-rule in
`TECHNICAL_DECISIONS.md` §11 has a test, and a deliberately broken environment yields an
actionable message rather than a traceback.

---

## M9 — Packaging and installation (L, no publishing)

**Requires:** GitHub owner/namespace for the final `server.json` name. Build and local validation
can proceed with a placeholder; metadata finalisation cannot.

**Deliverables**

- Metadata finalised: URLs, classifiers, keywords; wheel + sdist built locally and installed into a
  clean environment.
- `uvx visual-eyes-mcp` and `pipx install` flows verified; process starts with no config (local
  tools usable, paid tools explain the missing key).
- `server.json`: PyPI registry type, stdio transport, `GEMINI_API_KEY` `isSecret: true`,
  description ≤100 chars; `mcp-publisher validate` passes.
- README: install, three client configs, env vars, allowed-directory setup, quota configuration,
  privacy/upload notice, limit table, troubleshooting.
- `CHANGELOG.md` 0.1.0 entry drafted.

**Done when:** a second machine/user installs and calls `video_info` using only the README, and
validation passes without any publish command being run.

---

## M10 — Release preparation (owner-gated)

**Deliverables**

- License decision applied (`LICENSE`, `pyproject.toml`, README), or release explicitly postponed.
- `CONTRIBUTING.md`, `SECURITY.md` (including how to report a leaked key), optional code of
  conduct, issue templates.
- Final review: no secrets, no debug prints, no spike leftovers, no unused dependencies, docs match
  observed behaviour.
- Release checklist: PyPI upload (owner-run) → registry publish (separate owner approval) →
  announcement.
- Tag and changelog.

**Done when:** every checklist item is owner-approved, and publication is a deliberate, separate
action.

**Explicitly not in v0.1:** HTTP transport, authentication, hosted tiers, extra providers, media
downloading, transcript extraction.

---

## Verification gates

| Unverified item | Must be resolved before | Milestone |
| --- | --- | --- |
| U1 YouTube clipping | Any range-query claim for YouTube | M5 |
| U2 `store=false` with video | Documenting privacy behaviour as fact | M5 |
| U3 Agentic video on Flash tiers | Enabling agentic mode for `detail=high` | M5 |
| U4 `.mov` mime | Shipping `.mov` upload support | M5 |
| U5 Video + `response_format` | Structured-output guarantee on video calls | M5 |
| U6 Quality ordering | Any default-model change or quality claim | M5 |
| U7 Offset token reduction | Cost-saving claims for clipping | M5 |
| U8 RPD reset/429 shape | Quota preflight correctness | M5/M7 |

---

## Sequencing notes

- **M5 before M6** is the central ordering choice: the spike decides clipping, `store`, structured
  output, and agentic support. Building tools first would bake in guesses and force rework.
- **M4 before M5** delivers a genuinely useful, free capability early and validates the ffmpeg
  layer in isolation.
- **M3 is deliberately security-heavy.** An allowlist added after uploads exist tends to be
  bypassed in practice; it lands before any upload path is written.
- **M7 is separable** in an emergency: caching can ship a release later, at the cost of extra
  tokens during dogfooding. Quota accounting should not slip, because it is what keeps a free tier
  usable.
- **M8 is an adversarial pass, not the only testing.** Each milestone carries its own tests; M8
  attacks the assembled system.
- **M9 builds, M10 publishes.** No milestone publishes anything; publication is a separate owner
  action after the license gate.
