# 腾讯文档：复用原流程

加载实际安装的 tencent-docs skill，动态定位 tencentdocs.py，tdoc_init 确认 READY。不写死插件缓存版本路径。其他 Agent 可以使用支持相同智能文档操作的已授权连接器，将下列参数映射为实际工具 schema；创建、插入、分页回读的顺序保持一致。没有该连接器时交付本地文件并说明在线步骤缺失，不把 WorkBuddy 插件当作所有宿主的安装前提。

固定调用顺序：

1. manage.create_file `{"title":"<视频标题>（逐字稿）","file_type":"smartcanvas"}` 创建空白文档。
2. smartcanvas.edit `{"file_id":"<id>","action":"INSERT_AFTER","id":"","content":"<MDX全文>"}` 插入正文。
3. smartcanvas.read `{"file_id":"<id>","size":20}`，持续携带 next_token 读取末页。

继续使用空白创建再插入，不调用 create_smartcanvas_by_mdx，以避开原环境遇到的模板内容。此为当前兼容路径，不推断所有版本均存在同一问题。

transcript.py mdx 输出 publish/content.mdx 和 content-index.json，普通行的加粗转 Mark，代码内星号不改，正文尖括号/花括号转义，避免当成 JSX。转换器面向标题、段落、列表、引用和代码，不承诺任意复杂 Markdown。特殊排版按实际 MDX schema 处理。

使用结构化参数传长正文，避免 shell 内拼中文、引号和反引号：

```python
import json, subprocess
from pathlib import Path
payload = {"file_id": file_id, "action": "INSERT_AFTER", "id": "",
           "content": Path(mdx_path).read_text(encoding="utf-8")}
subprocess.run([python_executable, tencentdocs_script, "tdoc_call", "tencent-docs",
                "smartcanvas.edit", json.dumps(payload, ensure_ascii=False)], check=True)
```

变量使用当次确认的值。子进程成功不代表服务端正文已完成，仍需检查返回状态和分页回读。

## 重试与长文

创建后立即将 file_id、URL、内容哈希写入任务 publish-state.json。已有记录先查原文档，不重复创建。插入超时先读，确认未插入后才补写，不能盲目重复整篇。

先按当前容量尝试全文，明确超限后按章节/完整块分批，不切断围栏或 Mark。第一批可用空 id；后续使用上批最后一个成功插入的**顶层块 ID**，从返回或 read 取得，不猜 ID，不对每批都使用空 id。逐批记录哈希、块 ID 与状态；结果不明先读后续写，避免重复和乱序。

## 回读验收

跨页核对 content-index 中的标题、章节顺序、正文开头/结尾和原稿附录首尾，确认无截断、重复或模板内容。比较时允许 read 将 Mark 序列化为 Markdown 加粗，不把格式语法差异误判成缺字；不明确时读取目标块或用文档界面核对。

保存回读响应和核对结果再给 URL。未执行在线写入或回读时，不宣称已交付。本技能不发送群消息或自动公开文档。
