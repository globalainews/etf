# Candidate Pipeline Migration

## Baseline

- Started from `tools/prepare_candidates.py` at 4,808 lines.
- Existing offline suite: 23 tests, all passing.
- Pre-existing worktree changes are preserved in `tests/candidate_pipeline/test_legacy_regression.py` and the `tools/gemini-skill` submodule.

## Completed

| Stage | Status | Actual migration |
| --- | --- | --- |
| Publication time | Complete | Base windows, HTML probe, Tavily hints, US date evidence, URL slug expiry, body parsing and Deep evidence merge are implemented in `publication_time.py`; duplicate entrypoint implementations were deleted. |
| Discovery | Complete | Tavily client/cache, query normalization, URL recovery, search-item merge and candidate construction are implemented in `discovery.py`. |
| Extractor | Complete | Trafilatura extraction, Hermes batching/retries, cache accounting and Reuters fallback search/extraction are implemented in `extractor.py`. |
| Policies | Complete | Source, URL, title, stale-event, political and editorial deterministic rules are implemented in `policies.py`. |
| LLM / Dedup / Selection | Complete | Qwen triage/classification/repair, deterministic and semantic dedup, Deep/Flash quotas and expansion decisions are implemented in their respective modules. |
| Deep / Flash | Complete | `DeepPipeline` and `FlashPipeline` use the shared services; Flash preparation, title classification and final Deep-wins selection are local implementations. |
| Output / Reporting | Complete | CP1 v2 projection, atomic qualified cache/output, validation and final reporting are implemented outside the CLI. |
| Orchestrator / CLI | Complete | The former `_legacy_main` now lives in `orchestrator.py`; `prepare_candidates.py` is a 128-line CLI and compatibility-export surface. |

## Validation

`python -m unittest discover -s tests/candidate_pipeline -p "test_*.py"` passed 23 tests before the remaining migration started. The final offline suite contains 44 tests (including extraction and orchestrator contracts). `compileall`, CLI help, direct imports, reverse-import scan and `git diff --check` are run as final acceptance. The entrypoint fell from 4,808 lines to 128 lines; all removed entrypoint functions are now either implemented in their responsible module or exposed as thin compatibility imports.
