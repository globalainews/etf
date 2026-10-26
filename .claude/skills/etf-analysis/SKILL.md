---
name: etf-analysis
description: |
  启动ETF基金规模趋势分析Web应用。当用户提到"ETF分析"、"基金规模趋势"、"启动ETF"、
  "ETF web应用"、"基金分析"、或想要查看ETF基金规模数据时触发。
  该应用提供交互式图表，支持两种分析模式：单只基金趋势对比和多基金汇总分析。
---

# ETF基金规模趋势分析

启动 ETF 基金规模趋势分析的 Web 应用。

## 应用信息

- **项目目录**: `D:\claude projects\ETF`
- **入口文件**: `app.py`
- **Python**: `F:\anaconda3\python`（Anaconda，已安装 pandas/flask）
- **端口**: `5010`
- **数据源**: `etf.db`（SQLite；1,583只基金，2025-01-02 起每日更新）

## 功能

| 功能 | 说明 |
|------|------|
| 📈 图形分析 | 选中基金各自绘制趋势曲线，对比单只走势 |
| 📊 汇总分析 | 选中基金按日期合计，绘制一条汇总曲线 |
| 🔍 搜索筛选 | 按基金名称或代码搜索 |
| ✅ 批量选择 | 全选 / Top N / Ctrl+点击多选 |

## 启动步骤

### 1. 处理端口占用

```bash
# 杀掉旧进程（如有）
powershell -Command "Get-NetTCPConnection -LocalPort 5010 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force -ErrorAction SilentlyContinue }" 2>/dev/null
sleep 2
```

### 2. 启动服务

```bash
cd "D:\claude projects\ETF" && F:\anaconda3\python app.py &
```

### 3. 等待服务就绪

```bash
sleep 4
# 验证 API 正常
curl -s "http://127.0.0.1:5010/api/funds" | F:\anaconda3\python -c "import sys,json; d=json.load(sys.stdin); print(f'OK: {len(d)} 只基金已加载')"
```

### 4. 打开浏览器

告知用户访问 **http://127.0.0.1:5010**，或执行：

```bash
start http://127.0.0.1:5010
```

## 注意事项

- 服务在后台运行（`&`），关闭终端不影响
- 如果端口被占用，先执行步骤1清理
- 数据库文件 `etf.db` 必须在项目目录下
- 启动加载 47万+条记录约1-2秒；数据更新后需重启服务才能看到新数据
