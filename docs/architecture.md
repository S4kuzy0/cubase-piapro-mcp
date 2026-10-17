# 工具与架构

本页工具清单为已发布的 0.5 范围。未验收的五个宿主控制开发工具默认关闭，关闭阶段新崩溃尚未修复，见 [开发状态](host-controls-development.md)。

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
| 歌手 | 实际 PPSF SingerEntry、双 Program Change 定点写入 | 仅已捕获定义与静态布局；快照、导入、完整回读及失败恢复 |
| MIDI 路由 | Win32 窗口消息、Windows.Media.Ocr、菜单与检视器回读 | Cubase 14 英文受限布局；乐器轨只读写 MIDI 输入 |
| 音符表情/颤音 | PPSF 逻辑主音与派生事件定点修改 | 八项风格；Normal/Type1 长度、深度与速度包络 |
| Miku EVEC | 原生发音扩展/辅音重复及已编译颜色/尾音切换，显式 ID 或多个区段的音符范围 | 首次生成/停用用同一插件内临时工作轨；release-only 非平直颤音组合拒绝 |

PPSF 时序既存在 Events，也存在 EditorDatas。仅在目标音符的映射与原始时序一致时同步两处；其他插件及未知块保持原内容。原生时序操作不延长区段原有音符终点，扩展使用 VSQX。

## 工具目录

| 工具 | 用途 |
|---|---|
| `get_capabilities` | 当前版本、实现、限制、状态目录及激活策略 |
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
| `piapro_list_singers` | 有路径时读取原生 PPSF 并捕获定义；无路径时导出现场及歌手 revision |
| `set_ppsf_singer` | 按来源 SHA-256 修改文件歌手，不更新现场 |
| `piapro_set_singer` | 按 Piapro 内部轨索引、真实定义 ID 与现场 revision 切换，完整回读及失败恢复 |
| `cubase_get_midi_routing` | 按宿主 UID 与清单 revision 读取实际输入/输出、可识别选项及路由 revision |
| `cubase_set_midi_routing` | 按双 revision 与路由 ID 修改，重新打开选择器回读并保留恢复检查点 |
| `piapro_read_note_expression` | 原生 PPSF 文件或现场快照的逻辑音符、风格、颤音、EVEC 分页读取 |
| `edit_ppsf_note_expression` | 按来源 SHA-256 写入 PPSF 表情/颤音/已验证 EVEC，不更新现场 |
| `piapro_set_note_expression` | 按最新现场 revision 原生修改，导入、完整回读及失败恢复 |
| `edit_ppsf_note_expression_range` | 文件中按显式 ID 或多区段半开索引范围统一调教，来源 SHA 保护 |
| `piapro_set_note_expression_range` | 现场多范围原生调教；可选临时工作轨 EVEC 编译，合并后完整回读及失败恢复 |
| `piapro_set_part_evec` | 首次生成/停用整区段 EVEC 颜色或尾音，受限界面加原生修复与核验 |

## 写入与回读约定

音符 ID 只对某个输入哈希有效。VSQX 使用轨道、区段、音符的数组位置；旧 PPSF 通用音符工具使用物理事件位置 `e#`，表情工具使用逻辑关联 `t#:p#:n#`；MIDI 使用原轨道消息位置。编辑并再次检查后，使用新哈希和新 ID。

音符的 `start_tick` 相对于所属区段，MIDI 中则相对于轨道起点。`absolute_tick` 用于观察工程位置。VSQX 的预备小节和 PPSF 的乐曲时间起点不应直接混为同一原点。音量与声像为宿主归一化值，不是分贝或声道百分比，回传受 MIDI 量化影响。

典型流程：检查输入，写入副本，导入，导出回读，核对字段。`live_state_updated=false` 表示仅写入了文件；`import_requested=true` 表示对话框已提交，不等于所有音乐字段已核实。工具的 `isError`、`connected`、`verified` 和 `scope` 都需要检查。

VSQX/MIDI 导入会追加轨道。PPSF 导入替换当前乐曲；确认仅在已保存实际快照后继续。首次空乐曲可显式设置 `initialize_empty=true`，同时要求导出与撤消不可用、宿主已停止。导入失败可能清空 Piapro 当前乐曲，可用返回的快照重新导入；此时可使用经过核验的空乐曲初始化路径。

## 歌手与路由事务

`singer:...` 是实际原生 SingerEntry 内容的指纹，不使用注册表序号授权写入。注册表只提供安装元数据。Program Change 的 `parameters` 与 `vfxc` 两份索引须一致，相关 automation clip 不得有事件；共享引用或未知布局拒绝。现场歌手 revision 覆盖除 EditorDatas 外的原生数据块；导入回读允许歌手表重新编号，但要求所有音符、其他轨歌手、插件及项目设置一致。

歌手工具的 `track_index` 是 Piapro 内部声乐轨，Cubase 工具的 `uid:...` 是宿主通道，两者不可替换。`route:...` 按方向与端点标识；路由 revision 包含当前选择、可选择项和宿主清单 revision，不依赖 OCR 坐标或菜单顺序。乐器输出端点还绑定真实所有者 UID，MIDI 轨的插件反馈变化只允许发生在本次目标轨。

每个现场写入持有跨进程 live-daw 锁。歌手保留原生快照并在失败后重新导入、导出核验；路由写入前保存 JSON 检查点，提交后重开未过滤菜单并与检视器核对。Piapro 输出再核对宿主插件身份。失败恢复只恢复本次请求的方向，但成功声明必须同时核对两个方向，不能忽略未请求字段的意外变化。人工及其他应用不受 MCP 锁约束。

## 现场操作与激活

### 原生表情及 EVEC 事务

`expressions.py` 通过 ENOT 的三个 Clip 局部槽解析主音、颜色元音及尾音；不是全局 Events 序号。验证同音高、连续时序、总时长及唯一关联，拒绝共享或未知布局。风格参数仅改已确认字段；重音/衰减改主音，其余相关风格同步编译链。逻辑颤音起点保存在 `dur_tick_copy`，历史字段 `vibrato_start` 实际为辅音重复计数；事件 `vibrato.start_position` 实际为颤音长度。包络按每个派生事件的有效颤音区间投影，并消除边界浮点误差和宿主会丢弃的终点。原生深度/速度点使用 Int16 值及 Int32 位置，启用 EVEC 的声库名是 PascalString；这两处本地模型修正有独立往返测试和上游记录。

Native EVEC 修改保留原始音节，同步更新 CVV/VSil/CTop 与编译音素，首次发音扩展的字符串长度差同步各级容器。已有 Soft/Power 和 Short/Long 只更换经实机确认的枚举及对应音素；颜色/尾音首次生成或停用由宿主编译，不猜测派生时序。启用 EVEC 的目标禁止旧物理音符写入及不同歌手赋值，避免部分链失配。

发音扩展接受实机捕获的五个日语元音、鼻音 `N\` 及 29 子音加五元音，完整白名单由 `get_capabilities.miku_pronunciation_inventory` 返回。35 个原生样本在 Mild/Strong 与四种重复组合的 280 对实际编译记录全部吻合。未捕获或复杂自定义音素先拒绝，不能仅凭形似简单 CV 拼接 EVEC 编码。表情和颤音不要求改变原始音素。

`piapro_evec.py` 要求 Piapro 2.0.5 中文设置布局、当前已打开的钢琴卷帘、活动轨恰好一个区段。用动态菜单打开设置，以实际标题、框体、放大本机 OCR 读出的音符数及选中标记授权窗口相对消息。范围始终为整个当前区段，未请求的混合颜色/尾音拒绝统一覆盖。界面会覆盖混合表情和重塑颤音，因此先保存原数据、让宿主生成 EVEC，再原生补回原风格、颤音及未请求的发音参数；最终重新导入、导出，严格检查原生结果及所有无关音乐、自动化和编辑元数据。错误路径取消已确认浮层或导入快照，恢复后必须再次读取核验。

`note_selection.py` 解析显式逻辑 ID 或多个区段的零基半开音符索引范围，按原乐谱顺序去重，拒绝不存在、空或歧义目标；省略范围终点表示该区段末尾。读取可筛选，但 revision 始终覆盖整个原生乐曲。`expression_ranges.py` 把统一设置展开成原生写入，已经编译的颜色/尾音不触碰界面。需要首次生成/停用时，先验证引用布局、音素、歌手和 profile，再要求显式后备及未锁屏的 Windows Default 桌面。

`evec_work.py` 按歌手、目标颜色/尾音、原发音与重复分组，用实际导出的 VSQX 声库表创建单区段临时内部工作轨。核验新轨身份、歌手、活动状态、所有音符和原始音素，原生菜单打开钢琴卷帘；`evec_focus.py` 在重复琴键列右侧检测临时音符正文，只给已核验工作轨发送受限焦点消息，再要求设置标题实际数量、两列标签及选中标记。本机中文 OCR 曾漏读数量中的零，增加仅限实际默认字体的独立数字轮廓解析；不把预期数量作为识别输入，未知轮廓继续依靠严格 OCR 或拒绝，不能补猜数字。锁屏时自绘截图可能停在旧画面，因此导入工作轨前及每组 UI 前均检查 Default 桌面。

`evec_patch.py` 从原快照开始合并宿主实际生成的目标编译链；保留原主音数据、原 ENOT 及风格/颤音，修复所有 Vocaloid3 Clip 全局事件引用、ENOT 三槽局部引用和 AutomationClip 引用。未知音频/MIDI/VSQ2 引用布局或目标音符共享自动化引用拒绝合并。每层容器按正文长度差增减 size，保留原生尾字节；仅本地字节往返无法证明宿主接受。正式两轨四区段、九音符、混合发音与非平直颤音的首次生成/同时停用，以及平直颤音下的单独停用均已实际完整回读。最终原数据不含工作轨；失败只取消已核验面板，或关闭唯一且明确点名本次阶段文件的已知文件错误，再导入原快照、再次导出严格核验。

已确认宿主的 release-only 链会丢弃尾音中额外的非平直颤音点，移离边界也无效。工作轨新建该链且逻辑包络变化时，plan 在任何现场导入前拒绝；直接颤音写入若投影到 release-only 尾段产生变化也拒绝，只有重复恒定点可规范成一个。保留颜色、同时停用两项或显式恒定包络均可，禁止静默改曲线或放宽回读。

现场 expression revision 覆盖所有非编辑块和逻辑 ENOT 元数据，忽略选择标记；与歌手 revision 不互换。文件写入仍要求完整字节往返、来源 SHA-256 和覆盖备份，现场写入要求停止走带及跨进程锁。生成 EVEC 会重排物理事件，比较时需解析索引，但不能忽略未请求的音符、全局块、剪辑或自动化变化。

当前操作向已核验的 Cubase/Piapro 窗口发送有超时的 WM_ACTIVATE 通知，无须切换前台或物理鼠标。文件导入、导出与歌手切换已在其他应用处于前台时实测。`activate_window` 与 `--focus-click` 仅保留旧兼容用途，当前正式路径不调用标题栏点击。

模板复制及按 UID 选轨不使用鼠标。添加音频轨通过原生控件消息；添加 MIDI/Piapro 时，自绘选择器没有可用的原生列表接口，需要 `allow_dialog_fallback=true`。后备以 Name 原生 Edit 为锚，核验 Cubase 14 英文布局、窗口尺寸和 DPI 后投递对话框相对指针消息；搜索内容、轨道名和 Add Track 提交使用原生控件消息。输入路由缺少独立回读，新增轨道尚未保存。MCP 不提供任意桌面点击工具。

路由选择器无 HMENU，系统 UIA 只暴露搜索 Edit；WM_SETTEXT 不可靠触发内部过滤模型，因此只选择未过滤菜单中的唯一识别项。`native_visual.py` 用 PrintWindow 捕获已核验窗口，调用随包的原始 PowerShell 辅助脚本使用 Windows 内置 OCR，无下载模型或网络识别。检视器需通过标题、行位置及实际下拉三角形定位；取消向弹窗本身投递 Escape。未知、多义、被截断或滚动菜单不推测选择。本机英语 OCR 不可用时使用用户语言识别器，仅对已知静态标签做受限归一化。

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
| `SINGER_LAYOUT_UNSUPPORTED` | 未知或带时间自动化的歌手布局；没有原生写入 |
| `SINGER_NOT_CAPTURED` | 从本机实际 PPSF 捕获定义，再使用返回 ID |
| `PIAPRO_STATE_CHANGED` | 重新读取现场歌手 revision |
| `SINGER_CHANGE_INCOMPLETE` | 查看原生快照及实际恢复核验状态，先检查现场 |
| `ROUTING_CHANGED` | 重新读取路由，使用新的双 revision |
| `MIDI_OUTPUT_NOT_APPLICABLE` | 乐器槽不能当作 MIDI 输出；只为乐器轨设置 MIDI 输入 |
| `ROUTING_OPTION_UNAVAILABLE` | 请求项不可识别、歧义、保留控制端口或未验证的 All MIDI Inputs |
| `ROUTING_CHANGE_INCOMPLETE` | 查看检查点及双向恢复回读，未通过时不得盲目重试 |
| `UNSUPPORTED_ROUTING_LAYOUT` | 展开 Routing 并使用已验证的英文 Cubase 14 布局；其他布局拒绝 |
| `EVEC_UI_REQUIRED` | 首次颜色/尾音生成或停用使用受限后备；已编译切换可原生修改 |
| `INVALID_NOTE_SELECTION` / `INVALID_NOTE_RANGE` | 从最新逻辑音符清单选择显式 ID 或正确的半开区段范围 |
| `INTERACTIVE_DESKTOP_REQUIRED` | 退出屏保、解锁 Windows 后重试界面后备；原生文件/范围功能不受影响 |
| `EVEC_REMAP_LAYOUT_UNSUPPORTED` | 存在未验证引用布局，拒绝首次 EVEC 合并；可继续独立原生调教 |
| `EXPRESSION_RANGE_INCOMPLETE` | 根据快照及实际恢复状态检查现场，未核验时不继续修改 |
| `RELEASE_ONLY_VIBRATO_UNSUPPORTED` | 保留颜色、同时停用颜色与尾音，或由调用者明确选择恒定颤音；避免宿主丢弃尾音曲线 |
| `PIAPRO_PART_VIEW_REQUIRED` | 打开所请求单区段活动轨的钢琴卷帘，不能使用概览或猜测坐标 |
| `EXPRESSION_LAYOUT_UNSUPPORTED` / `VIBRATO_PROFILE_UNSUPPORTED` | 未知关联或颤音预设，停止原生写入 |
| `EXPRESSION_CHANGE_INCOMPLETE` / `EVEC_CHANGE_INCOMPLETE` | 查看快照及真实恢复状态；未核验恢复时先检查现场 |
| `EVEC_NOTE_REQUIRES_EXPRESSION_TOOL` | 避免修改部分派生链，改用逻辑表情工具 |
| `EVEC_SINGER_CHANGE_UNSUPPORTED` | 先停用目标轨 EVEC，再进行不同歌手赋值 |

## 来源与部署

上游选择和许可证详见根目录第三方声明，固定提交与文件哈希见 `_vendor/UPSTREAM.json`。本项目只复用所需解析模块，未接入上游通用转换流水线。运行依赖本机 Python、已授权的 Cubase/Piapro 安装及用于现场控制的虚拟 MIDI 端口；不包含商业软件或声库的重新分发。
