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
| Cubase 现场 | MIDI Remote 官方宿主 API、loopMIDI | 当前选中通道及走带；需协议相符的驱动回传 |

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

## 写入与回读约定

音符 ID 只对某个输入哈希有效。VSQX 使用轨道、区段、音符的数组位置；PPSF 使用物理事件位置；MIDI 使用原轨道消息位置。编辑并再次检查后，使用新哈希和新 ID。

音符的 `start_tick` 相对于所属区段，MIDI 中则相对于轨道起点。`absolute_tick` 用于观察工程位置。VSQX 的预备小节和 PPSF 的乐曲时间起点不应直接混为同一原点。音量与声像为宿主归一化值，不是分贝或声道百分比，回传受 MIDI 量化影响。

典型流程：检查输入，写入副本，导入，导出回读，核对字段。`live_state_updated=false` 表示仅写入了文件；`import_requested=true` 表示对话框已提交，不等于所有音乐字段已核实。工具的 `isError`、`connected`、`verified` 和 `scope` 都需要检查。

VSQX/MIDI 导入会追加轨道。PPSF 导入替换当前乐曲；确认仅在已保存实际快照后继续。首次空乐曲可显式设置 `initialize_empty=true`，同时要求导出与撤消不可用、宿主已停止。导入失败可能清空 Piapro 当前乐曲，可用返回的快照重新导入；此时可使用经过核验的空乐曲初始化路径。

## 现场操作与焦点

默认只请求原生激活。Windows 拒绝切换前台时，返回 `FOCUS_REQUIRED`。安装时加 `--focus-click`，或设置 `CUBASE_PIAPRO_FOCUS_CLICK=1`，可启用受限后备：检查编辑器进程、无模态对话框、标题栏遮挡及窗口几何后，只点击标题栏取得焦点，并恢复原鼠标位置和置顶状态。音符、歌词、曲线和文件路径均不通过坐标操作。本机已启用此选项。

首次创建 Cubase 工程、加载插件及选择 WAV 要导出的 Piapro 音轨，仍可能使用界面。MCP 不提供任意桌面点击工具。

## 反馈与故障处理

驱动协议版本为 2。查询使用独立随机请求号，反馈仅来自宿主的 `mOnProcessValueChange`，不把控制器输入当作宿主结果；写入后需多次回传稳定。走带使用 Transport 命令，混音使用绝对跳转绑定。独奏可能影响宿主关联静音状态，返回结果包含其他字段的实际变化。

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

## 来源与部署

上游选择和许可证详见根目录第三方声明，固定提交与文件哈希见 `_vendor/UPSTREAM.json`。本项目只复用所需解析模块，未接入上游通用转换流水线。运行依赖本机 Python、已授权的 Cubase/Piapro 安装及用于现场控制的虚拟 MIDI 端口；不包含商业软件或声库的重新分发。
