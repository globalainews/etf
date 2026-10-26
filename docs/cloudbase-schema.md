# CloudBase集合、索引与权限

## 集合

| 集合 | 文档粒度/业务键 | 主要字段 | 客户端权限 |
|---|---|---|---|
| `etf_master` | 每ETF；`_id=code` | 名称、市场、类型、分类、搜索前缀、schema版本 | 禁止直读写 |
| `etf_history` | 每ETF/日期/修订；`code_date_releaseSeq` | 份额、收盘价、缺失状态、来源 | 禁止直读写 |
| `ranking_snapshots` | 每发布批次/ETF/方向前100名 | 增量、变化率、价格变化、质量状态 | 禁止直读写 |
| `streak_snapshots` | 每发布批次/连续至少2日且份额>100亿的ETF | 方向、天数、起止日、累计变化 | 禁止直读写 |
| `anomaly_snapshots` | 每发布批次/份额>100亿且满足异动阈值的ETF | 当日变化率、历史中位数、倍数、规则 | 禁止直读写 |

`ranking_snapshots` 按原始份额增减排序，增加、减少各最多100条。连续与异动的100亿门槛以原始份额“份”为单位，严格使用 `shares > 10,000,000,000`；门槛以下的产品不写入这两个集合。
| `sector_snapshots` | 每发布批次/板块 | 中位数、有效/缺失样本数、分类来源 | 禁止直读写 |
| `sync_batches` | 每发布尝试；`_id=release_id` | 状态、计数、来源签名、失败类型 | 禁止直读写 |
| `publish_status` | 单文档；`_id=current` | 当前发布指针、日期、覆盖与质量 | 禁止直读写 |
| `sync_locks` | 单文档；`_id=current` | 同步租约、过期时间 | 禁止直读写 |
| `user_watchlists` | 每用户/ETF；哈希 `_id` | `openid`、代码、观察标记 | 禁止直读写，仅云函数按当前用户访问 |
| `user_entitlements` | 每用户；哈希 `_id` | 等级、状态、有效期预留 | 禁止直读写，仅可信管理端维护 |

小程序只调用 `etf-api` 云函数。所有集合应用 [database-security-rules.json](../cloud-database/database-security-rules.json) 的 `read:false/write:false`，云函数和管理端不受客户端规则替代。云函数调用权限使用 [security-rules.json](../cloudfunctions/security-rules.json)，函数内部再按 `getWXContext().OPENID` 限定自选；事件参数中的 `openid` 会被忽略。

索引清单在 [indexes.json](../cloud-database/indexes.json)，也可执行：

```powershell
F:\anaconda3\python.exe -X utf8 -m sync.etf_sync.bootstrap
```

这会通过官方 NoSQL HTTP API 创建集合和索引。正式执行前必须配置仓库外的 `CLOUDBASE_ENV_ID` 和服务端 `CLOUDBASE_API_KEY`。

## 发布一致性

1. 获取带过期时间的 `sync_locks/current` 租约；发布前再次确认租约所有者。
2. 新建/更新 `sync_batches` 为 `uploading`，幂等上传基础资料、历史修订和整批派生快照。
3. 回查四类派生快照的 `release_id` 计数；任一不符即标记 `failed`。
4. 仅校验全部成功后，在一个数据库事务中把批次设为 `published` 并切换 `publish_status/current`。
5. 云函数每次查询先读取当前指针，再按对应 `release_id/release_seq` 查询。
6. 上传失败保留上一指针；未发布派生快照可按 `release_id` 延后清理，严禁删除当前指针引用的数据。

租约是防止两个同步进程互相发布的第二层保护；CloudBase事务保证最终指针与批次状态同进退。同步器不写 SQLite，也不与采集/视频流程共享事务或退出码。

## 官方能力核验

2026-10-26 已核对：

- [CloudBase NoSQL HTTP API](https://docs.cloudbase.net/http-api/nosql/nosql-restful-api) 支持文档CRUD、事务和数据库命令，使用 Bearer Token。
- [AccessToken/API Key说明](https://docs.cloudbase.net/http-api/basic/access-token) 明确 API Key 为长期、服务端、管理员权限凭据，严禁放客户端。
- [云函数运行环境](https://docs.cloudbase.net/cloud-function/runtime-support) 支持 Node.js 20.19 等运行时；部署配置选 Nodejs20.19。
- [索引管理](https://docs.cloudbase.net/database/data-index) 支持单字段、组合和唯一索引，并提示单集合索引建议不超过20个。
- [云函数安全规则](https://docs.cloudbase.net/cloud-function/security-rules) 只做函数级调用门禁，更细用户隔离仍需函数内部实现。

