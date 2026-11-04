# APIShift

**Contract-driven API migration with Meta's Muse models.** When an upstream API ships a breaking
version, APIShift finds every affected line in your Python codebase, rewrites it, proves the fix
against the new contract with tests it is not allowed to touch, and opens a reviewable pull request.

Built for the Meta AI Developer Hackathon (Oct 27 – Nov 4, 2026). All code in this repo was written
during the event.

## How it works

```
Spec Diff (code) → Scout (Spark) → Analyst (Spark, tool loop) → Refactor (Wonder)
      → Guardrails → Sandbox tests → green? → PR Writer (Spark) → PR
                                    ↓ no
                     Triage (Spark, escalates to Wonder) → back to Refactor or Analyst (max 3 heals)
                                    ↓ still failing
                              draft PR labeled needs-human
```

| Step | Model | Output |
| --- | --- | --- |
| Spec Diff | none (deterministic) | `RawDiff`: every difference between the two OpenAPI specs |
| Scout | `muse-spark-1.3-contributor`, strict JSON | `ChangeManifest`: breaking changes with migration rules, each traced to diff entries |
| Analyst | `muse-spark-1.3-contributor`, tool calling | `ImpactGraph`: affected files plus data-flow edges |
| Refactor | `muse-wonder-1.0` | `PatchSet`: search/replace edit blocks |
| Triage / Validator | Spark, escalating to Wonder | `DiagnosticReport` routed to Refactor or Analyst |
| PR Writer | Spark | PR title and summary; facts rendered from artifacts |

### Why you can trust a green run

Enforced in code, not in prompts (`apishift/tools/guardrails.py`):

- Tests, `conftest.py` and `pytest.ini` are read-only (mounted `:ro` in Docker, fingerprinted before and after).
- Edits outside the impact graph, edits adding `skip`/`xfail`, and paths outside the repo are rejected.
- Every edit's search text must match exactly once, or the whole patch is refused.
- The same number of tests must be collected as on the old API.
- Tests are first run against the old API (must pass) and the new API (must fail) to establish the oracle.

## Quick start

```bash
pip install -r requirements.txt
docker build -t apishift-sandbox:latest .        # sandbox image for running tests
cp .env.example .env && export META_API_KEY=...  # dev.meta.ai key

python -m apishift migrate --demo                 # migrate the bundled weather app (Docker sandbox)
python -m apishift migrate --demo --unsafe-local  # same, without Docker (development only)
python -m apishift dashboard                      # http://127.0.0.1:8000
```

Replay a recorded run with no API calls (tests still run for real):

```bash
python -m apishift migrate --demo --replay runs/<run-id>
```

Your own repo:

```bash
python -m apishift migrate --repo path/to/repo --from v1.yaml --to v2.yaml \
  --changelog CHANGELOG.md --mock-server path/to/mock_server.py [--open-pr]
```

The mock server must accept `--version v1|v2 --port N`, and the repo's tests must read `BASE_URL`.

## Layout

```
apishift/
  main.py            CLI (migrate, dashboard)
  orchestrator.py    state machine: pipeline, heal loop, terminal states
  meta_client.py     Meta /v1/responses client, call recording, replay
  config.py          model routing, prices, limits
  state.py           typed artifacts + strict JSON schema export
  specdiff.py        deterministic OpenAPI diff
  ledger.py          runs/<id>/events.jsonl, artifacts/, calls/
  agents/            scout, analyst, refactor, triage, validator, pr_writer
  tools/             repo_tools (path-jailed), guardrails, sandbox, git_tools, redact
  dashboard/         FastAPI backend (REST + WebSocket) and static UI
demo/
  weather-app/       target repo: src/, tests/ (14 tests), OpenAPI v1/v2, CHANGELOG
  mock_server.py     deterministic v1/v2 mock API
tests_apishift/      APIShift's own tests (a scripted model stand-in exercises the full loop)
Dockerfile           sandbox image
```

## Meta API usage

- `POST /v1/responses` with strict schemas in `text.format` (not `response_format`).
- `prompt_cache_key` and `prompt_cache_retention` on every request; conversation state is managed
  client-side so cached-token telemetry covers the full prefix.
- Contributor Mode (`muse-spark-1.3-contributor`) for all Spark roles; Meta may use those prompts to
  improve its models, so use standard model IDs for private code (`apishift/config.py`).
- Optional open-weights route for the Analyst: `ANALYST_PROVIDER=baseten|together|fireworks`
  (`meta-models/muse-spark-open-1026`).

## Status

- Verified offline: spec diff, guardrails, sandbox (local mode), orchestrator heal loop, replay and the
  dashboard, using a scripted model stand-in (`tests_apishift/`). Run `python -m pytest tests_apishift`.
- Live Meta API runs and the Docker sandbox need to be run with a key and Docker installed.
