# Video Transcript Doc Pro

[![Cross-platform regression](https://github.com/shenshuiqu-x/video-transcript-doc-pro/actions/workflows/test.yml/badge.svg)](https://github.com/shenshuiqu-x/video-transcript-doc-pro/actions/workflows/test.yml)

把有权访问的网页视频课程，从真实音轨转成带时间戳的逐字稿，生成本地文件，并按需整理章节、写入腾讯文档。

采用 [Agent Skills](https://agentskills.io/specification) 的 `SKILL.md` 目录结构，不绑定某个 Agent。WorkBuddy 是最初使用的宿主；其他能读取技能、运行本地命令的 Agent 也可安装。逐字内容来自语音识别（ASR），不使用 OCR；原始响应、原始转录和整理正文分别保留。

## 安装：把链接发给 Agent

推荐直接发送以下内容：

```text
请安装这个 Skill：https://github.com/shenshuiqu-x/video-transcript-doc-pro

先阅读仓库 README.md 和 SKILL.md，按你当前宿主的技能安装规范，
将整个技能目录安装到可发现的位置，确保 SKILL.md 直接位于技能根目录。
运行 scripts/bootstrap.py 安装依赖，并使用它输出的 Python 路径运行
scripts/pipeline.py doctor --require-browser。
检查你可用的语音识别工具和腾讯文档连接器，报告可以完成的交付范围。
```

需要 Agent 具备联网下载、写入技能目录和执行命令的能力；纯聊天窗口无法直接安装。本仓库不另设统一安装路径，各宿主按自己的规范发现技能；安装后如未出现，按宿主要求重新加载技能或重启会话。完整转录仍需可用的 ASR，在线文档仍需登录和连接器，安装源码不会自动取得这些权限。

## 支持哪些 Agent 和系统

| 部分 | 需要的能力 | 迁移方式 |
| --- | --- | --- |
| 技能指令 | 能加载 `SKILL.md`，或读取本目录并遵循指令 | 使用宿主的技能目录，无需修改工作流正文 |
| 抓流与抽音 | 本地命令、Node.js、可调试浏览器、Python | 共用本仓库脚本，不依赖 WorkBuddy 私有接口 |
| 逐字转录 | 能读取本地音频、返回真实时间戳的 ASR | 将响应转为统一 JSON 后导入；WorkBuddy 的 `AudioTranscribe` 是一种选择 |
| 腾讯文档 | 已授权且支持智能文档创建、插入、读取的连接器 | 保留“空白创建 → 插入 MDX → 分页回读”流程 |
| 本地交付 | 不需要腾讯文档连接器 | 输出 JSON、TXT、SRT 和整理 Markdown；缺少 ASR 时先交付已校验音频 |

代码适配 **Windows、macOS、Linux**，自动发现 Chrome、Edge 或 Chromium，处理不同系统的虚拟环境路径和浏览器进程启动方式。Windows 默认的 Edge 也可以使用，不必额外安装 Chrome。Linux 无桌面环境可使用 `browser.py --headless`；首次需要交互登录的网站，应先在有界面的专用浏览器中完成登录。

已在 macOS 本机验证媒体处理及真实浏览器抓流。仓库 CI 在三个系统原生环境中运行相同的媒体回归和浏览器测试，最新结果见上方徽章。CI 使用合成媒体，不证明每个课程网站、每种 ASR 或每个宿主的在线文档交付都经过验证。

## 环境与手动安装

- Python **3.12 或更新版本**（锁定依赖 `av==19.0.2` 的要求）。引导脚本本身可由 Python 3.7+ 启动，寻找已安装的新运行时。
- Node.js **20 或更新版本**，推荐仍受维护的 LTS；需要安装依赖时还需 npm。
- Chrome、Edge 或 Chromium，浏览器正常安装即可，不随本仓库打包。
- Linux 需安装系统 FFmpeg，例如 Debian/Ubuntu 使用 `sudo apt install ffmpeg`；CI 使用发行版 FFmpeg。Windows/macOS 无系统 FFmpeg 时可使用随 Python 依赖提供的构建。当前 imageio 的 Linux x86_64 7.0.2 静态构建在 MPEG-TS 回归中崩溃，脚本会排除它并给出安装提示。
- 完整转录需要宿主现有或用户已配置的 ASR；技能不自动下载模型或开通收费 API。

下载或克隆仓库到宿主的技能目录，避免多套一层同名目录。下面命令从**技能根目录**运行，与安装路径无关。

macOS / Linux：

```bash
python3 scripts/bootstrap.py
.venv/bin/python scripts/pipeline.py doctor --require-browser
.venv/bin/python scripts/browser.py
```

Windows PowerShell：

```powershell
py -3 scripts/bootstrap.py
.\.venv\Scripts\python.exe scripts\pipeline.py doctor --require-browser
.\.venv\Scripts\python.exe scripts\browser.py
```

Windows 未安装 `py` 启动器时，使用可用的 `python` 命令。bootstrap 输出实际使用的 Python 路径，Agent 应保存该值执行后续命令。

bootstrap 创建技能自己的 `.venv`，安装 Python 媒体依赖；能复用现有 Playwright 时直接复用，否则使用锁定的 `package-lock.json` 运行 `npm ci` 安装到本地 `node_modules`。不下载另一份浏览器。只处理已有媒体时可加 `--media-only`，跳过 Node/Playwright 安装。第三方组件按各自许可证分发。

可用环境变量覆盖自动发现：`VIDEO_TRANSCRIPT_PYTHON`、`VIDEO_TRANSCRIPT_NODE`、`VIDEO_TRANSCRIPT_BROWSER`、`VIDEO_TRANSCRIPT_FFMPEG`、`VIDEO_TRANSCRIPT_PLAYWRIGHT`（模块名或绝对路径）。`VIDEO_TRANSCRIPT_DATA_DIR` 可更改数据目录。

## 使用

安装后向 Agent 发送：

```text
使用 video-transcript-doc-pro，把这个课程完整转成带时间戳的逐字稿，
保留原稿并整理章节，写入腾讯文档：<课程链接>
```

也可以只要求本地逐字稿或只验证抽音。首次在技能启动的专用浏览器中正常登录课程网站；脚本输出本机 CDP 地址，供后续抓流使用。具体命令及 PowerShell 示例见 [执行流程](references/workflow.md)。

## 能力与边界

- 导航前监听媒体请求，支持跨 iframe 播放器，不把 `blob:` 或单个分片当作完整视频。
- 使用标准 FFmpeg 处理 HTTP(S) 文件和带 `ENDLIST` 的完整 HLS 点播，支持 TS、fMP4、字节范围及普通 AES-128 HLS。DASH、未结束直播和 DRM/SAMPLE-AES 尚未适配。
- 每课独立目录，绑定源文件和 ASR 切片，执行下载、全量解码和时长检查，防止缓存串课、缺片和部分下载被当成完成。
- ASR 的格式、大小、时长及时间戳以实际工具定义为准。没有真实时间戳不能均分全文伪造时间轴；结构检查通过也不保证逐字准确，术语、数字、否定句和切片接缝仍需回听。
- 腾讯文档适配器由宿主提供，不包含在仓库里；缺少连接器时保留本地产物并报告缺口，不宣称已经在线交付。

## 本地数据与隐私

默认数据目录为 `~/.video-transcript-doc-pro/`（Windows 对应用户主目录下的同名文件夹），其中 `browser/` 保存专用登录会话，`tasks/` 保存课程任务。以前的任务目录仍可通过 `--task` 继续使用，已有专用浏览器目录可通过 `--profile` 复用。

这些目录可能包含签名 URL、Cookie、音频和完整转录，不应提交到公开仓库。POSIX 系统设置私有目录/文件权限；Windows 使用所在目录的账户 ACL，`chmod(0600)` 不等同于 Windows ACL 隔离，请将数据留在自己的用户目录。

HLS 请求由本机临时转发层逐请求处理 Cookie 和同源鉴权；FFmpeg 不接收真实 Cookie 或远程签名 URL。技能不复制日常浏览器密码库、不关闭日常浏览器、不发送群消息、不自动公开腾讯文档，也不绕过登录、付费或 DRM。

## 测试与文档

从技能根目录使用 bootstrap 输出的 Python 路径运行 `scripts/selftest.py`。设置 `VIDEO_TRANSCRIPT_BROWSER_TEST=1` 可增加真实浏览器测试，测试会自行关闭它创建的专用测试实例。默认测试不启动浏览器。测试使用本机 HTTP 服务与合成媒体，不调用 ASR、不使用真实登录信息、不创建在线文档。

- [技能入口](SKILL.md)
- [执行流程与 ASR 统一接口](references/workflow.md)
- [运行时与支持边界](references/runtime.md)
- [腾讯文档交付](references/tencent-docs.md)

本仓库采用 [MIT License](LICENSE)。
