# Video Transcript Doc Pro

将有权访问的网页视频课程，从真实音轨转为带时间戳的逐字稿，整理章节后写入腾讯文档的 WorkBuddy Skill。

逐字内容来自音轨 ASR，不使用 OCR。原始响应、原始转录和整理正文分别保留，方便核对。

## 能做什么

- 在导航前监听媒体请求，支持跨 iframe 播放器，不把 `blob:` 或单个分片当作完整视频。
- 通过 FFmpeg 处理 MP4 等文件和完整 HLS 点播，并执行下载、全量解码和时长校验。
- 为每课建立独立任务目录，绑定源文件和 ASR 切片，避免缓存串课。
- 导入真实 ASR 时间戳，生成 JSON、TXT、SRT，并标记需要回听的片段。
- 复用腾讯文档的“创建空白智能文档 → 插入 MDX → 分页回读”流程。

## 环境与依赖

当前主要面向 macOS 上的 WorkBuddy。需要：

- Python 3.10 或更新版本；安装脚本也可由较旧的系统 Python 启动，并寻找合适运行时。
- Node.js、Google Chrome 和 `playwright-core`。WorkBuddy 已提供的组件可直接复用；自备组件时需保证 Node 可解析 `playwright-core`。
- WorkBuddy 的 `AudioTranscribe` 工具与可用的 `tencent-docs` 技能。它们由宿主提供，不包含在本仓库中。

Python 媒体依赖通过 `requirements.txt` 安装。没有系统级 FFmpeg 时，可以使用 `imageio-ffmpeg` 随包提供的可执行文件。第三方组件按其各自许可证分发，本仓库不打包浏览器、模型、FFmpeg 二进制或 Python 虚拟环境。

本技能不会自动下载 ASR 模型，也不会自动配置收费的转录 API。其他 Agent 可以复用脚本，但需要自行适配语音识别和腾讯文档接口。当前没有 Linux 或 Windows 的完整流程验证。

## 安装

将仓库下载或克隆到本机，保证如下目录直接包含 `SKILL.md`，避免多套一层同名目录：

```text
~/.workbuddy/skills/video-transcript-doc-pro/
```

安装本地 Python 媒体依赖：

```bash
S="$HOME/.workbuddy/skills/video-transcript-doc-pro"
python3 "$S/scripts/bootstrap.py"
```

也可以用 `VIDEO_TRANSCRIPT_PYTHON` 指定 Python，用 `VIDEO_TRANSCRIPT_FFMPEG` 指定可执行的 FFmpeg。

在 WorkBuddy 中明确指定技能，例如：

```text
使用 video-transcript-doc-pro，把这个课程完整转成带时间戳的逐字稿，并写入腾讯文档：<课程链接>
```

## 浏览器登录

使用独立的浏览器配置目录，首次运行时在该专用浏览器中正常登录课程网站：

```bash
S="$HOME/.workbuddy/skills/video-transcript-doc-pro"
"$S/.venv/bin/python" "$S/scripts/browser.py" \
  --profile "$HOME/.workbuddy/video-transcript-browser"
```

脚本输出本机 CDP 地址，供后续抓流使用；不会复制日常浏览器的密码库，也不会关闭日常 Chrome。完整命令见 [执行流程](references/workflow.md)。

## 支持范围与边界

支持可下载的 HTTP(S) 媒体文件，以及带 `ENDLIST` 的完整 HLS 点播。媒体容器由标准库和工具处理，包括 TS、fMP4、字节范围和普通 AES-128 HLS。DASH、未结束直播和 DRM/SAMPLE-AES 尚未适配。

ASR 的输入格式、大小、时长及时间戳以当前工具定义为准。缺少工具或真实时间戳时会说明缺口，不伪造时间轴。切片齐全和音频解码通过，不代表识别文字已经逐字准确，仍需回听术语、数字、否定句及切片接缝。

离线回归覆盖下载失败、Cookie 范围、旧缓存、HLS、ASR 切片与 MDX 转换；原生 ASR 的准确率和腾讯文档在线交付需要在实际宿主环境中验证。

## 本地数据与隐私

默认任务目录是 `~/.workbuddy/artifacts/video-transcript-doc-pro/`。其中可能包含签名媒体 URL、Cookie、课程音频和完整转录，不应上传到公开仓库。抓流信息保存在权限为 `0600` 的文件中。

HLS 请求由临时本机转发层逐请求处理 Cookie 和同源鉴权；FFmpeg 不接收真实 Cookie 或远程签名 URL。技能不发送群消息、不自动公开腾讯文档，也不绕过登录、付费或 DRM 权限。

发布源码时排除 `.venv`、`__pycache__`、日志、凭据和任务产物；仓库已提供相应忽略规则。

## 测试

```bash
S="$HOME/.workbuddy/skills/video-transcript-doc-pro"
"$S/.venv/bin/python" "$S/scripts/selftest.py"
```

测试使用本机临时 HTTP 服务和合成媒体，不调用 ASR，不使用真实登录信息，不创建在线文档。

## 说明

- [技能入口](SKILL.md)
- [执行流程](references/workflow.md)
- [运行时与支持边界](references/runtime.md)
- [腾讯文档交付](references/tencent-docs.md)

本仓库采用 [MIT License](LICENSE)。
