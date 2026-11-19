# Hermes Agent ☤ (AI Agent Harness 源码分析与调试指南)

本项目基于 [Nous Research](https://nousresearch.com) 开源的 **Hermes Agent**，定位为 **AI Agent Harness 架构源码分析与调试学习工程**。

LLM 本身是无状态的概率推理内核（Stateless Reasoning Kernel）；将该内核封装为具备持久记忆、长程规划、环境感知、工具调度、容灾自愈与安全隔离的运行时系统，即为 **AI Agent Harness（智能体控制底座）**。

---

## 🎯 源码分析核心入口 (Entry Points)

| 核心模块 | 路径 | 源码分析与学习重点 |
|---|---|---|
| **核心执行外观** | `run_agent.py` | `AIAgent` 类装配、上下文参数解析、Turn Lease 租约锁 |
| **9 步回合状态机** | `agent/conversation_loop.py` + `agent/turn_*.py` | 思考-行动-观察循环、流式打断、角色交替不变式、空响应守卫 |
| **提示词与前缀缓存** | `agent/prompt_builder.py` | 三层静态提示词组装、动态变量旁路注入（保护 Prefix Cache） |
| **上下文分级压缩** | `agent/turn_context_compaction.py` | 增量微压缩（折叠工具大输出）与 4 阶段总结压缩机制 |
| **工具注册与调度** | `tools/registry.py` & `model_tools.py` | 导入期自注册、并发派发、有状态工具内联拦截机制 |
| **编程式工具卸载** | `tools/execute_code.py` | 本地 Unix Domain Socket RPC 沙箱执行，多步逻辑程序化卸载 |
| **单写权威与状态库** | `hermes_state.py` & `gateway/run.py` | SQLite WAL 模式、`runtime_epoch` 隔离栅栏、FTS5 局部损坏自愈 |

---

## 🚀 本地开发与环境准备

### 1. 环境激活 (PM-managed Virtualenv)

项目采用锁定的 PM 依赖管理，支持 Python 3.11 至 3.14：

```bash
# 激活当前工作区的开发环境（POSIX Shell / Bash / Zsh）
source ./activate

# 若使用 fish:
# source ./activate.fish

# 若使用 PowerShell (Windows):
# .\activate.ps1
```

> **提示**：激活后 `hermes` 会成为当前 worktree 的局部函数；可通过 `which hermes` 或 `hermes doctor` 验证环境正常。

### 2. 本地调试与隔离配置

避免调试污染主机全局的 `~/.hermes` 配置，建议指定独立的测试 `HERMES_HOME`：

```bash
# 指定独立运行与状态目录（Profile 隔离）
export HERMES_HOME="$PWD/.hermes_dev"
export HERMES_DEBUG=1
```

在本地创建 `.env` 文件填入调试所用的模型密钥（按需配置）：
```bash
cp .env.example .env
# 编辑 .env，填入测试用 API 密钥，例如：
# OPENAI_API_KEY=sk-...
# OPENROUTER_API_KEY=sk-...
```

---

## 🐞 调试方式 (How to Debug & Run)

### 方式一：独立 Python 脚本断点调试 Harness 核心内核（最推荐）

绕过复杂的 CLI/TUI 前端，直接通过脚本单步调试 `AIAgent` 编排控制回路（支持 VS Code / PyCharm / pdb）：

创建临时调试脚本 `debug_harness.py`：

```python
import os
from run_agent import AIAgent

# 1. 实例化核心智能体（Narrow Waist）
agent = AIAgent(
    model="gpt-4o-mini",          # 或任意自定义模型 / 本地端点
    # base_url="http://localhost:11434/v1", # 本地 Ollama 调试示例
    temperature=0.2,
    max_iterations=10,
    quiet_mode=False,
)

# 2. 在 agent/conversation_loop.py 的 run_conversation 打断点
# 3. 触发一次多步工具调用交互
result = agent.run_conversation("列出当前目录下的文件，并统计数量")

print("\n--- [Debug Response] ---")
print("Final Output:", result.get("final_response"))
print("Turn Count:", len(result.get("messages", [])))
```

使用 pdb 或 IDE 断点调试：
```bash
python -m pdb debug_harness.py
```

### 方式二：交互式 CLI 调试单步交互

直接启动命令行 REPL，观察控制台流式工具调用输出与日志：

```bash
# 启动 CLI（添加 verbose 日志输出）
python cli.py

# 快速切换调试模型或查看当前状态
/model openai:gpt-4o-mini
/status
/usage
```

### 方式三：运行窄范围契约测试（验证单模块行为）

不要直接运行裸 `pytest`，必须使用官方包装脚本 `./scripts/run_tests.sh`，自带环境守卫与沙箱隔离：

```bash
# 1. 调试回合状态机与消息不变式
./scripts/run_tests.sh tests/agent/test_turn_lifecycle.py

# 2. 调试工具注册与并发调度
./scripts/run_tests.sh tests/tools/test_registry.py

# 3. 调试上下文压缩与微压缩
./scripts/run_tests.sh tests/agent/test_context_compaction.py

# 4. 调试单写者权威与会话状态库
./scripts/run_tests.sh tests/gateway/test_session_authority.py
```

### 方式四：静态代码健康检查

在修改核心状态机或工具代码后，运行项目的严格校验工具（包含 `HX` 架构健康规则）：

```bash
# 运行全部代码质量检查（包含 ruff, 类型检查, 边界检查）
python scripts/check
```

---

## 📖 Harness 架构与开发全景专栏

本项目沉淀了面向生产级 AI Agent Harness 框架从零研发与二次开发的四卷全景指南：

1. 📘 **[卷一：核心编排回路与快速上手指南 (docs/harness/01_core_loop_and_quickstart.md)](docs/harness/01_core_loop_and_quickstart.md)**
   - 无状态推理内核 vs 有状态控制底座，单核窄腰（Narrow Waist）范式与三大黄金不变式。
   - 9 步回合状态机（Turn Lifecycle）生命周期与流式超时强杀机制。
   - 180 行完整可执行的最小可用闭环（Minimal Viable Harness）从零实现。
   - 本地开发环境隔离、单步断点调试脚本（`debug_harness.py`）与单模块契约测试实战。

2. 📗 **[卷二：提示词工程、上下文压缩与多层记忆体系 (docs/harness/02_prompt_context_and_memory.md)](docs/harness/02_prompt_context_and_memory.md)**
   - 三层静态提示词组装架构（Layer 1 静态、Layer 2 冻结、Layer 3 动态旁路）保活厂商前缀缓存（Prefix Caching）。
   - 上下文窗口治理：大工具输出增量微压缩（Micro-Compaction）与 4 阶段深度总结压缩（带确定性滑动窗口降级）。
   - L1-L3 多层记忆架构：L1 常驻用户画像、L2 会话级 FTS5 倒排检索、L3 技能自演进闭环（Curator）。

3. 📙 **[卷三：工具系统、编程式代码卸载与子智能体编排 (docs/harness/03_tools_execution_and_subagents.md)](docs/harness/03_tools_execution_and_subagents.md)**
   - 双轨制工具调度：公开注册表（`tools/registry.py`）vs 有状态内联拦截（`inline_tool_executors.py`）。
   - `ThreadPoolExecutor` 受控并发调度、原始请求顺序严格对齐回填与串行屏障机制。
   - 编程式认知卸载（Programmatic Tool Calling - PTC）：通过 Unix Socket RPC 将多步循环下放本地沙箱执行。
   - 子智能体（Subagent）隔离编排：独立上下文、Git Worktree 工作区分支、停滞看门狗与深度防套娃熔断。

4. 📕 **[卷四：多模型适配、网关单写权威、容灾自愈与纵深防御 (docs/harness/04_providers_gateway_disaster_recovery_and_security.md)](docs/harness/04_providers_gateway_disaster_recovery_and_security.md)**
   - 规范中间表示（Canonical IR）与出站动态适配（OpenAI, Anthropic, Bedrock, Ollama）。
   - 流式响应适配与多线程 FD 安全的套接字级中断强杀 `shutdown(SHUT_RDWR)`。
   - 跨 25+ 平台多端接入架构：`gateway.sock` 单一写中枢与 `runtime_epoch` 隔离栅栏。
   - SQLite WAL 持久化高并发设计与 FTS5 全文索引损坏自愈重建。
   - 8 层纵深防御体系：硬性禁令（`HARDLINE_PATTERNS`）、人机审批门禁与沙箱敏感凭据剥离。

---

## 📚 全景架构总纲与工程手册

- 🌐 **[Harness 架构与工程实现全景总纲 (docs/harness/harness-deep-dive.md)](docs/harness/harness-deep-dive.md)**（**一文通览全部子系统**）：
  - **内核隐喻与拓扑**：CPU ALU vs Agent OS Kernel 对照矩阵、六大开发公理、三大黄金不变式与“单核窄腰”端到端拓扑图。
  - **编排与上下文治理**：9 步回合状态机、Persist-Before-Execute、三层提示词 KV Cache 保活、微压缩与 4 阶段单次总结压缩（含滑动窗口降级）、L1-L3 记忆与 Curator 技能自演进状态机。
  - **执行、隔离与多模型**：双轨工具调度与串行屏障、PTC 本地 Socket RPC 卸载、6 类执行沙箱、子智能体四重隔离、Canonical IR 出站归一化与 FD 安全 `shutdown(SHUT_RDWR)` 强杀。
  - **权威、容灾、安全与全源码映射**：`gateway.sock` 单写权威、Epoch 纪元栅栏、入站双门禁、多端生态（CLI/TUI/Desktop/ACP/25+ IM/Cron）、SQLite WAL 两级损坏自愈、8 层纵深防御与 20 大模块源码全景映射总表。
- 🛠️ **[Harness 开发者实施手册 (docs/harness/harness-developer-notes.md)](docs/harness/harness-developer-notes.md)**：
  - 10 个核心子系统的工程决策、实现规格参数与避坑公理。
  - 常见技术痛点与踩坑指南（避免 Prompt 分裂、死锁、状态污染）。

- 📋 **[仓库工作指南 (AGENTS.md)](AGENTS.md)**：
  - 完整的系统架构图、数据流图、开发规范与测试契约。
---

## 许可证

本项目遵循 MIT 许可证，详见 [LICENSE](LICENSE)。
由 [Nous Research](https://nousresearch.com) 构建。
