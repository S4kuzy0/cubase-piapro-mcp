# Vocal to Score MCP

独立的本地音频 MCP，配合仓库中的 `skills/piapro-cover` 和稳定 Cubase/Piapro MCP。音频服务不导入 DAW 模块、不发送 MIDI、不启动宿主；重模型在各自环境的有限子进程运行。当前已验收并接入本机 Codex 的版本为 `0.1.0`。

## 接口

| 工具 | 用途 |
| --- | --- |
| `get_audio_capabilities` | 查看配置的后端、模型身份、语种和限制，不加载模型 |
| `inspect_audio` | 读取本地音频哈希、时长和声道信息 |
| `start_vocal_job` | 启动 `separate`、`transcribe`、`align` 或串联三项的 `prepare_score` |
| `get_vocal_job` | 查询阶段和终态，成功产物重新计算哈希 |
| `read_vocal_score` | 分页读取音符、浮点音高、歌词、延续和原曲偏移 |
| `cancel_vocal_job` | 请求取消；继续查询到终态，不能将请求本身当作已取消 |

所有写入使用新 E 盘作业目录，先复制且核验音频，原件只读。最多处理一分钟的明确片段；省略结束时间时，超过限制会报错，不静默截断。歌词必须对应片段，首版不自带歌词 ASR 或网易云下载器。裁剪偏移保留在 JSON，MIDI 使用片段内的相对时间。

## 输出与边界

- 分离：四个 Demucs stem，以及由非人声 stem 相加的伴奏；不能据此宣称任意录音都已去除和声或混响。
- 对齐：TIFA 原始 TextGrid、三个语义层的 JSON 及诊断数据。TIFA 音素不等于 Vocaloid 音素。
- 转谱：GAME CSV、JSON、MIDI；保留浮点音高。带歌词流程使用 TIFA 的音节边界约束 GAME，支持一字多音和休止；输出时间以语音边界为基础，辅音提前量需在 Piapro 校正，不能混称为无约束 GAME 的起音精度。
- 中文复合词可在字与音节数量一致时拆到单字；无法明确对应的文本保留原词及读音标记，交给校正，不能把整个词重复分配给每个音节。
- `requires_review=true` 表示模型推理不等于音乐质量验收。MIDI 为离散音符，连续音高、滑音、表情、颤音及 EVEC 由后续调教处理。

任务状态为 queued、running、succeeded、failed、cancelled。取消由拥有模型子进程句柄的 worker 执行；错误保留对应阶段日志。新请求遇到运行中的模型任务会以 `WORKER_BUSY` 停止，避免同时占用显存。异常终止导致遗留锁时，应先检查对应作业和真实进程，不能直接反复启动。

## 部署

全部环境、模型、缓存、下载和样本放在 E 盘。建议一个控制环境，以及互相隔离的 separation 和 score 环境。下载须各自建文件名主体目录；`scripts/fetch_asset.py` 支持发行 SHA-256 校验和安全 ZIP 解包。包管理器的缓存及临时目录也需显式指向本轮 E 盘目录。

已选上游为 audio-separator `v0.47.0`、GAME `v1.0.3`、TIFA `v1.0.0`；完整提交身份由 `configure_local.py` 记录。本机使用 Torch/torchaudio `2.7.1+cu128`。不要将 NumPy 主版本要求互相冲突的环境合并。audio-separator 的 `audioread` 直接导入依赖已在 `requirements-separation.txt` 显式补齐。

1. 创建独立 E 盘环境，按对应依赖文件与固定上游 requirements 安装。Torch/CUDA 与硬件按实际环境选择，不为此自动更新系统驱动。
2. 从原始发行页获取 GAME small、TIFA ST、Demucs htdemucs 权重和元数据；保留来源、许可和哈希。TIFA 的英文资源随模型发行包，日语另用 E 盘 `unidic-lite`，明确词典路径避免首次运行默认下载。
3. `scripts/configure_local.py --root <E盘本轮目录> --ffmpeg-dir <已有工具目录>` 记录路径、模型身份、G2P 配置及源码提交，默认 CPU。`VOCAL_TO_SCORE_CONFIG` 指向生成的本机 JSON；该文件不上传。
4. 离线验证和 E 盘 Piapro 副本验证完成后，构建 wheel 并安装到 control 环境，采用 `-I -B -X utf8 -m vocal_to_score_mcp.server` 启动已安装包。正式入口拒绝开发源码回退。
5. 使用 `scripts/install_codex.py --python <control Python> --audio-config <本机 JSON> --acceptance <真实验收 JSON> --skill-source <仓库 skills/piapro-cover>` 接入。安装器要求验收通过且配置哈希一致，保存 Codex 原配置和既有 Skill 恢复点，只新增本服务器配置并回读。

服务器不接受任意命令、自动模型下载或 DAW 动作。它与稳定 `cubase-piapro` 为两个服务；现有未发布的宿主控制功能不随这个包启用。

## 许可

本项目新代码与原仓库许可一致。audio-separator/GAME/TIFA 源码为 MIT，各自版权声明保留在原始依赖。GAME/TIFA 发行权重为 `CC BY-NC-SA 4.0`，不能将代码许可视为权重许可；维护者明确其条款不约束流经模型的数据。模型、词典和依赖分别获取，不随本项目 wheel 或 Git 分发。来源及进一步说明见 [调研提案](../docs/audio-to-score-proposal.md)。

## 验收状态（2026-10-18，北京时间）

已通过 \(24\) 项针对性测试，包含真实 stdio 工具发现、结构化响应、输入变更拒绝、词/音素分层、中文复合词、连音、休止、Unicode MIDI 回读、真实模型进程取消、已验收运行文件身份，以及配置/Skill 恢复点。

新 wheel 安装在 E 盘 control 环境，全部 \(7\) 个运行文件与已检查 wheel 字节一致，隔离入口确认从 site-packages 导入。通过该安装包的真实 MCP 连接完整执行分离、TIFA 对齐、GAME 转谱、结果读取与取消，未使用开发源码入口。

自制八音符初音片段的无约束 GAME 输出，离散音高全部与原谱一致；最大偏差约 \(4.2\,\mathrm{cent}\)，起音偏差不超过 \(0.02\,\mathrm{s}\)。这些是这个样本的测量，不能套到带词约束或其他歌曲。加入已知伴奏的另一个四音符样本，分离前后 SI-SDR 分别约 \(3.32\,\mathrm{dB}\) 和 \(33.57\,\mathrm{dB}\)，之后完整生成四个音符和对应歌词；简单合成伴奏的结果不能当作流行歌曲普遍表现。

E 盘唯一 Piapro 副本已实际导入该四音符乐谱，独立导出 VSQX 核对音高、位置、长度、歌词、音素，渲染非静音 WAV。随后导入原空白 PPSF，完整原生音乐/表情 revision 回读一致；单次原生 Quit 实际退出码为零。普通堆原生异常观察器未捕获 AV，既有崩溃报告清单无新增，原 CPR 和稳定 MIDI Remote 驱动哈希均不变。旧宿主原始崩溃的未证实部分仍保留，不能宣称厂商内部问题已修复。

本机声库登记为初音日语各音色及英语，未发现中文声库；中文歌曲的 Piapro 发音方案需要另行确定。中文、英语对齐另使用 Windows 本机自制语音样本检查，区分语音与歌声验收，不能据此承诺真实中文或英语歌声准确率。

安装器已通过上述验收及配置/运行文件身份检查，在 Codex 注册独立 `vocal-to-score`，并安装 `piapro-cover` Skill。原始配置的其他 TOML 值完全保持；按照实际保存的配置启动的新 SDK 连接确认版本 `0.1.0`、\(6\) 个工具，并实际读取音频和已验收乐谱。Skill 文件已逐字回读并通过验证器。运行中的旧连接不会因文件保存自动更换代码；当前聊天的工具列表尚未证实热刷新，需重新连接后使用新工具。
