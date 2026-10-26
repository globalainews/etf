const crypto = require('crypto');
const { noData, forbidden } = require('./errors');
const v = require('./validate');

const COLLECTION = {
  status: 'publish_status', master: 'etf_master', history: 'etf_history', ranking: 'ranking_snapshots',
  streak: 'streak_snapshots', anomaly: 'anomaly_snapshots', sector: 'sector_snapshots',
  watchlist: 'user_watchlists', entitlement: 'user_entitlements'
};

const watchId = (openid, code) => crypto.createHash('sha256').update(`${openid}:${code}`).digest('hex');
const userId = (openid) => crypto.createHash('sha256').update(`user:${openid}`).digest('hex');

function createService(db, getContext) {
  const _ = db.command;

  async function one(collection, where) {
    const result = await db.collection(collection).where(where).limit(1).get();
    return (result.data || [])[0] || null;
  }

  async function current() {
    const item = await one(COLLECTION.status, { _id: 'current' });
    if (!item) throw noData('尚未发布可用数据批次');
    const timestamp = Date.parse(item.published_at || item.updated_at || '');
    return { ...item, is_stale: Number.isFinite(timestamp) ? Date.now() - timestamp > 96 * 3600 * 1000 : true };
  }

  function context() {
    const value = getContext();
    if (!value || !value.OPENID) throw forbidden('无法识别当前微信用户');
    return { openid: value.OPENID, appid: value.APPID || '' };
  }

  async function paged(collection, where, order, args) {
    const page = v.page(args.page);
    const pageSize = v.pageSize(args.pageSize);
    let query = db.collection(collection).where(where);
    order.forEach(([field, direction]) => { query = query.orderBy(field, direction); });
    const result = await query.skip((page - 1) * pageSize).limit(pageSize).get();
    return { items: result.data || [], page, pageSize, hasMore: (result.data || []).length === pageSize };
  }

  async function status() {
    return current();
  }

  async function rankings(args) {
    const release = await current();
    const direction = v.oneOf(args.direction, ['increase', 'decrease'], 'increase', '方向');
    const sort = v.oneOf(args.sort, ['delta', 'rate'], 'delta', '排序字段');
    const field = sort === 'rate' ? 'change_rate' : 'delta_shares';
    const order = direction === 'increase' ? 'desc' : 'asc';
    const result = await paged(COLLECTION.ranking, { release_id: release.release_id, direction }, [[field, order], ['code', 'asc']], args);
    return { ...result, release };
  }

  async function search(args) {
    const release = await current();
    const term = v.keyword(args.keyword);
    const limit = Math.min(v.pageSize(args.pageSize || 20), 20);
    const result = await db.collection(COLLECTION.master).where({ search_keys: term }).orderBy('code', 'asc').limit(limit).get();
    const items = result.data || [];
    return { items, keyword: term, release, note: items.length ? '' : '数据库中没有匹配记录；这不代表市场上不存在该ETF。' };
  }

  async function detail(args) {
    const release = await current();
    const code = v.code(args.code);
    const period = v.period(args.period);
    const master = await one(COLLECTION.master, { _id: code });
    if (!master) throw noData('数据库中没有该ETF记录');
    const byDate = new Map();
    for (let offset = 0; offset < 500 && byDate.size < period; offset += 100) {
      const raw = await db.collection(COLLECTION.history).where({
        code,
        date: _.lte(release.statistic_date),
        release_seq: _.lte(release.release_seq)
      }).orderBy('date', 'desc').orderBy('release_seq', 'desc').skip(offset).limit(100).get();
      for (const row of raw.data || []) if (!byDate.has(row.date)) byDate.set(row.date, row);
      if ((raw.data || []).length < 100) break;
    }
    const history = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date)).slice(-period);
    const snapshotResult = await db.collection(COLLECTION.ranking).where({ release_id: release.release_id, code }).limit(1).get();
    const streakResult = await db.collection(COLLECTION.streak).where({ release_id: release.release_id, code }).limit(1).get();
    const anomalyResult = await db.collection(COLLECTION.anomaly).where({ release_id: release.release_id, code }).limit(1).get();
    return {
      master, snapshot: (snapshotResult.data || [])[0] || null, history,
      streak: (streakResult.data || [])[0] || null,
      anomaly: (anomalyResult.data || [])[0] || null,
      requestedPeriod: period, availablePoints: history.length, release
    };
  }

  async function streaks(args) {
    const release = await current();
    const direction = v.oneOf(args.direction, ['increase', 'decrease'], 'increase', '方向');
    const result = await paged(COLLECTION.streak, { release_id: release.release_id, direction, status: 'OK' }, [['days', 'desc'], ['code', 'asc']], args);
    return { ...result, release, rule: '按连续有效统计日计数；份额不变或缺失均中断。' };
  }

  async function anomalies(args) {
    const release = await current();
    const result = await paged(COLLECTION.anomaly, { release_id: release.release_id, qualified: true }, [['anomaly_ratio', 'desc'], ['code', 'asc']], args);
    return { ...result, release, disclaimer: release.disclaimer };
  }

  async function sectors(args) {
    const release = await current();
    const result = await paged(COLLECTION.sector, { release_id: release.release_id }, [['median_change_rate', 'desc'], ['sector', 'asc']], args);
    return { ...result, release };
  }

  async function sectorSamples(args) {
    const release = await current();
    const sector = String(args.sector || '').trim();
    if (!sector || sector.length > 40) throw require('./errors').badRequest('板块名称无效');
    const result = await paged(COLLECTION.ranking, { release_id: release.release_id, sector }, [['change_rate', 'desc'], ['code', 'asc']], args);
    return { ...result, release, sector };
  }

  async function watchlistList() {
    const { openid } = context();
    const release = await current();
    const watch = await db.collection(COLLECTION.watchlist).where({ openid }).orderBy('created_at', 'desc').limit(50).get();
    const codes = (watch.data || []).map(item => item.code);
    if (!codes.length) return { items: [], release };
    const snapshots = await db.collection(COLLECTION.ranking).where({ release_id: release.release_id, code: _.in(codes) }).limit(50).get();
    const byCode = new Map((snapshots.data || []).map(item => [item.code, item]));
    return { items: (watch.data || []).map(item => ({ ...item, snapshot: byCode.get(item.code) || null })), release };
  }

  async function watchlistAdd(args) {
    const { openid } = context();
    const code = v.code(args.code);
    const exists = await one(COLLECTION.master, { _id: code });
    if (!exists) throw noData('数据库中没有该ETF记录');
    const id = watchId(openid, code);
    await db.collection(COLLECTION.watchlist).doc(id).set({ data: {
      _id: id, openid, code, observation: Boolean(args.observation), created_at: db.serverDate(), updated_at: db.serverDate()
    }});
    return { code, added: true };
  }

  async function watchlistRemove(args) {
    const { openid } = context();
    const code = v.code(args.code);
    await db.collection(COLLECTION.watchlist).doc(watchId(openid, code)).remove();
    return { code, removed: true };
  }

  async function userStatus() {
    const { openid } = context();
    const stored = await one(COLLECTION.entitlement, { _id: userId(openid) });
    if (!stored) return { tier: 'basic', membershipActive: false, expiresAt: null, source: 'default', plansOpen: false };
    return {
      tier: stored.tier === 'member' ? 'member' : 'basic',
      membershipActive: stored.status === 'active',
      expiresAt: stored.expires_at || null,
      source: 'cloud', plansOpen: false
    };
  }

  return { status, rankings, search, detail, streaks, anomalies, sectors, sectorSamples,
    'watchlist.list': watchlistList, 'watchlist.add': watchlistAdd, 'watchlist.remove': watchlistRemove,
    'user.status': userStatus };
}

module.exports = { createService, watchId, userId, COLLECTION };

