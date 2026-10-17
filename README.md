# Cubase Piapro MCP

本地运行、优先直接处理工程数据的 MCP。当前版本 0.3.0，共 27 个工具；适配 Windows、Cubase LE 14 和 Piapro Studio Standard，Codex 通过 stdio 启动服务。

## 功能

- VSQX 3/4：读取音符、歌词、音素、声库表；修改已有音符；插入、删除音符；替换或合并控制曲线。VSQX4 可用已有声库表新建乐谱。
- 旧版二进制 PPSF：直接读取及修改音符音高、位置、时长、力度、歌词、音素。先要求原始文件精确二进制往返；修改时按长度差保留事件与块的尾部字节；时序修改同步已验证对应关系的编辑器副本，其余块保留。不经通用乐谱模型重建工程。
- 标准 MIDI：匹配同通道的音符起止事件，修改音高、时长、位置和力度，保留其他消息。
- CPR：借助 CubaseTools 核心做部分只读解析，保留重名轨道。结果为启发式提取，不能作为完整工程或实时状态。
- Piapro：动态发现 Windows 窗口、菜单与标准文件对话框，用原生激活通知和窗口消息导入、导出，无须前台切换或鼠标输入。WAV 为当前选中 Piapro 音轨。
- 歌手/音色：读取本机安装元数据及实际导出的原生歌手定义，直接修改 PPSF 歌手表与两份 Program Change；现场切换先快照，再导入及完整乐谱回读，失败恢复并再次回读。本机六种 Miku 音色的原生赋值与回读均已实测。
- Cubase：通过 MIDI Remote 回读宿主 UID、轨道类型、名称、乐器和选中状态，按 UID 选择音轨；支持播放、停止、录音及选中通道的音量、声像、静音、独奏。成功需要宿主反馈。
- 工程与音轨：从本机预置 Piapro 的独立 CPR 模板创建新工程；添加音频、MIDI 或首个 Piapro 乐器轨；复用、选择并打开已有 Piapro。音频轨使用原生控件消息；MIDI/Piapro 的自绘选择器需要显式启用受限的对话框相对指针消息后备。
- MIDI 路由：读取、修改 MIDI 轨的输入和输出，以及乐器轨的 MIDI 输入。官方 MIDI Remote 没有相应路由属性，使用经过布局核验的 Routing 检视器后备；Windows 本机 OCR、菜单选中项、检视器文本及目标乐器宿主反馈共同核验结果，保留恢复检查点。

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
.venv/Scripts/python.exe scripts/install_codex.py --python .venv/Scripts/python.exe --state-dir state
```

`--focus-click` 是保留的旧兼容设置。当前文件操作、添加对话框及路由使用原生激活通知，不依赖此开关或前台焦点；可省略该参数。

安装脚本先备份现有 Codex 配置，再使用 `codex mcp add` 注册 `cubase-piapro`。本机已经完成注册，当前聊天实际发现工具并执行过调用。`codex mcp get cubase-piapro --json` 可检查注册结果。新配置通常需在新会话中加载；注册成功不能代替工具握手验证。

### 实时 MIDI 控制

1. 启动 loopMIDI 并保持后台运行，创建两个独立虚拟 MIDI 端口：`CP MCP To Cubase`、`CP MCP From Cubase`。
2. 运行 `.venv/Scripts/python.exe scripts/install_midi_driver.py`。
3. 在 Cubase 的 MIDI Remote 中重新加载脚本，或正常保存后重启 Cubase。
4. 不要把控制端口加入乐器轨的 All MIDI Inputs。
5. 调用 `daw_get_status`，只有 `midi.connected` 为真才能使用反馈控制。

轨道工具另外要求 `host-cache-1` 驱动握手。新版客户端先发送独立的版本请求，旧驱动未响应时不会发送轨道查询或重置 bank。升级后使用 **MIDI Remote > Scripting Tools > Reload Scripts**；Manager 中的启用/禁用及端口连接状态不能证明脚本内容已经更新。

loopMIDI 主程序可安装到其他数据盘；迁移时保留上述端口名称并更新自启动路径，MCP 不依赖其安装目录。系统 MIDI 驱动及 Windows Installer 缓存仍由 Windows 管理。

状态中的数值采用 MIDI Remote 的归一化值，不代表音量分贝。控制对象为当前选中通道，不能用 CPR 解析出的序号代替它。

## 使用流程

### 工程与音轨

先用 `cubase_list_templates` 取得模板 ID。`cubase_create_project` 把模板 CPR 原字节复制到全新的绝对路径目录；打开工程要求当前没有其他工程，不会关闭用户现场。检查 `live_state_verified`，然后用返回的轨道 `revision` 调用 `cubase_load_piapro`。

已有工程先调用 `cubase_list_tracks`。`cubase_select_track` 使用其中的 `id` 和 `revision`；添加或重排轨道后重新读取清单。`cubase_add_track` 支持 `audio`、`midi`、`piapro`，每次只添加一轨并核对新 UID、名称及类型。MIDI/Piapro 需设置 `allow_dialog_fallback=true`；模板预加载路径可省去这些选择器操作。新增音轨保持未保存状态，返回值包含现场清单与选中 UID 的检查点。

本机已安装独立模板 `piapro-ready`。其他机器须先在 Cubase 中保存不依赖外部音视频的独立 CPR，然后注册：

```powershell
.venv/Scripts/python.exe scripts/install_project_template.py --source "E:\Audio\Piapro Template.cpr" --sha256 "实际SHA256" --id piapro-ready --description "预置一个 Piapro 乐器轨" --standalone-no-external-media
```

无外部媒体必须由操作者检查；CPR 部分解析不能自动证明这一点。模板在本机状态目录中保存，现有模板不覆盖。新工程旁的 `.mcp-project.local.json` 记录模板来源；商业插件、声库和用户工程不随代码分发。

### 乐谱数据

先调用 `inspect_project`，保留返回的 `sha256` 和音符 `id`。随后调用 `edit_notes`、`transpose_notes` 或 `set_control_curve`，建议先写入新文件。同路径写入会自动生成并校验原内容备份；源哈希不匹配时拒绝写入。

`piapro_import` 会先导出当前 PPSF 快照，再从经哈希验证的暂存副本导入。PPSF 覆盖提示仅在快照完成后自动确认；文件错误会返回错误码和快照路径。VSQX/MIDI 的导入可能追加轨道；其返回值明确标记尚未验证。继续调用 `piapro_export` 回读并核对音符和参数，必要时用原 PPSF 快照恢复。空乐曲首次导入时，可设置 `initialize_empty=true`；只有乐谱导出均禁用、无可撤消操作且宿主回传已停止时才接受。

### 歌手与 MIDI 路由

`piapro_list_singers()` 导出现场 PPSF，返回实际逐轨歌手、`revision`、安装元数据与 `captured_singers`。用其中的 `singer_id`、Piapro 内部 `track_index` 和最新 `revision` 调用 `piapro_set_singer`；成功要求目标歌手及所有其他音乐数据回读通过。它不会自动保存 Cubase 工程。`piapro_list_singers(path=...)` 读取文件并缓存真实定义；`set_ppsf_singer` 仅写文件，需要来源 `expected_sha256`，不会更新现场。其他声库先从本机实际 PPSF 捕获定义，或在 Piapro 选择一次后读取；安装记录本身不证明授权或兼容性。

路由先调用 `cubase_list_tracks`，以宿主 UID 和最新清单 `revision` 调用 `cubase_get_midi_routing(..., allow_dialog_fallback=true)`。将返回的 `host_revision`、路由 `revision` 和可选项的 `route:...` ID 传给 `cubase_set_midi_routing` 的 `expected_revision`、`expected_routing_revision`、`input_route_id`/`output_route_id`。ID 分别绑定方向与端点，不能自行拼造。乐器轨只支持 MIDI 输入，输出请求会在打开选择器前拒绝。

路由后备目前要求 Cubase 14 英文界面、展开的 Routing 检视器及足够大的窗口，使用窗口相对指针消息，不移动系统鼠标。仅提供能唯一识别并回读的当前菜单项；专用 MCP 端口及尚未核实排除桥接端口的 All MIDI Inputs 禁止新选入。失败结果含检查点和实际恢复核验状态，恢复未通过时先检查现场，勿直接重试。

备份、快照与本机安装记录位于 `CUBASE_PIAPRO_STATE_DIR`，未设置时位于本机应用数据目录中的 `CubasePiaproMCP`。备份恢复使用 `list_recovery_copies` 与 `restore_recovery_copy`。恢复当前版本前也会再次备份。

## 限制

- 不提供任意 CPR 原生写入、Piapro NT ZIP 编辑、声库逆向解析或音色模型提取。
- 原生 PPSF 插入/删除音符及批量新增控制点暂未开放；这些操作使用 VSQX。原生时序修改要求目标音符的两份时序一致，并保持在区段原有音符终点范围内；否则改用 VSQX。
- 原生歌手写入只接受精确字节往返、已知静态 Program Change 布局和本机捕获定义；两份索引不一致、共享引用或带时间的歌手自动化均拒绝。切换会重载当前 Piapro 乐曲。
- Piapro 乐谱工具要求恰好一个原生编辑器；`cubase_load_piapro` 可加载首个实例或复用已有实例。Piapro 每台电脑仅支持一个实例，包含其他工程和 DAW；同一工程的重复添加会在发送宿主命令前被拒绝。
- 实时清单覆盖前 64 个音频、乐器、采样器和 MIDI 混音通道，含隐藏通道，不含文件夹和输入/输出通道。MIDI 轨的 `plugin` 可以表示其输出所连接的乐器，不代表插件实例的所有权。
- 添加 MIDI/Piapro 的后备仅验证 Cubase 14 英文对话框及其 DPI 布局。添加流程中的输入请求仍没有独立回读，需另用路由工具验证；音频轨沿用 Cubase 默认输入/输出。新路由工具的结果来自 UI 回读，官方宿主路由接口并不存在；未验证外接硬件、多层或滚动菜单、所有语言及任意声库。
- 部分插件自绘对话框和其他语言版本尚未验证；遇到未支持对话框返回明确错误，不假称成功。
- 未保存的 Cubase 状态不会出现在 CPR 文件中。第三方插件的全部内部参数也未必能读取。
- 同时从其他聊天或手工修改同一现场会影响结果；文件哈希和 MCP 进程间锁不等于软件级事务锁。

## 验证与维护

```powershell
.venv/Scripts/python.exe -m pip install pytest
.venv/Scripts/python.exe -m pytest -q
```

测试覆盖参数与未知 XML 保留、旧哈希拒绝、备份恢复、MIDI 起止配对、控制曲线、新乐谱和真实 stdio MCP 握手。旧版 PPSF 测试通过环境变量 `CP_TEST_PPSF` 指向本机原始资料；原始资料不随项目分发。

27 个工具及示例见 [工具与架构](docs/architecture.md)。实际验证状态见 [验证记录](docs/validation.md)，查询边界修复见 [轨道查询回归](docs/crash-regression.md)，架构与决策见 [项目记忆](docs/project-memory.md)。第三方来源和许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
