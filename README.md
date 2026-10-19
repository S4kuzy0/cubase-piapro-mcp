# Cubase / Piapro MCP

**优先直接处理乐谱数据、带哈希校验与恢复副本的本地 MCP。** 读取和编辑 VSQX、旧版二进制 PPSF、标准 MIDI；在已验证的 Windows 布局中连接 Cubase 与 Piapro Studio。

稳定版 **0.5.0 / 33 个工具 / MIT**。不分发 Cubase、Piapro、声库、商业插件或用户歌曲。

## 能力与边界

| 功能 | 支持范围 |
| --- | --- |
| 乐谱文件 | VSQX 3/4 读取与编辑；MIDI 音符修改；旧版 PPSF 已验证布局的定点修改 |
| 文件保护 | 来源 SHA-256、未知布局拒绝、原子写入、可核验的覆盖备份与恢复 |
| Piapro | 原生导入、导出，歌手与逻辑音符表情；已验证 Miku EVEC 范围操作 |
| Cubase | MIDI Remote 宿主反馈、UID 轨道清单、选轨、走带、混音与受限路由 |
| CPR | 部分、启发式、只读检查；不提供任意原生 CPR 写入 |

文件写入和宿主现场更新分别报告；写文件成功不代表实时工程已经改变。完整工具、事务与错误处理见 [使用详解](docs/usage.md) 和 [架构](docs/architecture.md)。

## 安装

使用 Python 3.12+ 和独立虚拟环境。在 Windows PowerShell 中：

```powershell
git clone https://github.com/S4kuzy0/cubase-piapro-mcp.git
Set-Location cubase-piapro-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe run_server.py
```

服务通过 stdio 等待 MCP 客户端，不是网页服务器。`requirements.lock` 为历史 Windows/Python 3.12 实测快照；需要复现该环境时使用它。离线 wheel 见 [v0.5.0 Release](https://github.com/S4kuzy0/cubase-piapro-mcp/releases/tag/v0.5.0)。

### 先跑一个不需要 DAW 的例子

```powershell
.\.venv\Scripts\python.exe examples/offline_demo.py
```

示例自己生成短 MIDI，检查音符、按来源哈希移调到新文件，再回读核对；不读取个人乐谱、连接宿主或合成人声。输出在被忽略的 `work/offline-demo-*` 中，详见 [示例说明](examples/README.md)。

### 连接 MCP 与宿主

将 stdio 命令配置为虚拟环境 Python 的绝对路径，参数为本仓库 `run_server.py` 的绝对路径。Codex 使用者也可运行：

```powershell
.\.venv\Scripts\python.exe scripts/install_codex.py --python .venv/Scripts/python.exe --state-dir state
```

脚本会备份客户端配置并注册服务。先验证工具发现和文件读取；需要现场控制时再按 [安装与反馈说明](docs/usage.md#实时-midi-控制) 设置 loopMIDI、两个独立端口与 Cubase MIDI Remote 驱动。不要将控制端口加入乐器轨的 All MIDI Inputs。

## 版本适配

| 组件 | 已有实测基线 |
| --- | --- |
| 系统 | Windows；受限 UI 后备需要已解锁的交互桌面 |
| Cubase | LE 14.0.40；路由和添加对话框后备按英文布局核验 |
| Piapro | Studio Standard 2.0.5.10；EVEC UI 使用已验证中文设置布局 |
| 声库 | 本机已有 Miku V4/V4X；其他声库定义须自行捕获并核对 |

这些是验证基线，不是其他 Cubase/Piapro 版本或声库的兼容承诺。未知布局会拒绝。MIDI Remote 必须有实际宿主反馈，端口存在不等于连接成功。正常文件检查不要求启动 DAW。

## 稳定版与开发分支

- `main` 与 `v0.5.0`：本 README 所述稳定接口；main 的文档可持续更新。
- [`feat/vocal-to-score`](https://github.com/S4kuzy0/cubase-piapro-mcp/tree/feat/vocal-to-score)：另有插件控制、分离、转谱和对齐开发内容，以及独立 `audio-to-score/` 服务。它们尚未合并到本稳定包，不能在安装稳定版后假定这些工具存在。
- `audio-v0.1.0`：独立音频服务的版本，安装和依赖以对应分支文档为准，不属于 `cubase-piapro-mcp` 的 33 工具目录。

## 验证与贡献

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest tests/test_files.py -q
```

该子集使用合成 MIDI/VSQX，私有 PPSF 样本测试按环境变量跳过。完整历史 v0.5.0 验收记录了 154 项自动测试及独立实机验证，见 [验证记录](docs/validation.md) 和 [摘要](docs/validation-summary.json)；这不代表每个新克隆都附带私人样本或已在所有宿主复测。

提交中保留第三方通知，排除声库、音频、真实工程、状态目录、配置和备份。项目决策与历史边界见 [项目记忆](docs/project-memory.md)。遇到失败先检查返回的错误、快照和恢复核验，避免重复操作未知现场。

## 许可与来源

[MIT](LICENSE)。底层模型和模板复用了 LibreSVIP、CubaseTools、UtaFormatix 的许可组件，固定来源与通知见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。商业软件、API、声库及音乐素材的权利仍由各自权利方持有。
