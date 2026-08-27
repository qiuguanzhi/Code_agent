# Development Progress

Last updated: 2026-08-27

## Phase 1 — Tools Layer

Status: Accepted by user

### Completed modules

- Project package skeleton
- OpenAI-compatible tool schemas
- Workspace path guard and streaming SHA-256
- Paginated UTF-8 file reading
- Atomic file writing with `expected_sha256`
- Local command execution with timeout, process-tree cleanup, sanitized environment, and bounded output
- Unit tests for path, filesystem, and shell behavior

### Test status

- Status: Pass
- Command: `pytest tests/test_filesystem_tools.py -v`
- Result: 6 passed
- Full Phase 1 command: `pytest tests/test_path_guard.py tests/test_filesystem_tools.py tests/test_shell.py -v`
- Result: 16 passed, 1 skipped
- Skip reason: Windows did not grant permission to create a test symlink; parent traversal and absolute-path escape tests passed.
- Static checks: Python compilation passed; all functions in Phase 1 source/tests have argument and return type annotations; `git diff --check` passed.

### Pending items

- Phase 3: Context manager, agent loop, CLI, and demo

## Phase 2 — Provider and State

Status: Accepted by user

### Completed modules

- `providers/base.py`: framework-free `ModelProvider` abstract base class
- `providers/openai_compatible.py`: DeepSeek/Bailian Chat Completions adapter with native `tool_calls`, `reasoning_content`, finish reason, and usage normalization
- `agent/state.py`: `ToolCall`, `AssistantTurn`, and `AgentState` data classes
- `agent/parser.py`: strict JSON parsing, schema validation, canonical signatures, and repeated-call limits
- `tools/registry.py`: validated tool dispatch, call-id result caching, and write policy hook
- Network-free FakeProvider and SDK-shaped response tests

### Extension interfaces

1. Snapshot and rollback
   - Location: `utils/snapshot.py`, `agent/state.py`
   - Capture: `save_workspace_snapshot(workspace_root)` returns `dict[relative_path, metadata_json]`; metadata contains SHA-256 and `mtime_ns`.
   - Storage: assign the result to `AgentState.initial_snapshot`.
   - Placeholder: `rollback_to_snapshot(snapshot_data)` prints `Rollback not fully implemented yet` and returns `False`.
2. Change confirmation and undo
   - Location: `tools/registry.py`
   - Policy: `WRITE_POLICY = {"require_confirmation": True}`; override through `ToolRegistry(..., write_policy=...)`.
   - Hook: `ToolRegistry._confirm_write(path)` currently returns `True` and is called before `write_file`.
   - Result: every dispatched write includes `meta.confirmed_by_user`; rejection returns `user_aborted` without touching disk.
3. Goal-oriented mode
   - Location: `agent/state.py`, `agent/parser.py`, `prompts/system.md`
   - State: `AgentState.mode` accepts `auto` or `goal`.
   - Parser hook: `extract_goal_plan(response_text)` currently returns `[]`.
   - Prompt: commented `GOAL_MODE_PROMPT` block is reserved for Phase 3 dynamic injection.
4. File diff
   - Location: `utils/diff.py`, `tools/filesystem.py`
   - Call: `generate_unified_diff(original_content, new_content, file_path)`.
   - Integration: `write_file` computes the diff before disk replacement and returns it as `meta.diff`.
   - Limit: diff output is bounded to 12,000 characters with head/tail retention.

### Test status

- Status: Pass
- Phase 2 focused command: `pytest tests/test_parser.py tests/test_providers.py tests/test_extensions.py -v`
- Phase 2 focused result: 24 passed
- Full regression command: `pytest tests/ -v`
- Full regression result: 40 passed, 1 skipped
- Network usage: none; provider tests use FakeProvider and SDK-shaped fake clients.
- Skip reason: Windows did not grant permission to create a test symlink.
- Static checks: compilation passed, type-hint audit passed, dependency allowlist audit passed, and `git diff --check` found no whitespace errors.

### Local test environment fix

- User reproduction: tests that did not need `tmp_path` passed, while 18 tests failed during fixture setup with `WinError 5` on `%TEMP%\pytest-of-lenovo`; no project assertion failed.
- Root cause: an inaccessible pytest global temporary directory and an inaccessible legacy `.pytest_cache`, rather than filesystem/provider implementation behavior.
- Fix: pytest now uses the repository-local `.pytest-local-tmp` base directory and disables the optional cache provider through `pyproject.toml`.
- Verification: the user's two original pytest commands now report 24 passed and 40 passed/1 skipped respectively.
- Result: test execution no longer depends on the damaged global temp/cache directories; the verification directory was removed afterward so the user's account creates it locally.

### Pending items

- Phase 3: context manager, agent loop, CLI, and demo
