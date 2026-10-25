# 执行命令

S 是技能目录，P 是其私有 Python，T 是 init 返回的实际 task。跨 shell 工具调用要重新赋值，不假定变量持久存在。

## 准备

```bash
S="$HOME/.workbuddy/skills/video-transcript-doc-pro"
python3 "$S/scripts/bootstrap.py"
P="$S/.venv/bin/python"
"$P" "$S/scripts/pipeline.py" init --url '<课程URL>'
```

保存返回路径并设置 T。已有专用 CDP 时跳过启动，否则：

```bash
"$P" "$S/scripts/browser.py" --profile "$HOME/.workbuddy/video-transcript-browser"
```

首次在该专用浏览器中正常登录。已有其他专用配置目录时可显式传入；端口以输出为准。

```bash
"$P" "$S/scripts/pipeline.py" capture --task "$T" --cdp 'http://127.0.0.1:<实际端口>'
"$P" "$S/scripts/pipeline.py" extract --task "$T"
```

多个候选时查看私有 capture.json，核对广告、多语言、多课视频后用 --media-index 指定。不要输出凭据。确认需要直连时才加 extract --direct。audio.file 是实际文件名，audio.decoded_duration 是解码时长；AAC/ALAC 文件通常生成 M4A，其他编码及 HLS 生成 FLAC。401/403、无音轨、坏帧或时长不符就停止，刷新真实授权，不手写假 capture。

## ASR

现场读取 AudioTranscribe schema，满足限制时直接传完整音频。工具仅接受 WAV、或文件需要切小时：

```bash
"$P" "$S/scripts/pipeline.py" chunk --task "$T" --seconds 900 --overlap 2
```

900秒只是示例，按实际限制选择。16kHz 单声道 PCM16 WAV 约每分钟 1.92MB，预留余量。每片偏移在 chunks.json，所有编号都要处理。

保存工具完整原始响应，另写标准化 JSON：

```json
{"segments":[{"start":0.2,"end":3.8,"text":"工具实际返回的原话","speaker":"仅实际返回时保留"}]}
```

start/end 是片内秒数，脚本加回 offset，不要重复加。毫秒转换依据 schema，不猜单位。没有真实时间戳不能把全文平均分配成伪时间戳；先解决接口缺口，或明确交付无时间戳稿。

```bash
"$P" "$S/scripts/transcript.py" import-asr --task "$T" \
  --input "$T/asr-normalized-0000.json" --raw "$T/asr-response-0000.json" \
  --provider 'AudioTranscribe:<实际可见版本或未提供>' --part 0
"$P" "$S/scripts/transcript.py" assemble --task "$T"
```

切片逐片导入再 assemble。静音片可导入空 segments，但保留响应并回听确认。缺片、绑定不符、越界均失败。覆盖已有不同识别记录需 --replace。重叠区按句段中点归属切片，分句不同仍可能有边界重复/漏词，须回听接缝；原始响应不做破坏性去重。

## 正文与发布

读取 transcript.raw.json/TXT/SRT 与 transcript.qa.json。检查只证明片段与时间结构，不能证明识别准确。回听长空白、重复、低置信度和片头、中段、片尾、接缝，核对术语、数字和否定句。校订另存 corrected，不覆盖 raw。

写 transcript.md，按用户范围包含课程信息、转录说明、章节脉络、正文，默认附原始转录。画面未口述的对象标注“此处讲师指向画面，音轨未说明具体对象”，不猜图形或名称。不强制章节数、不默认增加金句或泛化免责声明。

```bash
"$P" "$S/scripts/transcript.py" mdx --input "$T/transcript.md" --outdir "$T/publish"
```

继续 [tencent-docs.md](tencent-docs.md) 的原创建/插入/回读流程。
