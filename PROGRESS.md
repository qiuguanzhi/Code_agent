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

- None for Phase 1

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
- Fix: pytest disables the optional cache provider, and `tests/conftest.py` assigns every process a unique repository-local `.pytest-run-<pid>-<uuid>` base directory.
- Verification: the user's two original pytest commands now report 24 passed and 40 passed/1 skipped respectively.
- Result: test execution no longer depends on damaged global temp/cache directories and does not reuse a directory created under another Windows security context.

### Pending items

- None for Phase 2

## Phase 3 — Agent Loop and CLI

Status: Accepted by user

### Completed modules

- `agent/context.py`: protocol-unit grouping, deterministic JSON work memory, conservative character-based token estimation, and budget fitting
- `agent/loop.py`: snapshot initialization, model/tool loop, bounded API retries, wall-clock/max-step/repeated-call stops, and lifecycle callbacks
- `main.py`: CLI argument parsing, provider/environment validation, interactive write confirmation, dependency-free colored events, and completion summary
- `tools/registry.py`: injected confirmation callback and separate Diff/hash change tracking
- `examples/buggy_calculator/`: intentionally failing zero-division demo with isolated pytest configuration
- `tests/conftest.py`: unique per-process repository-local pytest temp directories to avoid Windows ACL collisions

### CLI usage

```powershell
python main.py --workspace ./examples/buggy_calculator "修复除零错误"
python main.py --workspace ./demo "运行测试" --max-steps 12
python main.py --workspace ./demo "修改代码" --interactive
python main.py --cli --workspace ./demo "规划并修复" --mode goal --verbose
```

Supported arguments:

- `--workspace`: required existing workspace directory
- `--max-steps`: maximum model/tool iterations, default 20
- `--max-wall-seconds`: wall-clock run limit, default 600
- `--input-budget`: conservative input token budget, default 48,000
- `--interactive`: wait for `y/n` before each `write_file`
- `--verbose`: print structured lifecycle event data
- `--mode {auto,goal}`: activate normal or goal-oriented prompt behavior
- `--provider {deepseek,bailian}`: select the environment-backed provider
- `--cli`: explicit CLI selector reserved for future GUI coexistence

### Test status

- Status: Pass
- Phase 3 focused command: `pytest tests/test_context.py tests/test_agent_loop.py tests/test_cli.py -v`
- Phase 3 focused result: 12 passed
- Full regression command: `pytest tests/ -v`
- Full regression result: 52 passed, 1 skipped
- Demo precondition: `examples/buggy_calculator` reports 1 failed/1 passed before the agent repair, as intended.
- Network usage: none; the full read → write → pytest → finish loop uses `FakeProvider` while executing real local tools.
- Skip reason: Windows did not grant permission to create a test symlink.

### Design update

- `E:\codex\Project\coding_agent_technical_design.md` now contains section `0.4 桌面 GUI 架构（PySide6）` between sections 0.3 and 1.
- Phase 3 remains CLI-only; PySide6 is not yet a dependency and no GUI implementation was added.

### Known limitations

- `rollback_to_snapshot` remains a Phase 2 placeholder and snapshots record metadata, not restorable file contents.
- Token estimation deliberately overestimates from character count; no model-specific tokenizer is bundled.
- Shell execution remains an allow-listed local mechanism, not a container-grade sandbox.
- Streaming model output and GUI delivery are not implemented in Phase 3.
- A real API end-to-end run requires the user's environment-specific key/model and was not performed in automated tests.

### Pending items

- None for Phase 3

## Phase 4 — PySide6 GUI Skeleton

Status: Implemented; awaiting user acceptance

### Completed modules

- `gui/theme.py`: complete Catppuccin Mocha QSS theme using the design palette
- `gui/worker.py`: `QThread` placeholder with log, code, Diff, status, and completion signals
- `gui/main_window.py`: menus, toolbar, three-color status indicator, adjustable 60/40 split view, decision buttons, task input, and status bar
- `gui/__init__.py`: public exports for `MainWindow` and `AgentWorker`
- `main.py`: mutually exclusive `--gui` and `--cli` startup paths with lazy PySide6 imports
- `pyproject.toml`: PySide6 runtime dependency and GUI package discovery
- `tests/test_gui.py`: offscreen theme, layout, worker-signal, six-log, and GUI-dispatch tests

### GUI usage

```powershell
python main.py --gui
```

- GUI mode does not require a workspace, API key, or network connection in Phase 4.
- Enter a task and click `发送` or `▶ 运行任务` to run the five-second simulation.
- The simulation emits six color-coded logs at half-second intervals, previews candidate code and a Unified Diff, and enables the apply/reject placeholders.
- CLI usage remains unchanged, including the optional explicit `--cli` selector.

### Test status

- Status: Pass
- Focused command: `pytest tests/test_gui.py tests/test_cli.py -v`
- Focused result: 8 passed
- Full regression command: `pytest tests/ -v`
- Full regression result: 58 passed, 1 skipped
- GUI platform in tests: Qt `offscreen`; no display server, API key, or network is required.
- Skip reason: Windows did not grant permission to create a test symlink.

### Known limitations

- The GUI worker is intentionally simulated and does not call `run_agent` in Phase 4.
- Apply/reject buttons only update the preview state; they do not write files.
- Rollback invokes the existing Phase 2 placeholder and cannot yet restore content.
- GUI interactive confirmation records the selected policy but is not connected to tool execution.

### Pending items

- Wait for user acceptance of Phase 4
- Connect `AgentWorker` to the real Agent loop in a future phase
