# ETF 龙虎榜素材生产器

在 `D:\antigravity` 下运行：

```powershell
F:\anaconda3\python.exe -X utf8 -m etf_longhubang.cli
```

默认目标日为上海时区昨天。正常交易日先调用
`D:\claude projects\ETF\daily_update.py --date <YYYYMMDD> --scale-only`，随后读取
`etf.db` 并发布到 `D:\antigravity\ETF\<YYYYMMDD>\`。非交易日只写
`_SKIP.json`。

常用参数：

```powershell
# 使用已有数据库重建历史交易日
F:\anaconda3\python.exe -X utf8 -m etf_longhubang.cli --date 2026-10-23 --publish-date 2026-10-24 --skip-refresh

# 相同内容也生成下一修订版本
F:\anaconda3\python.exe -X utf8 -m etf_longhubang.cli --date 2026-10-23 --skip-refresh --force-revision
```

也可调用无 `pause` 的 `run_etf_longhubang.bat`，适合 Windows 任务计划程序。

退出码：`0` 成功或正常非交易日，`20` 数据源不可用，`30` 数据不足，`40`
契约或页面质检失败，`50` 发布失败，`60` 已有任务运行。`20/30` 会写带相应
`reason_code` 的 `_SKIP.json`，同时保留非零退出码供调度告警。

运行审计、四页截图和 QA 报告保存于
`D:\antigravity\output\etf_longhubang_runs\`，不进入交付包。

## 从素材包生成短视频

素材生产器成功发布 `ETF\YYYYMMDD\` 后，运行：

```powershell
F:\anaconda3\python.exe -X utf8 -m etf_longhubang.video_cli --date 20261023
```

或使用批处理：

```powershell
build-etf-longhubang-video.bat --date 20261023
```

不传 `--date` 时会自动选择 `ETF\` 下日期最新且包含 `_READY.json` 的素材包，
因此可直接用于每日调度，不会在周末误选一个不存在的自然日目录。

视频流水线依次执行素材契约校验、1080×1920 页面渲染、Global CosyVoice3
分句配音、实测音频驱动字幕、页面视频编码、拼接和自动质检。中间产物保存在
`cache\etf_longhubang_video\YYYYMMDD\`，可按输入哈希断点复用；最终交付物位于：

```text
output/ETF/YYYYMMDD/
├── ETF龙虎榜_YYYYMMDD.mp4
├── ETF龙虎榜_YYYYMMDD_cover.jpg
├── etf_longhubang.json
├── narration.json
├── subtitle_plan.json
├── subtitles.srt
├── input_validation.json
├── qa_report.json
└── run_manifest.json
```

常用参数：

```powershell
# 从字幕阶段继续（前置产物必须存在）
build-etf-longhubang-video.bat --date 20261023 --from-stage subtitles

# 只做到页面渲染，便于先检查画面
build-etf-longhubang-video.bat --date 20261023 --to-stage pages

# 忽略缓存完整重建
build-etf-longhubang-video.bat --date 20261023 --force
```

自动 QA 检查输入哈希、页面安全区、分辨率、编码、总时长、黑帧和静音。
成片发布前仍需人工抽听 ETF 专名与数字读音，并检查字幕同步和页面语义匹配。
