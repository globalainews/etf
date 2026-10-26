# Windows运行、部署与验收

## 1. 本地测试

项目规范指定使用 `F:\anaconda3\python.exe`：

```powershell
cd "D:\claude projects\ETF"
F:\anaconda3\python.exe -X utf8 -m pip install -r sync\requirements.txt
F:\anaconda3\python.exe -X utf8 -m pytest tests\test_miniprogram_compute.py tests\test_miniprogram_sync.py -q --basetemp .codex_stage\pytest-mini
npm test --prefix cloudfunctions\etf-api
```

同步运行时仅用Python标准库；`pytest`只用于测试。

## 2. 运行demo

1. 保持 [env.js](../miniprogram/config/env.js) 中 `mode: 'demo'`。
2. 微信开发者工具选择“导入项目”，目录选仓库根目录；首次可使用测试号或把 `project.config.json` 的 `appid` 换为自己的小程序AppID。
3. 编译后按“首页 → 完整榜单 → ETF详情 → 7/30/90日 → 加入自选 → 自选 → 搜索”验收。
4. demo页始终显示演示标记，自选只保存到本地Storage，不写正式集合。

## 3. 配置云环境

1. 在微信开发者工具或CloudBase平台创建环境，记录环境ID。
2. 在CloudBase的API Key管理页创建**服务端**API Key；只放入Windows环境变量，禁止写入小程序或提交Git。
3. PowerShell当前会话设置：

```powershell
$env:CLOUDBASE_ENV_ID="your-env-id"
$env:CLOUDBASE_API_KEY="your-server-api-key"
$env:ETF_DB_PATH="D:\claude projects\ETF\etf.db"
```

4. 创建集合和索引：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.bootstrap
```

5. 在每个集合应用 `cloud-database/database-security-rules.json`（客户端不可直读写）；在云函数权限控制应用 `cloudfunctions/security-rules.json`。

## 4. 部署云函数

在微信开发者工具中右键 `cloudfunctions/etf-api`，选择“上传并部署：云端安装依赖”，运行时选择 `Nodejs20.19`，入口 `index.main`，超时建议10秒。不要把 `CLOUDBASE_API_KEY` 配到此查询函数。

云函数依赖固定为 `wx-server-sdk@4.0.2`（MIT），并由 `package-lock.json` 锁定完整依赖树；小程序图表使用本地原生Canvas实现，没有远程脚本和第三方图表包。

然后修改 [env.js](../miniprogram/config/env.js)：

```js
module.exports = {
  mode: 'real',
  cloudEnvId: 'your-env-id',
  cloudFunctionName: 'etf-api',
  requestTimeoutMs: 12000
};
```

`real`调用失败会显示错误和重试，不会静默读取demo。

## 5. 同步命令

云同步现在只读取SQLite已发布派生批次。首次使用先执行：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --full --dry-run
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --full
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --verify
```

只计算和校验，不写云端：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --dry-run --initial
```

首次同步最近90个可用统计日：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --initial
```

日常增量（最新统计日 + 全部最新派生快照）：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --incremental
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli
```

历史更正并重算派生结果：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.local_cli --correction-from 2026-10-01
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.cli --correction-from 2026-10-01
```

输出会给出发布ID、日期范围、各集合待写数量、覆盖和最终发布状态。失败日志只记录异常类型，不打印API Key。建议先把日常命令独立接在现有采集任务成功之后；本实现不会自动创建定时任务，也不会改变现有视频任务退出码。

## 6. 尚需人工验证

- 当前机器未检测到已配置的真实CloudBase环境，因此没有宣称云端部署成功。
- 需要在微信开发者工具模拟器检查Canvas触点、长名称、断网、旧批次提示和安全区；再在至少一台iOS和Android真机检查布局。
- 上线前核实主体/类目、金融数据展示许可、隐私政策、会员协议及支付资质。首版不接支付。

