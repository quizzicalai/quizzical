Teesting Plan
# Application structure
This is the FastAPI backend for "Quizzical" which generates buzzfeed quizzes using an AI Agent. 

## Stack:
 - Backend
 -- FastAPI
 -- Redis
 -- Postgres
 - Frontend
  -- React

## Primary endpoints
### Application & Configuration Endpoints
These endpoints handle general application health, documentation, and configuration.

GET /: Redirects the root URL to the API documentation at /docs.

GET /health: Performs a simple database query (SELECT 1) to confirm the service is healthy and connected to the database.

GET /config: Provides a static JSON object containing configuration details for the frontend, such as theme colors, UI text, and feature flags.

### Core Quiz Flow Endpoints
This is the main, asynchronous workflow for a user taking a quiz.

POST /quiz/start: Starts a new quiz session. It takes a category from the user, triggers the AI agent to generate an initial synopsis, saves the state to Redis, and returns a unique quiz_id.

POST /quiz/proceed: Begins the question phase. This signals that the user is ready to answer questions, prompting the AI agent to generate them in a background task.

POST /quiz/next: Submits a user's answer. The answer is recorded in Redis, and the AI agent is triggered in the background to generate the next adaptive question or the final result.

GET /quiz/status/{quiz_id}: Polls for the current quiz state. The frontend uses this endpoint to repeatedly check for the next unseen question or the final result once the AI has finished processing.

### Supporting Endpoints
These endpoints handle feedback, results, and assets.

POST /feedback: Submits user feedback (rating and text) for a completed quiz. This endpoint is protected from bots by Cloudflare Turnstile.

GET /result/{result_id}: Retrieves the final, shareable character profile for a completed quiz from the PostgreSQL database.

GET /character/{character_id}/image: Serves a character's profile image. It uses ETag headers to enable efficient browser caching.

# Application Structure

Excluding tests, the application is defined as:

backend/
 - app/
 -- agent/ <- contains everything needed for agentic workflows
 --- graph.py
 --- prompts.py
 --- schemas.py
 --- state.py
 ---- tools/ <- all agent tools, including some third party tools
 ----- analysis_tools.py; content_creation_tools.py; data_tools.py; image_tools.py: planning_tools.py; utility_tools.py:
 -- api/
 --- dependencies.py
 --- endpoints/
 ---- assets.py
 ---- config.py
 ---- feedback.py
 ---- quiz.py
 ---- results.py
 -- core/
 --- config.py
 --- logging_config.py
 -- models/
 --- api.py
 --- db.py
 -- services/
 --- database.py
 --- llm_service.py <- handles calls to openai
 --- redis_cache.py
 - main.py
 - db/
 - .env
 - appconfig.local.yaml <- used in place of Azure config on local
 - Dockerfile
 - poetry.lock
 - pestry.toml
 - README.md <- currently empty

# Layout
```
repo-root/
├─ pytest.ini
├─ .coveragerc
└─ backend/
   └─ tests/
      ├─ conftest.py
      ├─ README.md
      ├─ fixtures/
      │  ├─ env_fixtures.py
      │  ├─ redis_fixtures.py
      │  ├─ db_fixtures.py
      │  ├─ agent_graph_fixtures.py
      │  ├─ http_client.py
      │  └─ background_tasks.py
      ├─ helpers/
      │  ├─ assertions.py
      │  ├─ state_builders.py
      │  └─ sample_payloads.py
      ├─ data/
      │  ├─ sample_questions.json
      │  ├─ sample_synopsis.json
      │  └─ sample_result.json
      ├─ integration/
      │  ├─ test_health.py
      │  ├─ test_quiz_start.py
      │  ├─ test_quiz_proceed.py
      │  ├─ test_quiz_next.py
      │  ├─ test_quiz_status.py
      │  ├─ test_feedback_endpoint.py
      │  ├─ test_assets_endpoint.py
      │  └─ test_results_endpoint.py
      ├─ unit/
      │  ├─ services/
      │  │  ├─ test_llm_service.py
      │  │  ├─ test_redis_cache_repo.py
      │  │  ├─ test_database_repos.py
      │  │  └─ test_result_normalizer.py
      │  ├─ api/
      │  │  └─ test_dependencies.py
      │  ├─ agent/
      │  │  └─ tools/
      │  │     ├─ test_content_creation_tools.py
      │  │     ├─ test_data_tools.py
      │  │     └─ test_planning_tools.py
      │  ├─ models/
      │  │  └─ test_api_models.py
      │  └─ utils/
      │     └─ test_state_normalizers.py
      └─ smoke/
         └─ test_quiz_happy_path.py
```

## What goes in each file

### repo-root/pytest.ini

* Configure markers: `unit`, `integration`, `smoke`.
* Set `asyncio_mode = auto`.
* Add `filterwarnings` for noisy libs.
* Optional: `addopts = -q --strict-markers --cov=backend/app --cov-report=term-missing`.

### repo-root/.coveragerc

* Include `backend/app/*`.
* Omit `backend/tests/*`, `__main__`, and migrations.
* `fail_under` (e.g., 85–90).

---

### backend/tests/conftest.py (fixtures, not tests)

* `app()` fixture: FastAPI app import + `dependency_overrides` for cache, DB, Turnstile.
* `client(app)` fixture: `httpx.AsyncClient` with lifespan.
* `fake_cache_repo()` fixture: in-mem stub for `CacheRepository`.
* `fake_agent_graph()` fixture: stub with `ainvoke/astream`.
* `no_bg_tasks()` fixture: patch `BackgroundTasks.add_task`.
* `mock_llm()` fixture: patch `litellm.acompletion`.

### backend/tests/README.md

* How to run tests locally, markers, common fixtures.

---

### backend/tests/fixtures/env_fixtures.py

* Fixture that sets env for tests: `APP_ENVIRONMENT=local`, `ENABLE_TURNSTILE=false`, test model names/keys.

### backend/tests/fixtures/redis_fixtures.py

* In-mem `FakeCacheRepository` (get/set/update_atomically, TTL noop).
* Helpers: `seed_quiz_state(quiz_id, state_dict)`.

### backend/tests/fixtures/db_fixtures.py

* AsyncSession stub (returns `None`/`[]` in cache-only MVP).

### backend/tests/fixtures/agent_graph_fixtures.py

* `FakeAgentGraph`: returns canned synopsis/questions/final result depending on state.

### backend/tests/fixtures/http_client.py

* `async_client(app)` factory using `AsyncClient(app=app, base_url=...)`.

### backend/tests/fixtures/background_tasks.py

* Patch for `BackgroundTasks.add_task` that records tasks without running them.

---

### backend/tests/helpers/assertions.py

* `assert_is_uuid(value)`
* `assert_question_shape(obj)`
* `assert_result_shape(obj)`

### backend/tests/helpers/state_builders.py

* Builders for `agent state` dicts:

  * `make_synopsis_state(...)`
  * `make_questions_state(..., baseline_count=N)`
  * `make_finished_state(..., result=...)`

### backend/tests/helpers/sample_payloads.py

* `start_quiz_payload(topic="...")`
* `next_question_payload(quiz_id, index, option_idx=None, freeform=None)`

---

### backend/tests/data/sample_questions.json

* 2–3 normalized questions with options/ids.

### backend/tests/data/sample_synopsis.json

* Minimal synopsis structure your agent expects.

### backend/tests/data/sample_result.json

* Minimal final result structure (title/description/image if used).

---

## Integration tests

### integration/test_health.py

* `test_health_returns_ok_200()`

### integration/test_quiz_start.py

* `test_start_quiz_201_saves_state_and_returns_synopsis_and_first_question()`
  (fake agent returns synopsis + first Q; assert 201, quizId uuid, state saved)
* `test_start_quiz_503_when_agent_produces_no_synopsis()`
  (fake agent returns state without synopsis)
* `test_start_quiz_504_on_internal_timeout()`
  (simulate asyncio.TimeoutError in agent)
* `test_start_quiz_503_on_unhandled_exception()`
  (agent raises generic Exception)

### integration/test_quiz_proceed.py

* `test_proceed_202_marks_ready_and_schedules_background()`
  (state exists; assert `BackgroundTasks.add_task` called)
* `test_proceed_404_when_session_missing()`
  (cache returns None)

### integration/test_quiz_next.py

* `test_next_202_records_answer_and_does_not_schedule_until_baseline_complete()`
  (answer < baseline_count → no add_task)
* `test_next_202_records_answer_and_schedules_when_baseline_complete()`
  (answer == baseline_count → add_task called)
* `test_next_202_duplicate_answer_is_idempotent()`
  (same index resubmitted → still 202, no duplicate)
* `test_next_409_out_of_order_index()`
* `test_next_400_option_index_out_of_range()`
* `test_next_404_when_session_missing()`
* `test_next_409_atomic_update_conflict()`
  (`update_quiz_state_atomically` returns None)

### integration/test_quiz_status.py

* `test_status_200_returns_next_unseen_question_with_active_status()`
  (cache has 2 Qs; client knows 1 → returns Q2)
* `test_status_200_returns_processing_when_no_new_question()`
  (know count == generated count)
* `test_status_200_returns_finished_with_final_result()`
* `test_status_404_when_session_missing()`
* `test_status_500_on_malformed_final_result()`
  (result can’t be normalized/validated)

### integration/test_feedback_endpoint.py

* `test_feedback_204_saved()`
  (mock SessionRepository returns “ok”)
* `test_feedback_404_session_not_found()`
* (optional if Turnstile enabled in prod)
  `test_feedback_401_invalid_turnstile_token()`

### integration/test_assets_endpoint.py

* `test_character_image_200_returns_png_bytes_with_cache_headers()`
  (assert `Content-Type`, `ETag`, `Cache-Control`)
* `test_character_image_304_when_if_none_match_matches_etag()`
* `test_character_image_404_when_missing()`

### integration/test_results_endpoint.py

* `test_results_200_returns_shareable_result()`
  (mock result service/repo to return normalized result)
* `test_results_404_when_missing()`

---

## Unit tests

### unit/services/test_llm_service.py

* `test_get_structured_response_returns_model_instance_on_success()`
  (patch `litellm.acompletion` to yield a model)
* `test_get_text_response_retries_on_transient_then_succeeds()`
  (side_effect: transient error then success; assert call count)
* `test_get_structured_response_raises_structured_output_error_on_validation_error()`
  (simulate pydantic ValidationError)
* `test_tools_and_params_are_passed_through_to_litellm()`
  (assert called with expected messages, temperature, model)

### unit/services/test_redis_cache_repo.py

* `test_save_quiz_state_sets_key_and_ttl()`
* `test_get_quiz_state_returns_none_when_missing()`
* `test_update_quiz_state_atomically_success_path()`
* `test_update_quiz_state_atomically_returns_none_on_conflict()`
  (simulate CAS/watch error)
* `test_key_format_includes_namespace_and_quiz_id()`

### unit/services/test_database_repos.py

* (MVP is cache-only; still lock contracts)
  `test_find_relevant_sessions_for_rag_returns_empty_in_bypass_mode()`
* (prepare for DB later)
  `test_repository_methods_called_with_expected_parameters_when_enabled()`

### unit/services/test_result_normalizer.py

* `test_normalize_result_from_string()`
* `test_normalize_result_from_dict_various_keys()`
  (e.g., `profileTitle` → title; `summary` → description)
* `test_normalize_result_from_pydantic_like_object()`
* `test_normalize_result_returns_none_on_unknown_shape()`

### unit/api/test_dependencies.py

* `test_verify_turnstile_bypass_in_local_env_returns_true()`
* `test_verify_turnstile_401_on_invalid_token_when_enabled()`
* `test_verify_turnstile_500_on_http_client_error()`

### unit/agent/tools/test_content_creation_tools.py

* `test_generate_category_synopsis_calls_llm_and_returns_text()`
* `test_generate_baseline_questions_normalizes_options()`
* `test_generate_adaptive_question_uses_history_and_synopsis()`

### unit/agent/tools/test_data_tools.py

* `test_search_for_contextual_sessions_calls_repo_with_expected_args()`
* `test_search_for_contextual_sessions_returns_list_of_context_items()`

### unit/agent/tools/test_planning_tools.py

* `test_normalize_topic_strips_whitespace_and_lowercases()`
* `test_plan_quiz_sets_expected_baseline_count_default()`

### unit/models/test_api_models.py

* `test_start_quiz_request_model_validation()`
  (e.g., requires topic or category per your schema)
* `test_next_question_request_validates_index_and_option_union()`
* `test_quiz_status_response_variants_roundtrip()`
  (active/processing/finished union types)

### unit/utils/test_state_normalizers.py

* `test_as_payload_dict_removes_internal_fields()`
* `test_character_to_dict_maps_required_fields()`
* `test_to_state_dict_handles_missing_optionals()`

---

## Smoke test

### smoke/test_quiz_happy_path.py

* `test_happy_path_start_to_finish()`
  Start → Proceed → Next (finish baseline) → Status (get next/adaptive) → Status (finished).
  Use `FakeAgentGraph` and `FakeCacheRepository`. Assert *only* key outcomes (201/202/200; status transitions; final result present). Keep it tiny & deterministic.

---

# Sequence:

1. **Repo config**

* `pytest.ini`, `.coveragerc`
  **Goal:** markers, asyncio mode, coverage gates.
  **Pass:** `pytest -q` runs (even with 0 tests).

2. **Core fixtures & client**

* `backend/tests/conftest.py`
* `fixtures/env_fixtures.py`
* `fixtures/http_client.py`
* `fixtures/background_tasks.py`
  **Goal:** FastAPI app fixture with dependency overrides; AsyncClient w/ lifespan; background task patch.
  **Pass:** import works; a no-op test can create the client.

3. **Cache & agent fakes + helpers**

* `fixtures/redis_fixtures.py` (FakeCacheRepository + seed helpers)
* `fixtures/agent_graph_fixtures.py` (FakeAgentGraph)
* `helpers/assertions.py`, `helpers/state_builders.py`, `helpers/sample_payloads.py`
  **Goal:** Deterministic state + simple asserts.
  **Pass:** a tiny unit test can instantiate these.

4. **Sanity integration**

* `integration/test_health.py`
  **Goal:** Prove routing/DI work end-to-end.
  **Pass:** GET `/health` → 200 with expected JSON.

5. **End-to-end smoke (minimal, happy path)**

* `smoke/test_quiz_happy_path.py`
  **Goal:** Start → Proceed → Next → Status (finish) using fakes; no external IO.
  **Pass:** All steps return expected 2xx/200 and final result present.

6. **LLM service (critical unit)**

* `unit/services/test_llm_service.py`
  **Goal:** structured success, transient retry, validation error → custom error, arg passthrough.
  **Pass:** All four tests green with `litellm.acompletion` patched.

7. **Cache repo unit**

* `unit/services/test_redis_cache_repo.py`
  **Goal:** save/get/atomic update happy & conflict; key format; TTL set.
  **Pass:** All behaviors verified with a mocked Redis client or in-mem stub.

8. **Quiz: start**

* `integration/test_quiz_start.py`
  **Goal:** 201 happy path (saves state & returns synopsis/first Q), 503 no synopsis, 504 timeout, 503 generic error.
  **Pass:** Status codes + body shape assertions.

9. **Quiz: proceed**

* `integration/test_quiz_proceed.py`
  **Goal:** 202 schedules background when ready; 404 missing session.
  **Pass:** `BackgroundTasks.add_task` intercepted & asserted.

10. **Quiz: next**

* `integration/test_quiz_next.py`
  **Goal:**

  * 202 record answer (no schedule until baseline complete)
  * 202 schedule when baseline completes
  * 202 duplicate idempotent
  * 409 out-of-order
  * 400 bad option index
  * 404 missing session
  * 409 atomic update conflict
    **Pass:** Branch coverage on all cases.

11. **Quiz: status**

* `integration/test_quiz_status.py`
  **Goal:** next unseen question (active), processing (no new), finished (result), 404 missing, 500 malformed result.
  **Pass:** Correct variant returned each time.

12. **Agent tools (unit)**

* `unit/agent/tools/test_content_creation_tools.py`
* `unit/agent/tools/test_data_tools.py`
* `unit/agent/tools/test_planning_tools.py`
  **Goal:** Each tool calls its dependency with expected args and returns normalized outputs; history/synopsis respected.
  **Pass:** Patches asserted; outputs match builders.

13. **API deps (unit)**

* `unit/api/test_dependencies.py`
  **Goal:** Turnstile bypass in local; 401 on invalid when enabled; 500 on HTTP error.
  **Pass:** Dependency override behavior proven.

14. **Feedback endpoint**

* `integration/test_feedback_endpoint.py`
  **Goal:** 204 on save, 404 on missing session (repo mocked).
  **Pass:** Status codes; repo calls asserted.

15. **Assets endpoint**

* `integration/test_assets_endpoint.py`
  **Goal:** 200 bytes + `Content-Type`,`ETag`,`Cache-Control`; 304 with If-None-Match; 404 missing.
  **Pass:** Headers + content checks.

16. **Results endpoint**

* `integration/test_results_endpoint.py`
  **Goal:** 200 normalized shareable result; 404 missing (service/repo mocked).
  **Pass:** Response shape verified.

17. **Models (unit)**

* `unit/models/test_api_models.py`
  **Goal:** Request/response schema validation (unions, indices).
  **Pass:** Pydantic acceptance/rejection as expected.

18. **State/normalizers (unit)**

* `unit/utils/test_state_normalizers.py`
* `unit/services/test_result_normalizer.py`
  **Goal:** Normalization of state/result from diverse shapes; internal fields stripped.
  **Pass:** Edge cases covered.

19. **Data samples**

* `data/sample_*.json`
  **Goal:** Keep minimal; used by earlier tests.
  **Pass:** Loaded by tests without mutation.

20. **Test README**

* `backend/tests/README.md`
  **Goal:** Document how to run subsets (`-m unit`, `-m integration`, `-m smoke`) and common fixtures.
  **Pass:** N/A (docs).

That order keeps feedback tight: after step 5 you already have end-to-end confidence; steps 6–11 harden the core quiz flow; steps 12–18 round out units and secondary endpoints.
