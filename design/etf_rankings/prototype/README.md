# ETF短视频原型流水线

数据库始终以 SQLite `mode=ro` 打开。所有输出位于 `design/etf_rankings/`，不会修改现有 Flask、采集或回测模块。

在 `design/etf_rankings` 目录运行：

```powershell
F:\anaconda3\python.exe -X utf8 -m prototype.pipeline --date 2026-10-23 --render data
F:\anaconda3\python.exe -X utf8 -m prototype.pipeline --date 2026-10-23 --render stills
F:\anaconda3\python.exe -X utf8 -m prototype.pipeline --date 2026-10-23 --render video --voice auto
```

`--voice none` 会生成带静音AAC轨道的视频。`--voice auto` 依次尝试 `zh-CN-YunyangNeural` 与 `zh-CN-XiaoxiaoNeural`，失败时自动降级为静音。

输出数据保存在 `prototype/runs/<run_id>/`，`prototype/runs/latest/` 是最近一次可复现快照。`manifest.json` 记录执行前后数据库哈希、配置哈希及输出文件哈希。当前演示文件因受限网络下免费TTS超时而采用静音音轨，同时交付 `narration.txt` 与 `narration.srt`。

