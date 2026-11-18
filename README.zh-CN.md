<p align="center">
  <img src="assets/banner.png" alt="Hermes Agent" width="100%">
</p>

# Hermes Agent ☤ (AI Agent Harness 源码分析与学习工程)

<p align="center">
  <a href="https://hermes-agent.nousresearch.com/docs/"><img src="https://img.shields.io/badge/Docs-hermes--agent.nousresearch.com-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="https://discord.gg/NousResearch"><img src="https://img.shields.io/badge/Discord-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord"></a>
  <a href="https://github.com/NousResearch/hermes-agent/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://nousresearch.com"><img src="https://img.shields.io/badge/Built%20by-Nous%20Research-blueviolet?style=for-the-badge" alt="Built by Nous Research"></a>
</p>

本项目基于 [Nous Research](https://nousresearch.com) 开源的 **Hermes Agent** 打造，**核心定位是源码分析与 AI Agent Harness 运行时架构学习**。

大语言模型（LLM）本身是无状态的概率推理内核（Stateless Reasoning Kernel）；而将其封装为具备持久记忆、长程规划、环境感知、工具执行、故障自愈与安全边界的运行时底座，即为 **AI Agent Harness（智能体控制底座）**。

---

## 📚 重点导读与 Harness 架构文档

本项目重点沉淀了 Harness 设计思想与工程落地的核心文档：

| 文档 | 路径 | 核心内容 |
|---|---|---|
| **代码库工作指南** | [AGENTS.md](AGENTS.md) | 仓库核心架构、单核窄腰原则、9 步回合状态机、消息流不变式与测试规范 |
| **Harness 架构深度解析** | [harness-deep-dive.md](website/docs/developer-guide/harness-deep-dive.md) | 从操作系统视角深度剖析调度器、前缀缓存、内存分页、系统调用与单写权威 |
| **开发者实施手册** | [harness-developer-notes.md](website/docs/developer-guide/harness-developer-notes.md) | 10 个核心子系统的工程决策、实现规格（带生产级基线参数）与避坑指南 |

---

## 🏗️ 核心架构：单核窄腰（Narrow Waist）

```
[前端/接入层] CLI / TUI / Electron 桌面 / 25+ IM 网关 (Telegram/Discord/Slack) / ACP Server
                              │ (薄代理化，零 Prompt 拼接)
                              ▼
[会话权威层]  gateway.sock (单写者权威, SO_PEERCRED 鉴权, runtime_epoch 隔离栅栏)
                              │
                              ▼
[编排核心窄腰] AIAgent (run_agent.py) ➔ 9 步回合状态机 (agent/conversation_loop.py)
              ├── 提示词冻结与前缀缓存 (Prompt Caching & 动态旁路通道)
              ├── 上下文分级治理 (增量微压缩 + 4 阶段总结压缩 + 降级安全网)
              ├── 工具运行时与编程式卸载 (tools/registry.py + execute_code RPC)
              └── 多智能体派生 (Git Worktree 隔离 + 停滞检测看门狗)
                              │
                              ▼
[持久化与状态] SQLite WAL (SessionDB) + L1 核心画像 + L2 FTS5 检索 + L3 Skills 闭环
```

### 核心开发公理
1. **提示词绝对稳定**：System Prompt 在会话启动时构建并冻结，中途绝不修改，确保服务商前缀缓存（Prefix Caching）逐字节命中。
2. **执行过程全可观测**：采用结构化钩子暴露内部流转，编排回路与具体 UI 彻底解耦。
3. **任意时刻安全可中断**：流式生成与并发工具调用随时可安全中断；中断时丢弃未闭合脏状态，保证角色交替合法。
4. **单写权威与故障隔离**：单会长驻网关持有数据库写权，FTS5 损坏降级原生 `LIKE`，物理损坏毫秒级隔离转储并热新建库。

---

## 🛠️ 快速上手与环境配置

### 1. 环境初始化
```bash
# 激活 PM 环境（推荐 Python 3.11 - 3.14）
source ./activate          # macOS/Linux (fish 使用 activate.fish, Windows 使用 activate.ps1)

# 代码质量检查与健康校验（HX 规则、ruff、类型检查）
python scripts/check

# 运行测试套件（使用环境隔离沙箱，禁止直跑 bare pytest）
./scripts/run_tests.sh
```

### 2. 常用开发与运行命令
```bash
hermes              # 交互式 CLI 会话
hermes model        # 选择并切换 LLM 提供商与模型
hermes tools        # 查看/配置启用的工具集
hermes gateway      # 启动多平台消息网关
hermes doctor       # 环境与依赖健康诊断
```

---

## 📂 源码目录索引

- `run_agent.py` & `agent/`: 编排内核外观与 9 步回合生命周期实现 (`turn_*.py`)。
- `model_tools.py` & `tools/`: 工具注册表 (`registry.py`)、沙箱后端及代码执行卸载器。
- `hermes_state.py`: SQLite WAL 驱动的持久化会话库 (`SessionDB`) 与自愈逻辑。
- `gateway/`: 消息网关与多平台适配器，统一接入与单写者仲裁。
- `tui_gateway/` & `ui-tui/`: 终端用户界面（TUI）的 JSON-RPC 接入层。
- `apps/desktop/`: 基于 Electron 的桌面客户端桥接。
- `website/docs/developer-guide/`: 开发者长篇文档与架构沉淀。

---

## 许可证

本项目遵循 MIT 许可证，详见 [LICENSE](LICENSE)。
由 [Nous Research](https://nousresearch.com) 构建。
