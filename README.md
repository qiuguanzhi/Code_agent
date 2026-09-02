# Code-Agent · Cerebro

轻量级本地编程智能体：不依赖 Agent 框架，以 OpenAI-compatible Tool Calling 连接 DeepSeek/百炼，由本地 Python 完成上下文、工具、安全审批与 GUI 编排。

## ✨ 核心特色

### 🧠 深度思考 + 流式推理
**快速/深度双模式**；深度推理逐段进入可折叠思考区，体验接近 DeepSeek 网页版。

### 📋 高亮 Diff 审批 + 批量应用
**多文件 Unified Diff** 显示 `+X -Y`，修改先暂存，再“全部应用/拒绝”。

### 📸 快照系统
**任务前自动快照**，手动保存会覆盖旧记录；可按精确时间点一键回退。

### 📁 工作区管理
**拖拽导入/仅预览**；支持文件树新建、删除、折叠，并与代码 Tab 联动。

### 🧩 Skill 技能系统
**UI 手动增删 Skill**：填写名称、描述、代码与权限，Agent 可像工具一样调用。

### 💬 多轮会话
**Session 自动保存**消息、日志与思考，支持切换/删除；启动时建立干净空会话。

### 🟢 上下文可视化与压缩
**Token 圆环**按绿/黄/红显示占用；超过 80% 自动摘要并记录释放量。

### 🎨 沉浸式主题
**Cyber Cortex** 科技蓝/荧光青主题，含神经水印、任务态能量光晕及亮暗切换。

### 🚀 品牌启动动画
**2.5 秒置顶序列**：思维脉冲、Terminal 接入、打字机 Logo；点击可跳过。

### ⏱️ 循环与超时保护
**默认 200 步/20 分钟**；到限询问，继续时增加 50 步且可重复。

### 🔧 实时状态反馈
**工具状态栏**展示执行/错误详情；运行时发送键变为圆形停止键，可随时中断。

### 🛡️ 原生本地工具
**文件工具**支持工作区约束、分页、SHA-256 乐观锁；Shell 使用 allowlist、环境清洗与超时；`difflib` Diff 可截断。

## ⚡ 快速开始

要求 Python 3.10+。API Key 仅通过环境变量配置。

### Windows PowerShell
```text
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
$env:AGENT_MODEL="你的模型"; $env:DEEPSEEK_API_KEY="你的密钥"
python main.py --gui
```

### macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
export AGENT_MODEL="你的模型" DEEPSEEK_API_KEY="你的密钥"
python main.py --gui
```

百炼改用 `AGENT_PROVIDER=bailian`、`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`。CLI 示例：
```bash
python main.py --cli --workspace ./examples/buggy_calculator "修复错误并运行测试"
```

## 📖 使用与快捷键

打开工作区，选择思考模式，输入任务并审批 Diff；用快照回退风险修改。

| 快捷键 | 功能 |
|---|---|
| `Ctrl+S` | 保存当前文件 |
| `Enter` | 发送任务 |
| `Shift+Enter` | 换行（当前单行输入栏暂不支持） |
| `Ctrl+O / Ctrl+R` | 打开工作区 / 回退快照 |

## 🏗️ 项目架构
```text
agent/      主循环、状态、上下文与解析
gui/        PySide6 窗口、Worker、会话与主题
providers/  DeepSeek/百炼兼容适配器
tools/      文件、Shell、Diff 与注册表
skills/     内置/用户 Skill 及权限路由
utils/      快照、Token、Diff 公共能力
tests/      FakeProvider 与离线回归测试
```

## 🧰 技术栈

Python 3.10+、PySide6、OpenAI Python SDK、pytest；无 LangChain 等编排框架。

## 📄 许可证

仓库暂未包含 `LICENSE`；公开发布前请由维护者选择并补充许可证。

## 🐞 维护与反馈

请通过仓库 Issue 提交复现步骤、系统版本、日志和期望行为；勿上传 API Key、隐私文件或工作区源码。
