---
name: video-transcript-doc-pro
description: 优化版网页视频课逐字稿与腾讯文档工作流。用于用户提供视频课程链接，要求从真实音轨转录、保留时间戳、整理章节并写入腾讯文档；使用导航前抓流、独立任务目录、标准媒体处理和完整性检查。用户指定 Pro 或优化版时使用本技能。支持普通 MP4 与完整 HLS 点播，不用于截图/OCR、绕过付费权限或 DRM。
---

# 视频逐字稿 Pro

交付可回溯到真实音轨的逐字稿，按用户需要附章节整理。腾讯文档沿用原技能路径：创建空白智能文档 → 插入 MDX → 分页回读。

## 工作原则

- 逐字内容来自音轨 ASR，不用 OCR、弹幕或播放器文字替代。页面标题和课程元数据可以读取；未说出的课件内容不补写为讲师原话。
- 原始响应、原始逐字稿与章节整理稿分别保存。原稿不删除重复、不改口误；整理稿明确说明删改范围。保留否定词、数字、条件、风险描述，不把改写句放进引号冒充原话。
- 先确认能力：AudioTranscribe 的格式、大小、时长和返回字段以当前工具 schema 为准，不沿用未经核验的“1GB/5小时”。不可用时说明缺口，已完成音频保留，不默认换成未经验证的小模型。
- 每课一个任务目录，输出由 manifest.json 关联。脚本非零退出就停止该阶段；空文件、部分下载、未完成切片不能作为全课交付。
- 只使用已授权播放的会话。复用专用已登录浏览器，不复制密码库或全部浏览记录，不关闭日常 Chrome。登录失效需要正常登录，不能伪造可播放状态。

## 执行入口

命令见 [references/workflow.md](references/workflow.md)，运行时与支持边界见 [references/runtime.md](references/runtime.md)。首次先预检，后续使用本技能目录内 .venv/bin/python，不误用系统旧 Python。

1. **准备与建任务**：执行 scripts/bootstrap.py 安装/检查私有媒体依赖，然后 pipeline.py init --url <课程URL>，保存返回的 task。已有任务续跑时复用该路径。
2. **抓取媒体**：连接可用的本机 CDP。没有连接时用 browser.py --profile <专用目录> 启动，建议目录为 ~/.workbuddy/video-transcript-browser，并在其中正常登录课程网站。pipeline.py capture 先监听、再导航、再跨 frame 触发播放。多个候选核对后选择，不能取第一个分片、blob 地址或凭 URL 中的 eof 判断完整性。
3. **抽取并校验**：执行 pipeline.py extract。兼容音轨通过标准 FFmpeg 无损复制；其他编码和 HLS 转为无损 FLAC。下载、源绑定、全量解码和时长检查全部成功，manifest 才记录 audio.validated=true。
4. **完整 ASR**：读取当前 AudioTranscribe schema。支持完整音频且未超限时传完整文件；确需切片时用 pipeline.py chunk，处理 chunks.json 的每一片。保留完整工具响应，将时间戳转成秒后用 transcript.py import-asr 导入，再 assemble 生成原始 JSON/TXT/SRT 和检查报告。不能以抽样代替全课。
5. **校订与整理**：阅读 transcript.qa.json，回听片头、中段、片尾、切片接缝及疑似漏词、重复、数字/术语错误。结构通过不等于逐字准确。无法确认的字词标注“听辨不清”，不靠常识补齐。章节时间来自原始时间戳，章节数随内容。默认一个文档包含说明与章节脉络、保守整理正文、带时间戳原始转录附录；只要原稿时省略整理，不因长文静默删除附录。
6. **写腾讯文档**：执行 transcript.py mdx 转换，再严格复用 [references/tencent-docs.md](references/tencent-docs.md)：加载目标环境 tencent-docs，tdoc_init，空白创建、插入、分页回读。记录 file_id 和插入状态，结果不明先读取，不盲目重建/重插。

## 交付判断

给腾讯文档 URL，说明处理了完整课程还是指定范围、是否有无法确认的词句，并简述文字来自真实音轨。原稿保存在任务目录。文档必须回读核对标题、章节、正文末尾和原稿附录；若未写入或未核验，明确报告完成到哪一步。

只要求验证抓流/抽音/ASR 时执行相应范围，不自动创建文档或发送群消息。
