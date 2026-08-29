# 项目架构与组件映射文档

> 基于 2026-08-29 当前工作区源码生成。本文描述的是实际代码，而不是目标设计。
> 目录树排除了 `.git/`、`.venv/`、缓存、临时测试目录和构建产物。

## 1. 目录树与模块职责

### 1.1 项目目录树

```text
Code_agent/
├─ .env.example                         # API 环境变量名称示例，不包含默认密钥
├─ .gitignore                           # 忽略密钥、虚拟环境、缓存和构建产物
├─ PROJECT_ARCHITECTURE.md              # 本文档
├─ README.md                            # 项目使用说明
├─ coding_agent_technical_design.md     # 技术设计文档
├─ process.md                           # 开发与修复过程记录
├─ PROGRESS.md                          # 阶段进度和测试状态
├─ pyproject.toml                       # 包元数据、运行依赖和 pytest 配置
├─ main.py                              # CLI/GUI 双入口
├─ set-env.ps1                          # 本地启动脚本；已被 Git 忽略，当前含敏感配置
├─ agent/
│  ├─ __init__.py                       # Agent 包标识
│  ├─ context.py                        # 协议单元分组、工作记忆压缩和输入预算裁剪
│  ├─ loop.py                           # 模型—工具—观察主循环及终止/重试策略
│  ├─ parser.py                         # 工具参数校验、签名去重和结构化错误
│  └─ state.py                          # ToolCall、AssistantTurn、AgentState 数据结构
├─ gui/
│  ├─ __init__.py                       # 导出 MainWindow 和 AgentWorker
│  ├─ main_window.py                    # 主窗口布局、GUI 状态和全部用户交互槽函数
│  ├─ session.py                        # Conversation/ConversationStore 与 QSettings 持久化
│  ├─ theme.py                          # 亮暗语义色板和全局 QSS 生成
│  ├─ widgets.py                        # 原生消息气泡和可滚动对话容器
│  └─ worker.py                         # QThread 后台适配器及 Agent→Qt 信号转换
├─ providers/
│  ├─ __init__.py                       # Provider 包标识
│  ├─ base.py                           # ModelProvider 抽象接口
│  └─ openai_compatible.py              # DeepSeek/百炼 OpenAI 兼容适配器
├─ tools/
│  ├─ __init__.py                       # 导出本地工具公共 API
│  ├─ filesystem.py                     # 工作区路径防逃逸、分页读取、哈希和原子写入
│  ├─ registry.py                       # 工具校验、去重、确认、分发和结果缓存
│  ├─ schemas.py                        # 三个模型可见工具的 JSON Schema
│  └─ shell.py                          # 无 Shell 命令执行、超时、进程树终止和输出截断
├─ utils/
│  ├─ __init__.py                       # Utils 包标识
│  ├─ diff.py                           # Unified Diff 生成及头尾截断
│  └─ snapshot.py                       # 工作区内容快照、校验、回退和临时备份清理
├─ prompts/
│  └─ system.md                         # Agent 系统提示词及 Goal 模式动态区块
├─ examples/
│  ├─ .gitkeep                          # 保留 examples 目录
│  └─ buggy_calculator/
│     ├─ backend_introduction.md        # 示例说明
│     ├─ calc.py                        # 除法示例实现
│     ├─ pytest.ini                     # 示例项目 pytest 配置
│     └─ test_calc.py                   # 常规除法和除零测试
└─ tests/
   ├─ conftest.py                       # 离屏 Qt 和仓库内独立临时目录配置
   ├─ test_agent_loop.py                # Agent 闭环、重试、上限、去重和拒绝写入测试
   ├─ test_cli.py                       # CLI 参数、缺失配置和无参数 GUI 启动测试
   ├─ test_context.py                   # 上下文分组、压缩、Token 估算和预算测试
   ├─ test_extensions.py                # 快照、回退、Diff 和确认扩展测试
   ├─ test_filesystem_tools.py          # 分页、截断、哈希锁、原子写和逃逸测试
   ├─ test_gui.py                       # GUI 布局、线程、审批、编辑、会话和主题回归
   ├─ test_parser.py                    # JSON Schema 校验、签名和模式测试
   ├─ test_path_guard.py                # 相对/绝对/父目录/符号链接路径防护测试
   ├─ test_providers.py                 # Provider 响应规范化和环境变量工厂测试
   └─ test_shell.py                     # 输出缓冲、环境清洗、超时和命令限制测试
```

### 1.2 核心 Python 文件的唯一职责

| 文件 | 唯一职责 | 主要对外接口 |
|---|---|---|
| `main.py` | 解析 CLI/GUI 参数并选择启动路径；CLI 下组装 Provider、AgentConfig 和终端输出。代码只捕获配置错误、`KeyboardInterrupt` 等指定异常，**没有全局未捕获异常处理器**。 | `build_parser()`、`run_gui()`、`main()` |
| `agent/state.py` | 定义 Provider/Agent 间共享的类型化运行状态。 | `ToolCall`、`AssistantTurn`、`AgentState` |
| `agent/parser.py` | 将模型工具参数解析为严格 JSON 对象，并按本地 Schema 校验、生成重复调用签名和标准错误。 | `parse_tool_arguments()`、`register_call_signature()`、`tool_error()` |
| `agent/context.py` | 在不拆开 Assistant+Tool 协议单元的前提下裁剪历史，并将丢弃历史压缩成 `WORK_MEMORY_JSON`。 | `fit_context()`、`group_protocol_units()`、`build_work_memory()` |
| `agent/loop.py` | 实现自主循环、快照初始化、模型重试、工具回填、墙钟/步数/重复调用终止和生命周期回调。 | `AgentConfig`、`AgentRunResult`、`run_agent()` |
| `providers/base.py` | 规定 Agent 唯一依赖的模型调用抽象。 | `ModelProvider.complete()` |
| `providers/openai_compatible.py` | 用原生 `openai.OpenAI` Chat Completions 调用 DeepSeek/百炼，并规范化原生 `tool_calls`。 | `OpenAICompatibleProvider`、`create_provider_from_env()` |
| `tools/schemas.py` | 保存 `read_file`、`write_file`、`run_command` 的 OpenAI 兼容 Schema。 | `TOOL_SCHEMAS`、`get_tool_schemas()` |
| `tools/registry.py` | 把已验证的 ToolCall 分发到本地工具，处理确认、去重、幂等缓存及 AgentState 写入记录。 | `ToolRegistry.execute_one_call()` |
| `tools/filesystem.py` | 提供工作区约束的 UTF-8 文件读取和带 SHA-256 乐观锁的原子写入。 | `resolve_in_workspace()`、`read_file()`、`write_file()` |
| `tools/shell.py` | 用 `subprocess.Popen(shell=False)` 执行允许列表命令，并限制环境、时间和输出。 | `run_command()`、`BoundedHeadTailBuffer` |
| `utils/diff.py` | 通过标准库 `difflib` 生成和截断 Unified Diff。 | `generate_unified_diff()`、`truncate_diff()` |
| `utils/snapshot.py` | 把工作区普通文件复制到进程私有临时目录，并验证哈希后回退。 | `save_workspace_snapshot()`、`rollback_to_snapshot()`、`discard_workspace_snapshot()` |
| `gui/main_window.py` | 创建完整桌面 UI，并持有工作区、会话、Tab、Diff、快照及 Worker 生命周期状态。 | `MainWindow` |
| `gui/session.py` | 定义 GUI 会话数据结构，并通过 QSettings 序列化消息、日志和过程摘要。 | `Conversation`、`ConversationStore` |
| `gui/widgets.py` | 构造原生圆角消息气泡及其滚动、删除信号。 | `MessageBubble`、`ConversationScrollArea` |
| `gui/worker.py` | 在 QThread 中运行框架无关 Agent，把结构化回调映射为 Qt 信号，并实现写入确认等待。 | `AgentWorker` |
| `gui/theme.py` | 从亮/暗语义色板生成全局 QSS。 | `LIGHT_THEME`、`DARK_THEME`、`get_theme()` |
| `examples/buggy_calculator/calc.py` | 提供演示用 `divide()` 实现。当前源码已包含除零保护。 | `divide()` |
| `examples/buggy_calculator/test_calc.py` | 验证演示项目的正常除法与除零异常。 | 两个 pytest 测试 |

`agent/__init__.py`、`providers/__init__.py`、`utils/__init__.py` 当前仅为包标识；`gui/__init__.py` 和 `tools/__init__.py` 提供稳定的包级导出。

### 1.3 测试文件映射

| 完整路径 | 覆盖边界 |
|---|---|
| `tests/conftest.py` | 设置 `QT_QPA_PLATFORM=offscreen`，并为每次 pytest 运行创建仓库内隔离临时根目录 |
| `tests/test_agent_loop.py` | 完整 Agent Tool Calling 闭环、瞬态重试、max_steps、重复调用和交互拒绝 |
| `tests/test_cli.py` | 参数解析、缺失 API Key、缺失任务/工作区、无参数 GUI 分支 |
| `tests/test_context.py` | 20% Token 余量、协议单元、Work Memory、预算裁剪和不可满足预算 |
| `tests/test_extensions.py` | 快照内容回退、Diff 截断、默认/拒绝确认钩子 |
| `tests/test_filesystem_tools.py` | 文件分页/截断、SHA 冲突、原子替换、新建文件和父目录逃逸 |
| `tests/test_gui.py` | PySide6 布局、Worker 信号、Diff 审批、手动保存、历史、拖拽、快照和主题 |
| `tests/test_parser.py` | 工具 JSON/Schema 校验、签名稳定性、重复上限和 Agent mode |
| `tests/test_path_guard.py` | 工作区内路径、绝对路径、`..` 与符号链接逃逸 |
| `tests/test_providers.py` | SDK/Dict 响应规范化、原生 tool_calls、reasoning 字段和环境变量工厂 |
| `tests/test_shell.py` | 头尾缓冲、敏感环境移除、成功/截断/超时、cwd 逃逸和 Shell 拒绝 |

## 2. UI 布局层次结构（重点）

### 2.1 QMainWindow 总体嵌套

```text
MainWindow : QMainWindow                         objectName: 未设置（建议 mainWindow）
├─ QMenuBar                                     Qt 自动创建
├─ QToolBar                                     objectName: mainToolbar
├─ centralWidget : QWidget                      objectName: 未设置（建议 centralRoot）
│  └─ QVBoxLayout                               margins=10, spacing=8
│     ├─ mainSplitter : QSplitter(Horizontal)   objectName: mainSplitter
│     │  ├─ workspacePanel : QWidget            objectName: workspacePanel
│     │  ├─ conversationPanel : QWidget         objectName: 未设置（建议 conversationPanel）
│     │  └─ codePanel : QWidget                 objectName: 未设置（建议 codePanel）
│     └─ toolStatusButton : QPushButton          objectName: toolStatusButton, fixedHeight=30
└─ QStatusBar                                   objectName: statusBar
```

窗口代码默认调用 `resize(1280, 780)`，最小尺寸为 `940×580`。在离屏平台、1280×780 窗口下实测：centralWidget 为 `(x=0, y=85, w=1280, h=673)`；菜单栏、工具栏和状态栏占用其余垂直空间。不同操作系统样式和 DPI 会改变实测像素，配置值不变。

### 2.2 Splitter 层级、尺寸与拉伸

| 层级 | 类型/方向 | 当前 objectName | 子项顺序 | 配置尺寸 | Stretch Factor | 1280×780 离屏实测 |
|---|---|---|---|---|---|---|
| 1 | `QSplitter(Horizontal)` | `mainSplitter` | 左：工作区；中：对话；右：代码/Diff | `setSizes([200, 620, 460])` | `0 : 3 : 2` | Splitter `(10,10,1260,615)`；子项宽度 `[200,604,448]`，两个 handle 各约 4px |
| 2 | `QSplitter(Vertical)` | **未设置**；建议 `conversationLogSplitter` | 上：对话气泡；下：日志/深度过程 | `setSizes([360, 260])` | **未显式设置**，使用 Qt size policy/size hint | 容器 `(0,42,604,531)`；子项高度 `[306,221]`，handle 约 4px |

主 Splitter 左栏 `workspacePanel` 的最小/最大宽度是 `120/400`。折叠时整个左 Panel `setVisible(False)`；展开时使用 `_workspace_saved_width` 恢复，初始记忆宽度为 200。

### 2.3 三个主 Panel 的内部结构

#### 左栏：工作区 Panel

```text
workspacePanel                              objectName: workspacePanel
└─ QVBoxLayout                              margins=4
   ├─ header : QHBoxLayout                  objectName 不适用于 Layout
   │  ├─ QLabel("工作区文件")              未设置；建议 workspaceTitleLabel
   │  └─ workspace_collapse_button          未设置；建议 workspaceCollapseButton, fixedWidth=32
   ├─ workspace_tree : QTreeView            objectName: workspaceFileTree, stretch=1
   └─ workspace_empty_label : QLabel        objectName: workspaceEmptyLabel, stretch=1
```

`QFileSystemModel` 为只读模型；创建/删除动作由 MainWindow 自行验证路径并执行，不直接依赖模型写接口。

#### 中栏：会话 Panel

```text
conversationPanel : QWidget                 未设置；建议 conversationPanel
└─ QVBoxLayout                              margins=0
   ├─ sessionRow : QHBoxLayout
   │  ├─ sessionCombo                       objectName: sessionCombo, stretch=1
   │  ├─ newSessionButton                   未设置；建议 newSessionButton
   │  └─ deleteSessionButton                objectName: deleteSessionButton
   ├─ conversationLogSplitter : QSplitter(V)未设置；建议 conversationLogSplitter
   │  ├─ ConversationScrollArea             objectName: conversationView
   │  │  └─ content QWidget                 objectName: conversationContent
   │  │     └─ MessageBubble 列表
   │  │        ├─ QFrame                    userBubble/assistantBubble/systemBubble
   │  │        ├─ role QLabel               messageRoleLabel
   │  │        ├─ content QLabel            messageContent
   │  │        └─ delete QToolButton         messageDeleteButton
   │  └─ logPanel : QWidget                 objectName: logPanel
   │     ├─ thinkingContainer               objectName: thinkingContainer
   │     │  ├─ thinkingToggle               objectName: thinkingToggle
   │     │  └─ thinkingView                 objectName: thinkingView, maxHeight=180
   │     └─ logView : QTextEdit             objectName: logView, stretch=1
   ├─ loadingContainer : QWidget             未设置；建议 agentLoadingContainer
   │  ├─ thinkingProgress                   objectName: thinkingProgress, fixedWidth=96
   │  └─ loadingLabel                       objectName: loadingLabel
   ├─ waitingIndicator                      objectName: waitingIndicator
   └─ inputRow : QHBoxLayout
      ├─ taskInput                          objectName: taskInput, stretch=1
      ├─ sendButton                         objectName: sendButton
      └─ thinkingModeCombo                  objectName: thinkingModeCombo
```

消息气泡最大宽度为 620px；用户消息右对齐，Assistant/System 左对齐。

#### 右栏：代码与 Diff Panel

```text
codePanel : QWidget                         未设置；建议 codePanel
└─ QVBoxLayout                              margins=0
   ├─ codeStack : QStackedWidget            objectName: codeStack, stretch=1
   │  ├─ codeEmptyPage                      objectName: codeEmptyPage
   │  │  └─ codeEmptyLabel                  objectName: codeEmptyLabel
   │  └─ codeTabs : QTabWidget              objectName: codeTabs（有文件时才加入 Stack）
   │     └─ per-file QTextEdit               objectName: codeView
   ├─ manualSaveRow : QHBoxLayout
   │  ├─ manualFileStatus                   objectName: manualFileStatus
   │  └─ manualSaveButton                   objectName: manualSaveButton
   └─ diffDecisionBar : QWidget             未设置；建议 diffDecisionBar
      ├─ applyButton                        objectName: applyButton
      └─ rejectButton                       objectName: rejectButton
```

零 Tab 时，`codeTabs` 会从 `codeStack` 中移除并隐藏；首个 Tab 创建时重新加入并显示。Diff 状态下 `codeView` 只读并渲染带颜色的 HTML；普通工作区文件状态下它是可编辑纯文本控件。

### 2.4 当前缺失的核心 objectName 建议清单

下表是**建议值，当前源码尚未设置**，不能在自动化选择器中当作现有名称使用。

| 当前对象 | 建议 objectName | 原因 |
|---|---|---|
| `MainWindow` | `mainWindow` | UI 自动化根节点 |
| centralWidget 根 QWidget | `centralRoot` | 区分 QMainWindow chrome 与业务内容 |
| 中栏 Panel | `conversationPanel` | 主 Splitter 三栏唯一定位 |
| 右栏 Panel | `codePanel` | 主 Splitter 三栏唯一定位 |
| 中栏垂直 Splitter | `conversationLogSplitter` | 调整/持久化上下高度所需 |
| `loading_container` | `agentLoadingContainer` | 与 thinkingContainer 区分 |
| `decision_widget` | `diffDecisionBar` | 定位 Diff 审批操作区 |
| `new_session_button` | `newSessionButton` | 会话操作自动化 |
| `workspace_toggle_button` | `workspaceToggleButton` | 工具栏折叠入口 |
| `workspace_collapse_button` | `workspaceCollapseButton` | 左栏内部折叠入口 |

### 2.5 已设置的非容器 objectName 速查

| 区域 | objectName |
|---|---|
| 文件菜单 Actions | `openWorkspaceAction`、`saveSnapshotAction`、`rollbackAction` |
| 强制审批 Action | `interactiveAction` |
| 工具栏状态与信息 | `statusIndicator`、`workspaceLabel`、`snapshotLabel` |
| 工具栏按钮 | `selectWorkspaceButton`、`saveSnapshotButton`、`themeButton` |
| 会话/输入 | `sessionCombo`、`deleteSessionButton`、`taskInput`、`sendButton`、`thinkingModeCombo` |
| 工作过程 | `thinkingToggle`、`thinkingView`、`thinkingProgress`、`loadingLabel`、`waitingIndicator` |
| 代码操作 | `manualFileStatus`、`manualSaveButton`、`applyButton`、`rejectButton` |

## 3. 核心状态变量与数据流

### 3.1 工作区路径

项目没有全局单例。工作区按层复制到以下对象：

| 位置 | 变量 | 作用 |
|---|---|---|
| `gui/main_window.py` | `MainWindow.workspace_root: Path | None` | GUI 当前工作区的权威值；切换时更新文件树、快照、打开 Tab，并调用 `os.chdir()` 同步进程 cwd |
| `gui/main_window.py` | `workspace_model.rootPath()`、`workspace_files: set[str]` | 文件树根路径与最多 2000 个普通文件的兼容索引 |
| `gui/worker.py` | `AgentWorker._workspace: Path | None` | 启动任务时解析并冻结给该 QThread 的工作区 |
| `agent/loop.py` | `AgentConfig.workspace: Path` | 单次 Agent 运行的类型化配置 |
| `tools/registry.py` | `ToolRegistry.workspace: Path` | 本地工具分发时的工作区边界 |

所有模型可见文件路径都必须经过 `resolve_in_workspace()`；绝对路径、`..` 逃逸和指向外部的现有符号链接会被拒绝。

### 3.2 对话历史与 Agent 历史

GUI 会话和模型协议历史是两套不同数据：

- `Conversation`：`id: str`、`title: str`、`messages: list[dict]`、`logs: list[dict]`、`process: list[dict]`。
- `ConversationStore.conversations: list[Conversation]`：GUI 会话集合；`active_id` 指向当前会话；编码为 JSON 存入 QSettings 的 `conversations/data` 与 `conversations/active_id`。
- `MainWindow.__init__()` 当前在创建 `ConversationStore` 后立即调用 `reset()`，所以每次新建窗口都会清空已加载会话并写入一个空会话。QSettings 持久化目前只在同一窗口生命周期/直接使用 Store 时有意义。
- `AgentState.messages: list[dict[str, Any]]`：单次 `run_agent()` 的原生 system/user/assistant/tool 协议历史；不会直接复用 GUI Conversation。
- `AgentState.repeated_signatures`、`tool_result_cache`：分别用于相同调用计数和 call-id 幂等回放。

### 3.3 未应用 Diff（暂存区）

未应用状态的权威位置是 `MainWindow`，而不是 `AgentState.changed_files`：

```text
MainWindow._tab_previews[file_path] = {
    "code": str,                 # 写入前/当前纯文本
    "diff": str,                 # 尚未决策的 Unified Diff；空串表示普通编辑态
    "additions": int,
    "deletions": int,
    "pending": bool,             # 是否等待 Agent 修改决策
    "workspace_backed": bool,    # 是否映射到当前工作区普通文件
    "base_sha256": str,          # 手动保存用乐观锁基线
    "dirty": bool,               # 未保存的手动编辑
}
```

配套状态：

- `_tab_editors: dict[str, QTextEdit]`：路径到实际编辑器。
- `_awaiting_confirmation: bool`：Worker 是否阻塞等待批准。
- `_pending_write_path: str`：当前等待决定的 Agent 写入路径。
- `AgentWorker._confirmation_event` / `_confirmation_result` / `_prepared_confirmation_path`：跨线程等待与一次性决定缓存。
- `AgentState.changed_files: dict[str, str]`：**已经成功执行**的文件 Diff 历史，不是 UI 暂存区。
- `AgentState.changed_file_hashes`：成功写入后的 SHA-256。

手动编辑与 Agent 写入是两条独立路径：手动编辑只设置 `dirty=True` 并通过 `manualSaveButton` 调用 `write_file()`；Agent 修改必须先进入 Diff 审批。

### 3.4 用户消息到界面更新的调用链

```text
taskInput.returnPressed / sendButton.clicked
    → MainWindow._submit_task()
        → ConversationStore.add_message("user", task)
        → MainWindow._render_active_session()
        → MainWindow._create_worker()
        → 连接 AgentWorker 全部信号
        → AgentWorker.start_agent(task, workspace, max_steps, interactive=True)
            → QThread.start()
                → AgentWorker.run()
                    → create_provider_from_env()
                    → AgentConfig(...)
                    → run_agent(task, cfg, AgentWorker._handle_update)
                        → save_workspace_snapshot()
                        → fit_context()
                        → ModelProvider.complete()
                        → ToolRegistry.execute_one_call()
                        → read_file/write_file/run_command
                        → _emit(... lifecycle event ...)
                    → AgentWorker 将生命周期事件转换成 Qt signals
        → MainWindow slots 更新会话、日志、代码、Diff、状态和快照
```

#### Worker → MainWindow 信号映射

| AgentWorker 信号 | MainWindow 槽 | UI/状态作用 |
|---|---|---|
| `log_signal(int,str,str,str,str)` | `update_log()` | 保存紧凑工具日志；step=0 的系统日志显示在日志框 |
| `code_signal(str,str)` | `update_code()` | 打开/刷新普通代码 Tab |
| `diff_signal(str,str,int,int)` | `update_diff()` | 建立未应用 Diff 暂存状态并渲染颜色 |
| `status_signal(str,str)` | `update_status()` | 更新工具栏状态点和 QStatusBar |
| `confirmation_signal(str)` | `_mark_confirmation_pending()` | 显示等待提示及应用/拒绝按钮 |
| `snapshot_signal(object,str)` | `_store_agent_snapshot()` | 保存本轮初始快照与时间戳 |
| `finished_signal(bool,str)` | `_handle_worker_finished()` | 写入 Assistant 总结并恢复控件 |
| `progress_signal(int,str)` | `_append_process_update()` | Goal/深度模式高层过程摘要 |
| `tool_status_signal(str,str,str)` | `update_tool_status()` | 覆盖显示当前/最近工具状态 |

#### Agent 写入审批链

```text
run_agent 发出 tool_call(write_file)
  → AgentWorker._handle_update()
  → AgentWorker._prepare_interactive_write()
      → 读取当前磁盘内容
      → generate_unified_diff()
      → emit diff_signal + confirmation_signal
      → Worker 阻塞在 threading.Event（每 0.1 秒检查中断）
  → MainWindow.update_diff() + _mark_confirmation_pending()
  → 用户点击 applyButton 或 rejectButton
  → MainWindow.confirm_signal.emit(True/False)
  → AgentWorker.resolve_write_confirmation()
  → Event 放行
  → ToolRegistry 的 confirm_write 回调取得已准备的决定
      ├─ True：执行 write_file()，再发 tool_result/code_signal
      └─ False：返回 error.code=user_aborted，不写盘，Agent 可继续下一轮
```

## 4. 主题与样式机制

### 4.1 应用范围

QSS 是**应用级全局样式**：

1. `main.run_gui()` 启动时先执行 `app.setStyleSheet(DARK_THEME)`。
2. `MainWindow._apply_theme()` 调用 `get_theme(theme_name)`，再对 `QApplication.instance()` 调用 `setStyleSheet(stylesheet)`。
3. `theme_name` 存在 QSettings 的 `ui/theme`，取值仅为 `light` 或 `dark`。

少量动态颜色不完全依赖 QSS：状态指示器和工具状态按钮使用控件级 `setStyleSheet()`；日志、深度摘要和 Diff 使用 HTML 内联颜色。主题切换后 `_apply_theme()` 会重新渲染当前会话、全部代码 Tab、工具状态和顶栏状态。

### 4.2 语义色板

| 语义键 | Light | Dark | 主要用途 |
|---|---|---|---|
| `background` | `#f7f7f8` | `#343541` | 窗口、会话区 |
| `code_background` | `#ffffff` | `#202123` | 代码、树、输入类控件 |
| `panel` | `#ffffff` | `#2d2d3a` | 工具栏、日志、按钮 |
| `text` | `#202123` | `#ececf1` | 主文本 |
| `muted` | `#6e6e80` | `#acacbe` | 次要文本、滚动条 |
| `success` | `#10a37f` | `#19c37d` | 成功、新增行、应用按钮 |
| `error` | `#d00e17` | `#ff6b6b` | 错误、删除行、拒绝按钮 |
| `accent` | `#10a37f` | `#19c37d` | 焦点、选择、强调 |
| `purple` | `#7c3aed` | `#c4b5fd` | Diff hunk/思考语义 |
| `warning` | `#b26a00` | `#f5c26b` | 等待确认、Diff 标题 |
| `border` | `#d9d9e3` | `#565869` | 边框和 Splitter handle |
| `hover` | `#ececf1` | `#40414f` | 悬停、选中 |
| `pressed` | `#d2d2d9` | `#4d4d5c` | 按压态 |
| `button_text` | `#ffffff` | `#ffffff` | 实心按钮文字 |
| `user_bubble` | `#e9f6f1` | `#244c43` | 用户消息气泡 |
| `user_bubble_text` | `#163d34` | `#e7fff7` | 用户消息文字 |
| `assistant_bubble` | `#f1f1f4` | `#2b2c38` | Assistant 气泡 |
| `system_bubble` | `#fff7e8` | `#443a29` | 系统/等待消息气泡 |
| `thinking_background` | `#f0f0f0` | `#40414f` | 深度过程容器 |

QSS 通过类选择器和 objectName 选择器覆盖 `QMenuBar`、`QToolBar`、输入控件、`QTreeView`、`QTabWidget`、`QSplitter`、滚动条，以及 `#userBubble`、`#codeEmptyPage`、`#thinkingContainer` 等语义节点。

## 5. 已知边缘情况与修复记录

### 5.1 已解决问题

| 问题 | 根因 | 当前解决方案 |
|---|---|---|
| 空代码区左上角出现不可交互的圆角矩形/异色横条 | 未加入布局的兼容 `QTextEdit` 和空 `QTabWidget` 会保留 Qt 默认 `100×30` 几何并可能被绘制 | `_empty_code_view.hide()`；零 Tab 时 `_sync_code_stack()` 对 `codeTabs` 执行 `removeWidget()+hide()`，首个 Tab 时再 `addWidget()+show()`。没有仅靠 `setMinimumSize(0,0)` 掩盖问题 |
| Agent 修改后看不到 Diff 或审批按钮 | GUI 的 interactive 标志曾默认关闭 | GUI 将 `interactive_confirmation=True` 固定开启，Action 只读；新建和修改文件均在写盘前生成 Diff 并等待决定 |
| 拒绝修改仍可能丢失原始预览 | Diff 状态未稳定保存写前内容 | `update_diff()` 从受限工作区重新读取原始内容；拒绝后清除 Diff 但保留原内容，磁盘不变 |
| Agent Diff 与用户手动编辑混在同一保存逻辑 | 同一个 QTextEdit 同时承担预览和编辑，但缺乏状态区分 | `_tab_previews` 增加 `workspace_backed/base_sha256/dirty/pending`；手动保存和 Agent 审批分离 |
| `QFont::setPointSize(-1)` 警告 | 某些系统固定宽度字体仅有 pixel size，point size 为 -1 | `_fixed_width_font()` 在 pointSizeF≤0 时设置 10pt；日志和代码编辑器统一使用该入口 |
| 未决 Diff 关闭 Tab/窗口会静默丢失 | 关闭路径没有检查暂存状态 | `_tab_has_pending_diff()` 与 `_confirm_discard_pending()` 在 Tab、工作区切换和窗口关闭时阻止静默丢弃 |
| 重复 ToolCall 造成死循环 | 模型可能重复同一规范化调用 | SHA-256 规范签名计数；超过 `max_same_call` 返回 `repeated_call_limit` 并终止 |
| Assistant tool_calls 与 Tool 结果被上下文裁剪拆开 | 逐消息裁剪破坏原生协议 | `group_protocol_units()` 将 Assistant 与连续 Tool 结果组成不可拆分单元 |
| 路径 `../`、绝对路径或符号链接逃逸 | 直接拼接字符串不能保证边界 | `Path.resolve()` + `relative_to()`；捕获 ValueError/OSError，拒绝越界 |
| Shell 超时后子进程残留/输出管道堵塞 | 仅终止父进程或等待完整输出 | POSIX 新会话+进程组终止；Windows `CREATE_NEW_PROCESS_GROUP`+`taskkill /T /F`；后台线程持续排空并保留头尾 |
| 快照仅有哈希不能实际回退 | 哈希清单没有文件内容 | 进程私有临时备份、哈希复验、原子还原，并删除快照后新建的普通文件 |
| 完整历史会话无法删除 | 只有单条消息删除入口 | `ConversationStore.delete_conversation()` + `deleteSessionButton`；最后一个会话删除后自动补空会话 |

### 5.2 尚存限制和协作注意事项

1. **会话重启即清空**：`ConversationStore` 会读取 QSettings，但 MainWindow 随后无条件 `reset()`。若未来要求跨启动历史，必须先移除或条件化该调用，并补迁移测试。
2. **窗口关闭未检查手动 dirty 状态**：关闭单个 Tab、切换工作区会询问是否放弃手动修改；当前 `closeEvent()` 只检查 pending Diff，直接退出窗口仍可能丢弃未保存的手动编辑。
3. **本地密钥风险**：被 Git 忽略的 `set-env.ps1` 当前含硬编码 API Key。不得把该文件加入版本控制或复制到文档；应立即在供应商控制台轮换密钥，并改用进程环境/安全凭据存储。
4. **不是 OS 沙箱**：`run_command()` 有 allowlist、`shell=False` 和环境清洗，但允许的 `python`/`git` 仍可执行项目代码；不应把它视作对恶意代码的容器隔离。
5. **快照生命周期**：备份只在当前进程注册并于退出时清理；不跟随符号链接，也不是持久版本控制系统。
6. **大文件限制**：`write_file`/拖拽默认上限 2 MiB；`read_file` 单页至多 500 行、50000 字符；Diff 默认截断至 12000 字符。
7. **工作区 cwd 是进程级状态**：GUI 切换工作区会 `os.chdir()`，可能影响同进程内其他相对路径代码；关闭窗口时尝试恢复 `_launch_cwd`。
8. **入口异常处理有限**：`main.py` 没有 `sys.excepthook` 或统一 GUI 异常对话框；Worker 会把其 `run()` 内异常转为失败信号，但 GUI 主线程意外异常仍走 Qt/Python 默认处理。

## 6. 关键依赖

### 6.1 第三方依赖

| 依赖 | 版本约束 | 用途 | 实际引用位置 |
|---|---|---|---|
| `openai` | `>=1.40,<3` | 原生 OpenAI 兼容 Chat Completions 客户端；对接 DeepSeek/百炼及 Tool Calling | `providers/openai_compatible.py` |
| `PySide6` | `>=6.6.0` | Qt Widgets、Signal/Slot、QThread、QSettings、QSS 桌面 GUI | `gui/*`、部分 GUI 测试 |
| `pytest` | `>=8,<9`，可选 `test` 依赖 | 单元和无网络 GUI 回归测试 | `tests/*`、示例测试 |

当前项目**没有**引用 LangChain、LlamaIndex、OpenAI Agents SDK、AutoGen、CrewAI、QScintilla、requests 或 python-dotenv。

### 6.2 关键标准库

| 标准库 | 用途 |
|---|---|
| `subprocess`、`signal`、`threading` | 本地命令、进程树终止、输出读取及 GUI 写入等待 |
| `pathlib`、`tempfile`、`os`、`shutil`、`stat` | 路径安全、原子写、快照和权限恢复 |
| `hashlib` | 文件 SHA-256 与工具调用签名 |
| `json`、`dataclasses` | 协议、配置、状态和 QSettings 序列化 |
| `difflib` | Unified Diff |
| `time`、`atexit` | 重试/超时、墙钟限制和临时快照清理 |

## 7. 外部 AI 协作入口速查

| 修改目标 | 首选入口 | 必须同时检查 |
|---|---|---|
| 改 UI 布局/objectName | `gui/main_window.py`、`gui/widgets.py` | `gui/theme.py`、`tests/test_gui.py` |
| 改消息/会话 | `gui/session.py`、`gui/widgets.py` | `_render_active_session()`、QSettings 重置行为 |
| 改 Agent 生命周期 | `agent/loop.py` | `gui/worker.py` 事件映射、`tests/test_agent_loop.py` |
| 改 Tool Schema | `tools/schemas.py` | `agent/parser.py`、ToolRegistry 和 Provider 请求 |
| 改文件写入 | `tools/filesystem.py` | SHA 乐观锁、Diff meta、GUI 手动保存和 Agent 审批 |
| 改 Shell | `tools/shell.py` | Windows/POSIX 双平台进程树、环境清洗、输出上限 |
| 改主题 | `gui/theme.py` | HTML 内联颜色的重新渲染路径 |
| 改快照 | `utils/snapshot.py` | MainWindow 单槽快照生命周期、符号链接策略 |

最小完整回归命令：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests\ -q
```
