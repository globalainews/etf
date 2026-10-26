# ETF龙虎榜本地Web、SQLite物化与视频素材

## 1. 数据流

`funds/fund_scale/fund_price` 是原始事实表。`etf_derived_daily`和
`etf_sector_daily`是可重建的派生结果。计算结果先写入暂存表，校验后在一个
SQLite事务中替换受影响日期并更新 `etf_calc_publish_status/current`。

云同步只读取该发布指针；未发布或失败批次不会进入CloudBase。

## 2. 首次回填

建议先备份 `etf.db`，再执行：

```powershell
cd "D:\claude projects\ETF"
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --full --dry-run
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --full
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --verify
```

2026-10-26已用当时数据完成一次回填：2019-01-02至2026-10-23，
`etf_derived_daily` 1,402,412条，`etf_sector_daily` 29,598条。数据库由约171MB
增长至约1.07GB；后续日常增量远小于首次回填。

## 3. 每日与历史修正

```powershell
# 采集和价格更新成功后
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --incremental

# 历史数据从某日起有更正
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --correction-from 2026-10-01

# 校验发布指针和最新日覆盖
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --verify
```

命令完全独立，未自动加入现有采集或视频定时任务。计算失败时保留上一成功批次。

## 4. 本地Web

```powershell
F:\anaconda3\python.exe -X utf8 app.py
```

访问：

- 原有首页：`http://127.0.0.1:5010/`
- 完整龙虎榜：`http://127.0.0.1:5010/ranking-app`

端口被占用时可在当前PowerShell会话设置：

```powershell
$env:ETF_WEB_PORT="5011"
F:\anaconda3\python.exe -X utf8 app.py
```

Web自选保存在 `web_watchlists`，固定用户为 `local-default`，不增加伪微信身份或登录系统。

Web图表使用仓库内的 ECharts 5.5.0，许可证为
`static/vendor/ECHARTS-LICENSE.txt`；页面不加载远程脚本。

## 5. CloudBase同步

必须先有成功本地批次：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --dry-run --initial
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --initial
```

日常执行：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --incremental
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --dry-run
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli
```

真实云写入仍需要 `.env.example` 中列出的CloudBase环境变量。不会从Web页面或小程序包内读取服务端密钥。

## 6. 视频素材

先启动Web，然后执行：

```powershell
# 输出JSON、审计、7个HTML场景、旁白和SRT
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.video_cli --render data

# 额外调用agent-browser生成7张1080×1920 PNG
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.video_cli --render stills
```

可用 `--date YYYY-MM-DD`、`--base-url http://127.0.0.1:5011` 和 `--output <目录>`
指定历史日、Web端口和输出位置。默认输出位于
`output/etf-ranking-video/YYYYMMDD`，该目录被Git忽略。

视频模式支持日增、日减、连续、异动、板块、ETF详情和TOP10结尾。
数据质量未达发布条件时仅写 `_SKIP.json`。旧的FFmpeg/TTS合成可继续消费这些PNG、
`narration.txt`和 `narration.srt`；素材程序本身不重新计算榜单。

## 7. 验收

```powershell
F:\anaconda3\python.exe -X utf8 -m pytest tests\test_miniprogram_compute.py tests\test_miniprogram_sync.py tests\test_local_materialization.py tests\test_mini_web_api.py -q --basetemp .codex_stage\pytest-ranking
npm test --prefix cloudfunctions\etf-api
node --check static\ranking-app.js
```

已用本机Chromium检查桌面、390×844手机和1080×1920视频画幅。微信开发者工具和真机验证仍按
`docs/miniprogram-runbook.md` 执行。
