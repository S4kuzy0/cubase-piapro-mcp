# Cubase Piapro MCP

本地运行、优先直接处理工程数据的 MCP。适配 Windows、Cubase LE 14 和 Piapro Studio Standard；Codex 通过 stdio 启动服务。

## 功能

- VSQX 3/4：读取音符、歌词、音素、声库表；修改已有音符；插入、删除音符；替换或合并控制曲线。VSQX4 可用已有声库表新建乐谱。
- 旧版二进制 PPSF：直接读取及修改音符音高、位置、时长、力度、歌词、音素。先要求原始文件精确二进制往返；修改时按长度差保留事件与块的尾部字节；时序修改同步已验证对应关系的编辑器副本，其余块保留。不经通用乐谱模型重建工程。
- 标准 MIDI：匹配同通道的音符起止事件，修改音高、时长、位置和力度，保留其他消息。
- CPR：借助 CubaseTools 核心做部分只读解析，保留重名轨道。结果为启发式提取，不能作为完整工程或实时状态。
- Piapro：动态发现 Windows 窗口、菜单与标准文件对话框，用原生激活通知和窗口消息导入、导出。音符与文件路径不靠坐标操作；Windows 拒绝激活时，可启用仅点击已核验标题栏的焦点后备。WAV 为当前选中 Piapro 音轨。
- Cubase：附带 MIDI Remote 脚本，以独立虚拟 MIDI 端口控制播放、停止、录音及选中通道的音量、声像、静音、独奏。成功需要宿主反馈。

文件写入与软件实时状态分别报告。编辑磁盘文件后，需要导入并回读验证，才能确认软件中的状态。

## 安装

需要 Python 3.12 或更新版本。建议使用独立虚拟环境，不修改系统 Python：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.lock
.venv/Scripts/python.exe run_server.py
```

`requirements.lock` 是本机已验证的 Windows/Python 3.12 依赖快照。其他平台可使用 `pip install .`，实时 Piapro 控制仍限 Windows。

### 接入 Codex

```powershell
.venv/Scripts/python.exe scripts/install_codex.py --python .venv/Scripts/python.exe --state-dir state --focus-click
```

`--focus-click` 允许受限的标题栏激活后备；省略则在原生激活失败时返回 `FOCUS_REQUIRED`。

安装脚本先备份现有 Codex 配置，再使用 `codex mcp add` 注册 `cubase-piapro`。本机已经完成注册，当前聊天实际发现工具并执行过调用。`codex mcp get cubase-piapro --json` 可检查注册结果。新配置通常需在新会话中加载；注册成功不能代替工具握手验证。

### 实时 MIDI 控制

1. 启动 loopMIDI 并保持后台运行，创建两个独立虚拟 MIDI 端口：`CP MCP To Cubase`、`CP MCP From Cubase`。
2. 运行 `.venv/Scripts/python.exe scripts/install_midi_driver.py`。
3. 在 Cubase 的 MIDI Remote 中重新加载脚本，或正常保存后重启 Cubase。
4. 不要把控制端口加入乐器轨的 All MIDI Inputs。
5. 调用 `daw_get_status`，只有 `midi.connected` 为真才能使用反馈控制。

loopMIDI 主程序可安装到其他数据盘；迁移时保留上述端口名称并更新自启动路径，MCP 不依赖其安装目录。系统 MIDI 驱动及 Windows Installer 缓存仍由 Windows 管理。

状态中的数值采用 MIDI Remote 的归一化值，不代表音量分贝。控制对象为当前选中通道，不能用 CPR 解析出的序号代替它。

## 使用流程

先调用 `inspect_project`，保留返回的 `sha256` 和音符 `id`。随后调用 `edit_notes`、`transpose_notes` 或 `set_control_curve`，建议先写入新文件。同路径写入会自动生成并校验原内容备份；源哈希不匹配时拒绝写入。

`piapro_import` 会先导出当前 PPSF 快照，再从经哈希验证的暂存副本导入。PPSF 覆盖提示仅在快照完成后自动确认；文件错误会返回错误码和快照路径。VSQX/MIDI 的导入可能追加轨道；其返回值明确标记尚未验证。继续调用 `piapro_export` 回读并核对音符和参数，必要时用原 PPSF 快照恢复。空乐曲首次导入时，可设置 `initialize_empty=true`；只有乐谱导出均禁用、无可撤消操作且宿主回传已停止时才接受。

备份、快照与本机安装记录位于 `CUBASE_PIAPRO_STATE_DIR`，未设置时位于本机应用数据目录中的 `CubasePiaproMCP`。备份恢复使用 `list_recovery_copies` 与 `restore_recovery_copy`。恢复当前版本前也会再次备份。

## 限制

- 不提供任意 CPR 原生写入、Piapro NT ZIP 编辑、声库逆向解析或音色模型提取。
- 原生 PPSF 插入/删除音符及批量新增控制点暂未开放；这些操作使用 VSQX。原生时序修改要求目标音符的两份时序一致，并保持在区段原有音符终点范围内；否则改用 VSQX。
- 实时工具要求 Cubase 中已加载 Piapro，且恰好打开一个 Piapro 编辑器。首次建 Cubase 工程、插入插件及选择要导出 WAV 的音轨仍可能需要界面操作；批量编辑不靠逐个点击音符。
- 部分插件自绘对话框和其他语言版本尚未验证；遇到未支持对话框返回明确错误，不假称成功。
- 未保存的 Cubase 状态不会出现在 CPR 文件中。第三方插件的全部内部参数也未必能读取。
- 同时从其他聊天或手工修改同一现场会影响结果；文件哈希和 MCP 进程间锁不等于软件级事务锁。

## 验证与维护

```powershell
.venv/Scripts/python.exe -m pip install pytest
.venv/Scripts/python.exe -m pytest -q
```

测试覆盖参数与未知 XML 保留、旧哈希拒绝、备份恢复、MIDI 起止配对、控制曲线、新乐谱和真实 stdio MCP 握手。旧版 PPSF 测试通过环境变量 `CP_TEST_PPSF` 指向本机原始资料；原始资料不随项目分发。

16 个工具及示例见 [工具与架构](docs/architecture.md)。实际验证状态见 [验证记录](docs/validation.md)，架构与决策见 [项目记忆](docs/project-memory.md)。第三方来源和许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
