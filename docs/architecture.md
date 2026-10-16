# 工具与架构

## 数据通路

Codex 通过本机 stdio 启动官方 Python MCP SDK 服务。文件处理不需要打开软件；现场操作通过 Piapro 原生菜单和专用 MIDI Remote 端口完成，没有网络音频服务、数据库或付费 API。

| 层 | 实现 | 边界 |
|---|---|---|
| VSQX | lxml 定点修改、保留外部节点与原有样式 | 原生 XML 文件，不包含声库音频 |
| PPSF | LibreSVIP 底层容器模型、原尺寸增量更新 | 旧版二进制格式；完整字节往返为写入门槛 |
| MIDI | mido 起止事件配对 | 标准同步 MIDI 文件；歌词及音素编辑使用 VSQX |
| CPR | CubaseTools 核心与保留重名轨道的包装 | 启发式、部分、只读；不是实时轨道标识 |
| Piapro 现场 | 动态 Win32 菜单、原生文件对话框 | 恰好打开一个编辑器；不能处理任意自绘对话框 |
| Cubase 现场 | MIDI Remote 官方宿主 API、loopMIDI | 宿主 UID、轨道选择、走带和混音；前 64 个支持通道 |
| 工程创建 | 注册的独立 CPR 原字节复制、宿主打开与回读 | 仅新目录；不修改 CPR 二进制，不隐式关闭工程 |
| 音轨添加 | MIDI Remote AddTrack 命令、原生 Edit/Button | MIDI/Piapro 自绘选择器需显式启用受限布局后备 |

PPSF 时序既存在 Events，也存在 EditorDatas。仅在目标音符的映射与原始时序一致时同步两处；其他插件及未知块保持原内容。原生时序操作不延长区段原有音符终点，扩展使用 VSQX。

## 工具目录

| 工具 | 用途 |
|---|---|
| `get_capabilities` | 当前实现、限制、状态目录及标题栏激活开关 |
| `inspect_project` | 工程摘要、声库、音符分页及 SHA-256 |
| `edit_notes` | 修改已有音符的支持字段 |
| `transpose_notes` | 全部或指定轨道移调 |
| `arrange_vsqx_notes` | VSQX 音符插入与删除 |
| `create_vsqx` | 使用本地声库表创建全新 VSQX4 |
| `read_control_curve` | VSQX 控制点分页读取 |
| `set_control_curve` | 替换或合并单条控制曲线 |
| `set_vsqx_singer` | 为区段选择已有声库表项 |
| `list_recovery_copies` | 查询文件覆盖前的恢复副本 |
| `restore_recovery_copy` | 校验当前哈希后恢复，并备份当前版本 |
| `daw_get_status` | 窗口及实际 MIDI 宿主回传 |
| `piapro_list_menus` | 动态读取菜单和启用状态 |
| `piapro_export` | PPSF、VSQX 或选中音轨 WAV 导出 |
| `piapro_import` | 先快照，再从不可变暂存副本导入 |
| `cubase_control` | 播放、停止、录音、选中通道音量、声像、静音和独奏 |
| `cubase_list_templates` | 校验并列出本机独立 CPR 模板 |
| `cubase_create_project` | 复制到新目录并按需打开，分别报告文件创建与现场回读 |
| `cubase_list_tracks` | 回读前 64 个支持通道的宿主 UID、类型、名称、乐器、选中状态和 revision |
| `cubase_select_track` | 使用当前 revision 与实际 UID 选轨，连续回传核验 |
| `cubase_add_track` | 添加单个 audio/midi/piapro 轨，保存原清单检查点并核验增量 |
| `cubase_load_piapro` | 复用、选中并打开已有 Piapro，或添加首个实例；核验原生编辑器及菜单 |

## 写入与回读约定

音符 ID 只对某个输入哈希有效。VSQX 使用轨道、区段、音符的数组位置；PPSF 使用物理事件位置；MIDI 使用原轨道消息位置。编辑并再次检查后，使用新哈希和新 ID。

音符的 `start_tick` 相对于所属区段，MIDI 中则相对于轨道起点。`absolute_tick` 用于观察工程位置。VSQX 的预备小节和 PPSF 的乐曲时间起点不应直接混为同一原点。音量与声像为宿主归一化值，不是分贝或声道百分比，回传受 MIDI 量化影响。

典型流程：检查输入，写入副本，导入，导出回读，核对字段。`live_state_updated=false` 表示仅写入了文件；`import_requested=true` 表示对话框已提交，不等于所有音乐字段已核实。工具的 `isError`、`connected`、`verified` 和 `scope` 都需要检查。

VSQX/MIDI 导入会追加轨道。PPSF 导入替换当前乐曲；确认仅在已保存实际快照后继续。首次空乐曲可显式设置 `initialize_empty=true`，同时要求导出与撤消不可用、宿主已停止。导入失败可能清空 Piapro 当前乐曲，可用返回的快照重新导入；此时可使用经过核验的空乐曲初始化路径。

## 现场操作与焦点

默认只请求原生激活。Windows 拒绝切换前台时，返回 `FOCUS_REQUIRED`。安装时加 `--focus-click`，或设置 `CUBASE_PIAPRO_FOCUS_CLICK=1`，可启用受限后备：检查编辑器进程、无模态对话框、标题栏遮挡及窗口几何后，只点击标题栏取得焦点，并恢复原鼠标位置和置顶状态。音符、歌词、曲线和文件路径均不通过坐标操作。本机已启用此选项。

模板复制及按 UID 选轨不使用鼠标。添加音频轨通过原生控件消息；添加 MIDI/Piapro 时，自绘选择器没有可用的原生列表接口，需要 `allow_dialog_fallback=true`。后备以 Name 原生 Edit 为锚，核验 Cubase 14 英文布局、窗口尺寸和 DPI 后投递对话框相对指针消息；搜索内容、轨道名和 Add Track 提交使用原生控件消息。输入路由缺少独立回读，新增轨道尚未保存。MCP 不提供任意桌面点击工具。

## 反馈与故障处理

走带/混音保留协议 2；轨道清单使用协议 3 分帧 UTF-16，另有协议 5 的 `host-cache-1` 版本握手。客户端未取得握手时不发送清单查询或重置 bank。宿主事件回调在通道已填充时缓存 UID、名称与类型；查询只序列化缓存，不在 MIDI 输入回调中读取原生通道对象。停用页面时清空缓存，重新激活更新会话标识。详见 [轨道查询回归](crash-regression.md)。

反馈仅来自宿主，不把控制器输入当作宿主结果；写入后需多次回传稳定。走带使用 Transport 命令，混音使用绝对跳转绑定。独奏可能影响宿主关联静音状态，返回结果包含其他字段的实际变化。轨道 revision 包含会话与有序 UID/名称/类型/插件，选中状态不影响它；原生 UID 尚未取得时不能以槽位或名称授权写操作。

| 错误码 | 处理 |
|---|---|
| `FILE_CHANGED` | 重新检查输入，再使用新哈希 |
| `TARGET_EXISTS` | 选择新输出，或对已检查原文件同路径写入 |
| `PPSF_ROUNDTRIP_MISMATCH` | 当前原生容器不适合定点写入，使用 VSQX |
| `PPSF_TIMING_MAP_UNSUPPORTED` | 目标音符时序存在冲突，使用 VSQX |
| `TRANSPORT_RUNNING` | 先停止 Cubase，再操作文件 |
| `NO_CUBASE_FEEDBACK` | 检查端口、重载本项目驱动；旧协议不会被当成成功 |
| `UNVERIFIED_LIVE_CHANGE` | 命令可能已执行，先读取状态，勿盲目重复切换 |
| `COMMAND_DISABLED` | 检查乐曲和所选音轨；WAV 需选中音轨 |
| `IMPORT_INCOMPLETE` | 读取具体对话框信息，保留并按需恢复返回的 PPSF 快照 |
| `TRACK_DRIVER_REQUIRED` | 打开/激活工程并重载新版驱动；无版本握手时未发送轨道查询 |
| `TRACK_LIST_CHANGED` | 重新读取清单后使用新 revision |
| `TRACK_ID_PENDING` | 等待宿主 UID 到达；不能用 CPR 索引、名称或槽位替代 |
| `PIAPRO_ALREADY_LOADED` | 调用加载工具复用已有实例，避免 E10 |
| `TRACK_OPERATION_INCOMPLETE` | 操作可能部分执行；先检查现场与返回的检查点，勿盲目重复添加 |
| `UNSUPPORTED_ADD_TRACK_LAYOUT` | 当前版本、语言或 DPI 布局未通过校验，未发送内容坐标输入 |

## 来源与部署

上游选择和许可证详见根目录第三方声明，固定提交与文件哈希见 `_vendor/UPSTREAM.json`。本项目只复用所需解析模块，未接入上游通用转换流水线。运行依赖本机 Python、已授权的 Cubase/Piapro 安装及用于现场控制的虚拟 MIDI 端口；不包含商业软件或声库的重新分发。
