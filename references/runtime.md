# 运行时与支持边界

## 依赖与网络

Linux 优先使用发行版 FFmpeg（Debian/Ubuntu 可 sudo apt install ffmpeg）。imageio 当前的 Linux x86_64 7.0.2 静态构建在原生 CI 的 MPEG-TS 回归中崩溃，自动发现时排除该构建；没有替代程序就停止并给出安装提示，不让崩溃结果进入后续 ASR。Windows/macOS 可使用已通过回归的随包 FFmpeg。

bootstrap.py 可由 Python 3.7+ 运行，会动态寻找 Python >=3.12（锁定的 PyAV 19 要求），优先用已有 uv，否则用 venv/pip，在本技能 .venv 安装 requirements.txt。Windows 使用 Scripts/python.exe，macOS/Linux 使用 bin/python；以后续输出为准。没有系统 ffmpeg 命令也能使用 imageio-ffmpeg 自带的可执行文件，前提是 -version 检查通过。可以用 VIDEO_TRANSCRIPT_PYTHON、VIDEO_TRANSCRIPT_FFMPEG 指定运行时，不根据架构名推断“不可用”。

采集需要 Node >=20 和 Playwright。bootstrap 优先复用可解析的 playwright-core/playwright，否则通过 npm ci 安装锁定的本地 playwright-core；WorkBuddy 内置模块只是可选回退。VIDEO_TRANSCRIPT_NODE、VIDEO_TRANSCRIPT_PLAYWRIGHT 可显式指定；仅处理已有媒体可 bootstrap --media-only。只有本机 CDP 自动添加 NO_PROXY。Python 默认遵循系统代理，FFmpeg 同步代理配置；确认 CDN 需要直连时才添加 extract --direct，浏览器也只有明确需要时添加 browser.py --direct。不默认关闭沙箱或全局覆盖代理。

ASR 由宿主或用户现有配置提供，例如 WorkBuddy 的 AudioTranscribe。用 transcript.py import-asr 导入统一的 segments/start/end/text 格式即可，不需要改媒体管线。脚本不安装 ASR 模型、不自动访问按分钟收费 API。读取当前工具 schema，不假定格式上限或时间戳单位。

## 浏览器

优先连接已授权的专用 CDP。browser.py 自动寻找 Windows/macOS/Linux 上的 Chrome、Edge、Chromium，可用 --chrome 或 VIDEO_TRANSCRIPT_BROWSER 指定实际可执行文件。新实例使用独立 user-data-dir、动态端口和正常沙箱。默认使用 ~/.video-transcript-doc-pro/browser 并首次正常登录；已有其他专用配置目录时可以 --profile 显式复用。脚本不复制日常浏览器的 Default、密码库或 Cookie 数据库。Linux 无桌面环境可用 --headless；交互登录需要有界面环境或已有专用会话。

专用目录的 DevToolsActivePort 可连接时直接复用。目录被占用而无法连接时停止，不用 pkill 或删除目录。保留专用浏览器便于复用登录；仅在明确需要时关闭本任务专用实例，不关闭日常浏览器。启动失败检查专用目录的 transcript-browser.log。

## 媒体

实现支持 HTTP(S) 文件及带 ENDLIST 的完整 HLS 点播。HLS 主/子清单、独立音频、TS、fMP4、字节范围和普通 AES-128 交给标准工具处理。DASH、未结束直播和 DRM/SAMPLE-AES 尚未适配，遇到时停止说明，不承诺所有网站通用。

Cookie 只采集候选媒体 URL 对应条目，下载按域、路径、secure、有效期匹配，分区 Cookie 不展平。HLS 通过任务内的本机临时转发层拉取已登记的清单、分片和密钥；CookieJar 逐请求匹配，Authorization 只发向原始同源。FFmpeg 只访问临时的本机不透明 URL，不接收 Cookie 头或远程签名地址。该处理避免依赖 FFmpeg 7.1 不完整的 Secure/host-only Cookie 实现。转发层绑定 127.0.0.1，处理完成即关闭，不提供任意 URL 代理入口。

默认单文件下载上限 4GiB，可调整 --max-bytes。缓存绑定精确媒体 URL 哈希和文件 SHA-256。临时文件不作为完成结果。输出全量解码；与源时长偏差超过 max(1秒, 0.1%) 则失败。特殊时间线先解释并修复处理，不删除校验绕过。

## 状态与数据

数据目录默认为 ~/.video-transcript-doc-pro/，可通过 VIDEO_TRANSCRIPT_DATA_DIR 修改；任务在其 tasks/ 子目录。每次 init 新建随机后缀目录；续跑显式传原 task，旧 WorkBuddy 任务无需迁移也可继续。写操作用 .lock；断电留下孤立锁时先检查其中 PID 不再运行，才清理。

capture.json 可能包含签名 URL 和 Cookie，不粘贴到对话或文档。POSIX 文件设置 0600；Windows 的 chmod 不能代替账户 ACL，应将数据保存在当前用户的私有目录。原始 ASR 和课程全文保存在任务目录。重抓、重抽或重切片后不能混用旧 ASR。运行产物不放 scripts/references。
