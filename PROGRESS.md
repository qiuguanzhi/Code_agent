# Development Progress

Last updated: 2026-08-31

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

Status: Accepted by user

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

- None for Phase 4

## Phase 5 — GUI and Agent Binding

Status: Accepted by user

### Completed modules

- `gui/worker.py`: real `run_agent` execution in `QThread`, environment-backed Provider creation, structured lifecycle-to-signal mapping, read/code preview, write/Diff preview, change counts, and completion summary
- `gui/main_window.py`: send-to-Agent binding, real-time HTML lifecycle logs, code/Diff display, live status updates, and GUI-thread write confirmation dialog
- `agent/loop.py`: GUI-independent callback payloads now include tool-call ids and raw argument JSON; early snapshot/context failures also emit terminal events
- `main.py`: GUI startup accepts an optional `--workspace` and still defers Provider/API-key validation until a task is submitted
- `tests/test_gui.py`: FakeProvider integration tests execute the real Agent loop and local read/write tools without network access

### GUI usage

```powershell
python main.py --gui --workspace ./examples/buggy_calculator
```

- Configure `AGENT_PROVIDER`, `AGENT_MODEL`, and the selected provider's API key in the process environment before sending a real task.
- If `--workspace` is omitted, use `文件 → 打开工作区` before sending a task.
- `设置 → 交互确认` displays a GUI confirmation dialog before each real `write_file` call.
- The log panel updates at model request/response, tool call/result, step completion, retry, and terminal events.
- Successful `read_file` results display their file path and content in the right panel.
- Successful `write_file` results display the generated Unified Diff and addition/deletion counts.

### Test status

- Status: Pass
- Focused command: `pytest tests/test_gui.py tests/test_agent_loop.py tests/test_cli.py -v`
- Focused result: 14 passed
- Full regression command: `pytest tests/ -v`
- Full regression result: 59 passed, 1 skipped
- Network usage: none; GUI tests inject FakeProvider while exercising the production worker, Agent loop, registry, and local tools.
- Skip reason: Windows did not grant permission to create a test symlink.

### Known limitations

- Provider responses are non-streaming; the GUI updates between lifecycle events rather than token by token.
- The Diff signal is generated from the successful write result, so it is a post-write view; the pre-write interactive dialog currently confirms the path only.
- The right-side “应用修改/拒绝” controls acknowledge or dismiss the displayed Diff and do not implement undo.
- `rollback_to_snapshot` remains a placeholder and cannot restore file contents.
- A live API run requires user-supplied environment configuration and is not performed by automated tests.

### Pending items

- None for Phase 5

## Phase 6 — Interactive Diff, Rollback, and Drag Import

Status: Implemented; awaiting user acceptance

### Completed modules

- `gui/worker.py`: generates the Unified Diff before `write_file`, emits the preview, blocks on `threading.Event`, and passes the cached button decision to the existing confirmation hook
- `gui/main_window.py`: `confirm_signal(bool)` connects apply/reject buttons to the blocked worker; Diff additions are green and deletions are red
- `utils/snapshot.py`: content-preserving temporary backups, manifest/path validation, backup and restored-file SHA-256 verification, atomic file restoration, and removal of regular files created after capture
- `gui/main_window.py`: receives `AgentState.initial_snapshot`, performs toolbar rollback, refreshes the visible file, and records the rollback in status/log panels
- `gui/main_window.py`: accepts local-file drops, reads UTF-8 content, atomically imports by basename through the workspace-safe filesystem tool, logs the import, and displays the file
- `tests/test_extensions.py`: the obsolete rollback placeholder assertion now verifies real content restoration and removal of a post-snapshot file

### Interaction behavior

- With `设置 → 交互确认` enabled, a model `write_file` call cannot touch disk until the user clicks `应用修改`.
- `应用修改` emits `confirm_signal(True)` and lets the validated write continue.
- `拒绝` emits `confirm_signal(False)`; `ToolRegistry` returns `user_aborted`, leaves the file unchanged, and the result is returned to the model for its next reasoning step.
- The latest completed Agent run publishes its initial snapshot to the window. The toolbar rollback action restores verified content and reports `已回退到初始快照`.
- Dropped files are imported only while the Agent is idle and only when they decode as UTF-8 text.

### Test and manual verification status

- Status: Pass
- Focused regression: `pytest tests/test_extensions.py tests/test_gui.py tests/test_agent_loop.py -q`
- Focused result: 16 passed
- Full regression: `pytest tests/ -v`
- Full result: 59 passed, 1 skipped
- Example logic check: real write → rejected write remains unchanged → dropped external Markdown import → toolbar rollback; passed and left `examples/buggy_calculator` unchanged
- GUI event check: pre-write Diff visible → apply button releases worker and modifies file → rollback restores it; reject button releases worker with no write and a second model-thinking step; passed
- Network/API usage: none for verification
- Skip reason: Windows did not grant permission to create a test symlink.

### Known limitations

- Snapshots copy regular files and intentionally do not follow or restore symbolic links.
- Snapshot disk usage is approximately the size of the selected workspace and temporary backups are removed when replaced or when the process exits.
- Drag import supports UTF-8 text within the existing `write_file` size limit and imports into the workspace root using the source basename.
- A same-named dropped file is atomically replaced; users should save a snapshot first when they need a reversible import.
- Diff previews retain the existing 12,000-character head/tail limit.

### Pending items

- Wait for user acceptance of Phase 6

## GUI Enhancement — Enhance.md

Status: Implemented; awaiting user acceptance

### Completed modules

- `gui/session.py`: QSettings-backed conversations with independent messages and compact logs
- `gui/worker.py`: one completed-tool log per call; model request/response chatter removed from the log channel
- `gui/main_window.py`: immediate input clearing, three-column workspace, collapsible file list, closable multi-file tabs, drag import/preview choice, visible waiting state, single-slot timestamped snapshots, and persistent theme switching
- `gui/theme.py`: complete semantic dark/light QSS palettes
- `tests/test_gui.py`: headless regression coverage for every item in the enhancement acceptance list
- `process.md`: implementation summary, commands, results, and checked acceptance list

### Test status

- Status: Pass
- GUI regression: `pytest tests/test_gui.py -q` → 14 passed
- Full regression: `pytest tests/ -q` → 67 passed, 1 skipped
- Network/API usage: none; deterministic FakeProvider tests exercise the real Agent loop and local tools
- Skip reason: the current Windows environment does not permit creation of the symlink used by one path-guard test

### Pending items

- User visual acceptance of the enhanced desktop interface

## GUI Core Optimization — promove.md

Status: Implemented; awaiting user acceptance

### Completed modules

- `main.py`: no-argument desktop startup from the project/current directory while explicit `--cli` behavior remains unchanged
- `gui/main_window.py`: switchable workspace with synchronized process cwd, native editable-operation file tree, first-tab visibility, empty task input, no redundant run action, quick/deep selector, progress animation, and strict Diff action lifecycle
- `gui/worker.py`: safe high-level deep-mode progress events and post-write code refresh without exposing model private reasoning
- `gui/session.py`: persistent high-level process summaries per conversation
- `gui/theme.py`: filesystem tree, native progress bar, and loading-label styling for both themes
- `tests/test_gui.py` and `tests/test_cli.py`: regression coverage for all nine requested optimizations
- `process.md`: append-only implementation and acceptance record for every item

### Test status

- Status: Pass
- Focused regression: `pytest tests/test_gui.py tests/test_cli.py -q` → 22 passed
- Full regression: `pytest tests/ -q` → 72 passed, 1 skipped
- Network/API usage: none
- Skip reason: the current Windows environment does not permit creation of the test symlink

### Pending items

- User visual acceptance of the optimized desktop interface

## GUI Round 3 — Staging, Logs, Messages, and Visual Refresh

Status: Implemented; awaiting user acceptance

### Completed modules

- `gui/widgets.py`: non-interactive black empty-tab placeholder with automatic first/last tab visibility
- `gui/main_window.py`: pending-Diff close/exit confirmation, staged-change state, Diff decision lifecycle, deep-process block inside logs, latest-tool status line, deletable message bubbles, and empty-start workspace behavior
- `gui/session.py`: permanent message deletion, startup reset, and hierarchical high-level process records
- `gui/worker.py`: hierarchical safe-process signals and running/success/error tool-status signals with full failure detail
- `gui/theme.py`: ChatGPT Windows Desktop-inspired light/dark QSS across the complete widget tree
- `main.py`: no-argument GUI startup no longer preloads the current directory
- `tests/test_gui.py` and `tests/test_cli.py`: coverage for every Round 3 state transition and regression requirement
- `process.md`: append-only per-task implementation, test, and limitation record

### Test status

- Status: Pass
- Focused regression: `pytest tests/test_gui.py tests/test_cli.py -q` → 27 passed
- Full regression: `pytest tests/ -q` → 77 passed, 1 skipped
- Network/API usage: none
- Skip reason: the current Windows environment does not permit creation of the test symlink

### Pending items

- User visual acceptance of Round 3

## GUI Corrective Regression — 2026-08-28

Status: Implemented and verified

### Completed modules

- `gui/main_window.py`: removed the stray empty-editor/empty-tab artifact; GUI Agent writes now always require Diff approval; workspace code tabs support separate manual editing and explicit save; complete-conversation deletion is wired to the session header; fixed-width fonts are normalized before use.
- `gui/widgets.py`: replaced HTML/table messages with native bordered, rounded, selectable message bubbles and per-message delete controls.
- `gui/session.py`: supports permanent deletion of an entire conversation with safe selection/empty-session recovery.
- `gui/theme.py`: native bubble, empty code surface, manual-save row, and delete-control styles for both themes; obsolete black placeholder styling removed.
- `tests/test_gui.py`: regression coverage for the exact empty-state artifact, mandatory approval of modified and newly generated files, reject-without-write, manual edit/save separation, complete history deletion, native bubble structure, and Qt font warnings.
- `process.md`: recorded root causes, behavior changes, and verification evidence.

### Test status

- Status: Pass
- GUI regression: `pytest tests/test_gui.py -q` → 28 passed
- Full regression: `pytest tests/ -q` → 82 passed, 1 skipped
- Visual check: empty state and pending-Diff state rendered offscreen and inspected; no stray top-left rectangle remains, and Diff decision controls are visible.
- Network/API usage: none
- Skip reason: the current Windows environment does not permit creation of the symlink used by one path-guard test

### Pending items

- User visual acceptance of the corrected desktop interface

## Round 11 — Skills, Context Capacity, and Runtime Limits

Status: Implemented and verified

### Completed modules

- `skills/`: framework-free Skill contract, discovery/enablement registry, permission gates, and three read-only built-in coding skills
- `tools/registry.py`: Skill-first routing, nested native-tool validation, high-risk mutation policy, and native-name collision protection
- `utils/token_counter.py` and `agent/context.py`: observable conservative Token estimates and protocol-safe automatic compression above 80%
- `agent/state.py` and `agent/loop.py`: context metrics, 20-minute hard duration, three-step warnings, and at most three user-approved ten-step extensions
- `gui/skill_dialog.py`, `gui/widgets.py`, `gui/worker.py`, and `gui/main_window.py`: persistent skill manager, high-risk confirmation, themed context ring, timeout reporting, and thread-safe loop-extension prompts
- `tests/test_round11_features.py`: local Skill, permission, compression, timeout, extension, and offscreen context-ring coverage
- `process.md`: per-task Round 11 implementation, evidence, and limitations

### Test status

- Status: Pass
- Round 11 focused regression: `pytest tests/test_round11_features.py -q` → 9 passed
- Full regression: `pytest tests/ -q` → 148 passed, 1 skipped
- Network/API usage: none; all new Agent tests use deterministic providers and local temporary workspaces
- Skip reason: the current Windows environment does not permit creation of the path-guard symlink

### Pending items

- User visual acceptance of the Skill dialog, context ring, and max-step prompt with a real provider session

## Round 12 — 64K Context and Unlimited User Continuations

Status: Implemented and verified

### Completed modules

- `agent/context.py` and `agent/loop.py`: shared 64K input budget, observable startup configuration, cumulative step progress, and unbounded user-approved +20 extensions
- `providers/openai_compatible.py`: validated 8192-token output limit on streaming and non-streaming requests
- `gui/worker.py` and `gui/main_window.py`: 64K startup log, context-ring initialization, default 30-step limit, permanent `current/limit` status label, and repeatable continuation prompt
- `main.py`: CLI defaults synchronized to 64K input and 30 steps
- `coding_agent_technical_design.md`: runtime-limit guidance synchronized to the implemented 30-step and repeatable +20 policy
- `tests/test_round12_features.py` and `tests/test_gui.py`: large-context, request-payload, multi-extension, rejection, startup-log, and status-label regression coverage
- `process.md`: Round 12 implementation evidence and limitations

### Test status

- Status: Pass
- Context/Provider focused regression: 26 passed
- Full regression: `pytest tests/ -q` → 155 passed, 1 skipped
- Network/API usage: none
- Skip reason: the current Windows environment does not permit creation of the path-guard symlink

### Pending items

- Superseded by the larger Round 13 validation target below

## Round 13 — 320K Context and 200-Step Default

Status: Implemented and verified

### Completed modules

- `agent/context.py` and `agent/loop.py`: 320K input budget, 200-step default, and repeatable +50 cumulative extensions
- `providers/openai_compatible.py`: 16,384-token output request limit for streaming and non-streaming calls
- `gui/worker.py` and `gui/main_window.py`: 320K startup event, dynamic context-ring denominator, 200-step status display, and +50 confirmation feedback
- `main.py` and `coding_agent_technical_design.md`: CLI defaults and design guidance synchronized with runtime behavior
- `tests/test_round12_features.py` and `tests/test_gui.py`: 150K-character input, 150-step no-prompt, 200/250-step extensions, Provider payload, startup log, and GUI state coverage
- `process.md`: Round 13 evidence, regression results, and model-capability caveat

### Test status

- Status: Pass
- Round 13 focused regression: 32 passed
- Full regression: `pytest tests/ -q` → 157 passed, 1 skipped
- Network/API usage: none
- Skip reason: the current Windows environment does not permit creation of the path-guard symlink

### Pending items

- Verify that the selected real model and gateway support a 320K input window and 16,384 output tokens

## Empty Model Response Recovery — 2026-08-31

Status: Implemented and verified

### Completed modules

- `providers/openai_compatible.py`: content-free raw response diagnostics, granular protocol error codes, 120-second per-request timeout, configurable model input limit, optional `max_completion_tokens=8192`, and explicit finish-reason handling
- `agent/context.py` and `agent/loop.py`: request diagnostics, effective model-aware context budget, protocol-safe tool-history compression, two clean-context empty-response retries, partial length-truncated answer preservation, and structured failure history
- `agent/state.py`: empty retry counters, last error code, and bounded structured error records
- `gui/worker.py`: friendly classified model errors, diagnostic event logs, compression/truncation feedback, and model-error signal
- `gui/main_window.py` and `gui/theme.py`: contextual “🔄 重试” action that creates a fresh run without duplicating the retained user conversation
- `tests/test_agent_loop.py`, `tests/test_context.py`, `tests/test_providers.py`, and `tests/test_gui.py`: network-free regression coverage for all recovery paths
- `process.md`: implementation evidence, compatibility decisions, tests, and remaining real-provider validation

### Test status

- Status: Pass
- Full regression: `pytest tests/ -q` → 167 passed, 1 skipped
- Network/API usage: none
- Skip reason: the current Windows environment does not permit creation of the path-guard symlink

### Pending items

- Validate one deliberately large request against the selected real gateway and set `AGENT_MODEL_INPUT_TOKENS` to that model's documented input limit
- Enable `AGENT_USE_MAX_COMPLETION_TOKENS=1` only for gateways that explicitly support that parameter
