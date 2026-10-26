# ETF龙虎榜本地Web、SQLite物化与视频素材

## 1. 数据流

`funds/fund_scale/fund_price` 是原始事实表。`etf_derived_daily`和
`etf_sector_daily`是可重建的派生结果。计算结果先写入暂存表，校验后在一个
SQLite事务中替换受影响日期并更新 `etf_calc_publish_status/current`。

云同步只读取该发布指针；未发布或失败批次不会进入CloudBase。

云端发布以单文档 `publish_status/current` 为原子可见性边界：业务文档校验完成后先把
`sync_batches` 标为 `validated`，再原子更新当前指针，最后补记 `published` 审计状态。
不依赖容易超过30秒时限的跨文档REST事务；指针写入失败时旧指针保持不变。

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

当前计算规模策略：日变动、详情和趋势仍保留所有已覆盖ETF；增加、减少榜各只物化前100名。连续和异动仅计算当日份额严格超过100亿份的ETF，门槛以下不生成相应云端快照。

## 4. 本地Web

```powershell
F:\anaconda3\python.exe -X utf8 app.py
```

访问：

- 原有首页：`http://127.0.0.1:5010/`
- 完整龙虎榜：`http://127.0.0.1:5010/ranking-app`
- 视频工作台：`http://127.0.0.1:5010/ranking-video`

端口被占用时可在当前PowerShell会话设置：

```powershell
$env:ETF_WEB_PORT="5011"
F:\anaconda3\python.exe -X utf8 app.py
```

Web自选保存在 `web_watchlists`，固定用户为 `local-default`，不增加伪微信身份或登录系统。

### 历史龙虎榜

页面顶部的“统计日”控件适用于首页、完整榜单、ETF详情、板块、连续变化和异动观察：

- 直接选择任一已物化的历史交易日；没有已发布结果的日期会明确提示无数据；
- `‹`、`›` 只在实际已发布交易日之间切换，不按自然日跳转；
- “最新”回到当前本地发布批次的统计日；
- URL 可用 `http://127.0.0.1:5010/ranking-app?date=YYYY-MM-DD` 直达某一历史日，便于制作历史回顾视频；
- 页面样本数、基期、排名、价格比较和风险提示均以所选统计日计算，不能用最新日覆盖信息冒充历史数据。

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

完整同步会核验每个 CloudBase `runCommands` 更新结果；任一批次未得到足额服务端确认就会失败，
并且不会切换发布指针。当前网关的集合列表/计数REST端点与API Key通道不兼容，因此不把其
错误的零计数作为发布依据。

若希望在完整上传前单独完成这一步，可先运行：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --initial --write-probe
```

输出 `"ok": true` 后，再运行完整 `--initial`。

如果首次上传已完成全部集合，但只在最终发布指针事务处失败，不要重新上传14万条历史。
使用恢复模式校验云端各集合计数，并只重试发布指针：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --initial --resume-publish
```

恢复模式不会写入ETF业务集合。即使旧版失败时没有留下 `sync_batches` 标记，仍可用实际
`release_id` 记录数完成受限恢复；任何集合计数不匹配时都会拒绝发布，届时才需要重新执行完整同步。

如果 CloudBase 控制台已人工确认该失败批次的业务文档完整，但 REST 查询兼容性校验不可用，
可使用更严格的人工核验发布命令。它要求云端存在同一 `release_id`、`release_seq` 和记录数的
失败批次，只发布指针，不会读写ETF业务集合：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --initial --publish-verified
```

恢复前或环境ID可能填错时，可执行只读诊断（不会写入云端）：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --initial --diagnose-cloud
```

输出会显示当前 `env_id`、本地发布批次、各集合总数以及该批次记录数。先确认 `env_id`
与 CloudBase 控制台顶部显示的环境ID一致，再决定是否需要完整同步。

同步客户端使用 CloudBase `POST documents:count` 条件计数接口校验 `release_id`，不能用集合
总数代替批次一致性校验。

## 6. 视频素材工作台

`/ranking-video` 是独立于手机龙虎榜的本地视频生产界面。它只读取
`etf_calc_publish_status/current` 指向的 SQLite 已发布批次，不会重新计算排名，
不会写回行情或云端。

默认预设为“每日双榜＋焦点”，输出约20秒的五段项目：开场、增加TOP5、减少TOP5、
焦点ETF、品牌结尾。可选择任何已物化的历史交易日，并可在当天可靠ETF样本中手动
替换焦点。自动焦点按“异动→连续变化不少于3日→绝对变化率”顺序选择；特殊事件、
质量异常和关键数据缺失记录不会自动入选。

视频纯画面路由为：

```text
http://127.0.0.1:5010/ranking-video/frame/open?date=YYYY-MM-DD
http://127.0.0.1:5010/ranking-video/frame/increase?date=YYYY-MM-DD
http://127.0.0.1:5010/ranking-video/frame/decrease?date=YYYY-MM-DD
http://127.0.0.1:5010/ranking-video/frame/focus?date=YYYY-MM-DD
http://127.0.0.1:5010/ranking-video/frame/closing?date=YYYY-MM-DD
```

所有纯画面固定为1080×1920，采用Global简读深色编辑风，并在字体、溢出、批次、
统计日和安全区检查通过后才暴露 `window.__videoRenderState.status="ready"`。

### 命令行素材包

先启动Web，然后执行：

```powershell
# 输出项目JSON、审计、5个离线HTML场景、动态旁白和SRT
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.video_cli --preset daily-20s --render data

# 启动本地Web后，额外生成5张1080×1920 PNG
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.video_cli --preset daily-20s --render stills

# 指定历史日并人工指定当日可靠ETF为焦点
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.video_cli --date 2026-10-23 --focus-code 510300 --render all
```

可用 `--date YYYY-MM-DD`、`--base-url http://127.0.0.1:5011` 和 `--output <目录>`
指定历史日、Web端口和输出位置。默认输出位于
`output/etf-ranking-video/YYYYMMDD`，该目录被Git忽略。

默认输出位于 `output/etf-ranking-video/YYYYMMDD/`：

```text
video-project.json     # 场景、批次、数据、焦点和品牌配置
content.json/audit.json
pages/                 # 可离线打开的1080×1920 HTML素材页
frames/                # 仅 --render stills/all 时生成的PNG
narration.json/.txt/.srt
qa-report.json         # 分辨率、时长、批次和渲染检查
manifest.json          # 输出文件SHA-256清单
```

数据质量未达发布条件或渲染质量检查失败时仅写 `_SKIP.json`，不会生成可交付的
成功清单。Global 视频程序应读取 `video-project.json`、PNG、`narration.json` 和
`manifest.json`，不得重新计算排名。

当五张PNG已经生成后，可先由 Global 适配器进行只读交接校验：

```powershell
F:\anaconda3\python.exe -X utf8 D:\antigravity\etf_longhubang\workbench_adapter.py `
  "D:\claude projects\ETF\output\etf-ranking-video\20261023"
```

适配器校验 `_SKIP.json`、批次、交易日、质量报告、五个场景和 manifest SHA-256，
输出 `global-handoff.json`。它不会从原始ETF表重新计算日榜。

## 7. 验收

```powershell
F:\anaconda3\python.exe -X utf8 -m pytest tests\test_miniprogram_compute.py tests\test_miniprogram_sync.py tests\test_local_materialization.py tests\test_mini_web_api.py -q --basetemp .codex_stage\pytest-ranking
npm test --prefix cloudfunctions\etf-api
node --check static\ranking-app.js
```

已用本机Chromium检查桌面、390×844手机和1080×1920视频画幅。微信开发者工具和真机验证仍按
`docs/miniprogram-runbook.md` 执行。
