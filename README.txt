Code-Agent（Cerebro）项目说明

Git 仓库地址：
https://github.com/qiuguanzhi/Code_agent

一、运行方式
环境要求：Python 3.10 及以上，支持 Windows、Linux 和 macOS。

1. 安装：
python -m venv .venv
pip install -e ".[test]"

2. 配置：
参照 .env.example，在本机环境变量中设置模型厂商、模型名称、网关地址及访问凭据。真实凭据不得写入项目文件或提交到 Git。

3. 启动桌面版：
python main.py --gui
也可直接运行 python main.py。

4. 启动命令行版：
python main.py --cli --workspace ./examples/buggy_calculator "修复除零错误并运行测试"

5. 运行测试：
pytest tests/ -q

二、特色功能
本项目未使用 LangChain、LlamaIndex、OpenAI Agents SDK、AutoGen、CrewAI 等 Agent 框架，仅使用模型厂商原生客户端和 Tool Calling 协议。Agent 主循环、消息历史、上下文裁剪、工作记忆、参数解析、重复调用检测、指数退避、步骤与墙钟终止条件均自行实现。

read_file、write_file、delete_file 和 run_command 全部在本地执行。文件工具具备工作区路径防逃逸、分页读取、流式哈希、原子写入和 SHA-256 乐观锁；命令工具使用 shell=False、可执行文件白名单、超时、进程组终止和头尾截断。

桌面版提供暗色/亮色主题、多会话、文件树、手动编辑、文件引用、实时日志、模型回答与深度推理流式显示。文件修改先暂存为多文件 Unified Diff，由用户统一应用或拒绝；任务前自动创建快照，可安全回退。

工具栏“🧩 Skill 管理”支持启用/禁用、查看权限、手动添加和删除用户 Skill。添加表单包含唯一名称、自然语言描述、Python 执行逻辑及文件/命令/网络权限声明；代码必须定义且只定义一个 Skill 子类。应用先执行语法与 AST 安全检查，再在仅注入 Skill、SkillResult、AgentContext 和少量安全内建函数的命名空间中加载；import、双下划线内部属性、os/subprocess 直连均被拒绝，能力只能通过 context.call_tool() 进入既有权限门。危险动态函数会要求二次确认，但不会因此自动获得系统能力。验证通过的源码以 skill_<name>.py 原子保存到 skills/user/，重启可发现；内置 Skill 标记为系统 Skill 且不可删除。运行时用户 Skill 默认被 .gitignore 排除，避免误提交用户代码。

启动动画使用 800×500 的无边框置顶 Tool 窗口，固定在主显示器中央，点击任意位置可立即跳过；主窗口显示后会取消置顶属性，避免影响后续操作。窗口管理器不支持全局置顶时会输出警告并降级为普通无边框窗口。

模型空响应会进行两次自动重试，并在事件日志中给出无 choices、缺少 message、工具调用格式异常、响应超时或上下文问题等分类诊断。模型/API 错误后可点击输入栏旁的“重试”按钮，以新的 Agent 状态重新执行上一任务，同时保留当前会话记录。模型真实输入上限可通过 AGENT_MODEL_INPUT_TOKENS 配置；只有网关明确支持时才应启用 AGENT_USE_MAX_COMPLETION_TOKENS=1。

三、其它说明
支持 DeepSeek 与阿里云百炼 OpenAI 兼容接口。测试默认使用 FakeProvider 和临时工作区，不消耗真实 API。当前自动化回归结果为 173 passed、1 skipped；跳过项是测试环境无符号链接权限，不影响正常功能。
