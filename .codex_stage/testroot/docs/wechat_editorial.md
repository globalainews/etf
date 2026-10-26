# Global简读公众号文章系统

这条链路与视频合成分开运行，使用同一份经过 CP1 v2 校验的 `candidates/YYYYMMDD.json`。只从具有正文摘要、关键事实、来源 URL 和发布时间的 Deep 候选选主线；Flash 标题不扩写。默认每天独立选择一条，视频头条不决定公众号选题。

## 首篇样稿与日常运行

项目运行环境使用 `F:/anaconda3/python.exe`（包含 Pillow 和 Playwright 所需依赖）。

```powershell
F:/anaconda3/python.exe -m wechat_editorial.cli sample --date 20261014
F:/anaconda3/python.exe -m wechat_editorial.cli run --date YYYYMMDD --wait-seconds 10800
```

也可以双击项目根目录的 `run_wechat_article.bat`。它默认使用当天北京时间并等待候选文件；指定主题时传入 Deep 候选 ID：

```powershell
run_wechat_article.bat 20261014 --topic-id 4
```

已有候选文件时，也可通过视频总控只运行 S4；选择 S4 会自动启用公众号文章链路：

```powershell
run_pipeline.bat 20261014 --stage 4
run_pipeline.bat 20261014 --stage 4 --wechat-topic-id 4
```

样稿固定选用 2026-10-14 的 Visa 候选。Visa 投资者公告和新华社转述的央行决定已在 2026-10-14 核对；候选中的路透原文无法直接打开，因此样稿只引用前两者的事实。若需重写已送入草稿箱的文章，应使用 `--version 2`，不能覆盖原版本。

日常命令等待候选文件就绪，重新调用既有候选校验器，然后以低温度模型根据事实卡撰稿。候选新闻阶段不调用 Gemini，也不输出 Gemini 补充字段。只有当编辑指定的 Deep 新闻没有显式正文（`article_body`、`body`、`content` 或 `_article_evidence_text`）时，公众号流程才使用已登录的 Gemini 普通新会话检索同一事件；会话保留在最近对话中，有正文时不会调用。为控制复杂度和噪声，该流程使用 Gemini Auto，不启用 Deep Research，并限制为最多 3 条事实、2 至 3 个直接报道链接。

Gemini 回答只作为检索线索：程序只接受页面引用区真实出现的链接，重新通过 Trafilatura/Hermes 抓取网页，要求摘录能在正文中归一化精确匹配，再由低温度 Qwen 判断事实是否得到直接支持。数字、引语、因果及重大影响结论还需要两个不同注册域名。任何一步失败都会阻断文章，不能退回模型记忆或标题扩写。通过验证的事实会进入事实卡，并在公众号正文中单独生成“新闻内容摘要”；摘要也必须绑定已验证事实 ID。正文不足时会提示编辑，不会凑字数。模型生成的内容必须逐段绑定事实 ID；数字、美元单位、来源、绝对因果及移动端横向溢出是程序阻断项。语义、引语范围、因果判断、解释是否过度仍须人工终审。

Windows 计划任务可在每日北京时间 06:15 调用 `powershell -File D:/antigravity/scripts/run_wechat_daily.ps1`。脚本最多等待候选文件三小时，随后以退出码和 `blocked.json` 报告失败；编辑应在每天送审前查看 `qa_report.json` 和手机预览。计划任务需在实际机器上由运营者配置，以便使用该机器的环境变量和模型服务。

`--topic-id` 和 `--wechat-topic-id` 使用从 1 开始的 Deep 新闻 ID，对应候选文件中 `deep_candidates` 的显示顺序；内部从 0 开始的候选 ID 只保留在事实卡中用于溯源。每期产物在 `output/wechat/<date>/<deep_news_id>/v<version>/`：

| 文件 | 用途 |
| --- | --- |
| `article.json` | 结构化文章与版本、内容哈希 |
| `fact_ledger.json` | 每项事实的候选 ID、原文 URL、发布时间及证据摘录 |
| `article.html` | 公众号排版成品，纯内联样式 |
| `cover.jpg`、`mobile_preview.png` | 自制文字封面及 390px 手机端预览 |
| `qa_report.json`、`manifest.json` | 阻断结果、运行与草稿状态 |
| `manual_import.md` | 账号接口不可用时的后台手工导入说明 |
| `video_comments.md` | 置顶、补充、互动三条视频号评论，供人工依次复制 |
| `video_comments.json` | 评论区素材、事实绑定、发布顺序与人工审核标记 |
| `gemini_research.json` | 仅无正文时生成；保存原始回答、页面引用、抓取与逐条接受/拒绝原因 |

公众号正文先展示绑定事实卡的“新闻内容摘要”，并在资料来源前展示“看懂这件事”，依次说明已确认事实、关键边界和后续观察。视频号置顶评论由程序添加真实来源名称与公众号文章标题；评论素材不包含外链、二维码或投资判断，也不会自动发送。

生成失败时，`output/wechat/<date>/blocked.json` 保留阻断原因。视频流水线默认跳过旧 S4，由独立日常任务负责文章；如需在视频命令中显式触发，可加 `--with-wechat`，文章失败会记录状态而不阻断视频。旧 `wechat-article.html` 生成器只供历史产物回看，不再是新文章来源。

## 草稿箱接入

在公众号后台确认开发接口、调用 IP 与草稿权限后，把 `WECHAT_APP_ID` 和 `WECHAT_APP_SECRET` 放入运行进程环境，不写入仓库。先只读探测，再送审稿：

```powershell
F:/anaconda3/python.exe -m wechat_editorial.cli probe
F:/anaconda3/python.exe -m wechat_editorial.cli draft output/wechat/YYYYMMDD/<topic_id>/v1
```

程序上传自制封面，调用新增草稿并回读标题、摘要、正文与封面标识。远端草稿 ID 在回读前落盘，网络抖动后重试不会重复建稿。`draft` 不调用发表或群发接口。若探测失败，编辑使用 `manual_import.md` 和 `article.html` 手工导入，并在公众号后台预览与发布。

若编辑退稿、要求修改或完成发布，记录决定和原因：

```powershell
F:/anaconda3/python.exe -m wechat_editorial.cli review output/wechat/YYYYMMDD/<topic_id>/v1 --decision needs_revision --reason "需核对原文措辞"
```

`review_log.jsonl` 是审稿记录；`rejected` 或 `needs_revision` 的版本不可再送入草稿箱。修改后的内容另建版本。正式上线前先进行两周每日生成及人工审校，核验误报、漏报和账号权限。

## 每周数据回流

从公众号后台导出 CSV，至少包含 `文章ID` 或 `标题`；可选列为 `日期`、`阅读量`、`分享量`、`新增关注`（也接受英文列名）。只汇总实际存在的字段，按文章 ID 或标题与本地选题、标题和文章结构关联。

```powershell
F:/anaconda3/python.exe -m wechat_editorial.cli import-metrics weekly.csv --out output/wechat/metrics/weekly.json
```

每周复盘阅读与分享、选题类型、标题写法及 `review_log.jsonl` 中的修改原因。不要把非随机的历史对比称为 A/B 实验。

## 凭据与上线注意

`tools/.env` 已从 Git 索引移除，本机文件保留并由 `.gitignore` 忽略；原 Tavily 凭据已迁入本机 `tools/.env` 的 `HEADLINES_TAVILY_API_KEY`，配置加载器也允许进程环境变量覆盖。新机器可参照 `tools/.env.example`。由于密钥曾存在于 Git 历史，仍需在 Tavily 和相关模型服务后台轮换。公众号 AppSecret 必须只通过进程环境或专门密钥存储提供。
