const crypto = require('crypto');
const { noData, forbidden, membershipRequired } = require('./errors');
const v = require('./validate');

const COLLECTION = {
  status: 'publish_status', master: 'etf_master', history: 'etf_history', ranking: 'ranking_snapshots',
  streak: 'streak_snapshots', anomaly: 'anomaly_snapshots', sector: 'sector_snapshots',
  watchlist: 'user_watchlists', entitlement: 'user_entitlements', member: 'user_members'
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

  function chinaDate() {
    return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' })
      .format(new Date());
  }

  function publicMember(stored) {
    if (!stored) return {
      tier: 'basic', membershipActive: false, applicationStatus: 'not_applied',
      memberStartDate: null, memberEndDate: null, source: 'default', plansOpen: false
    };
    const today = chinaDate();
    const active = stored.status === 'active' && stored.is_member === true
      && (!stored.member_start_date || stored.member_start_date <= today)
      && (!stored.member_end_date || stored.member_end_date >= today);
    const applicationStatus = active ? 'active'
      : (stored.status === 'active' && stored.member_end_date && stored.member_end_date < today ? 'expired' : (stored.status || 'pending'));
    return {
      tier: active ? 'member' : 'basic', membershipActive: active, applicationStatus,
      memberStartDate: stored.member_start_date || null, memberEndDate: stored.member_end_date || null,
      nickname: stored.nickname || '', videoMemberName: stored.video_member_name || '',
      appliedAt: stored.applied_at || null, reviewedAt: stored.reviewed_at || null,
      reviewNote: stored.review_note || '', source: 'cloud', plansOpen: false
    };
  }

  async function memberFor(openid) {
    return one(COLLECTION.member, { _id: userId(openid) });
  }

  async function requireMember(openid) {
    const member = publicMember(await memberFor(openid));
    if (!member.membershipActive) {
      throw membershipRequired('搜索和新增自选仅向已同步的会员开放，请先成为视频号：Global速览的会员后提交核验。');
    }
    return member;
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
    v.oneOf(args.sort, ['delta'], 'delta', '排序字段');
    const order = direction === 'increase' ? 'desc' : 'asc';
    const result = await paged(COLLECTION.ranking, { release_id: release.release_id, direction }, [['delta_shares', order], ['code', 'asc']], args);
    return { ...result, release };
  }

  async function search(args) {
    const { openid } = context();
    await requireMember(openid);
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
    const result = await paged(COLLECTION.streak, { release_id: release.release_id, direction, status: 'OK', days: _.gte(2) }, [['days', 'desc'], ['code', 'asc']], args);
    return { ...result, release, rule: '按连续有效统计日计数；份额不变或缺失均中断。' };
  }

  async function anomalies(args) {
    const release = await current();
    const result = await paged(COLLECTION.anomaly, { release_id: release.release_id, qualified: true }, [['anomaly_ratio', 'desc'], ['code', 'asc']], args);
    const rule = result.items[0] && result.items[0].rule
      ? result.items[0].rule
      : { window: 20, min_history: 20, ratio_threshold: 3, abs_pct_threshold: 1, min_shares: 10000000000, share_unit: '份', excludes_current: true };
    return { ...result, rule, release, disclaimer: release.disclaimer };
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
    await requireMember(openid);
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
    return publicMember(await memberFor(openid));
  }

  async function memberApply(args) {
    const { openid } = context();
    const nickname = v.memberName(args.nickname, '微信昵称', 32);
    const videoMemberName = v.memberName(args.videoMemberName, '视频号会员名称', 40);
    const id = userId(openid);
    const previous = await memberFor(openid);
    if (previous && previous.status === 'active' && publicMember(previous).membershipActive) {
      return { ...publicMember(previous), submitted: false, message: '会员资格已生效，无需重复提交。' };
    }
    const now = db.serverDate();
    const record = {
      ...(previous || {}), _id: id, openid, nickname, video_member_name: videoMemberName,
      status: 'pending', is_member: false, applied_at: previous && previous.applied_at ? previous.applied_at : now,
      resubmitted_at: previous ? now : null, reviewed_at: null, review_note: '', review_source: '', updated_at: now,
      schema_version: '1.0'
    };
    await db.collection(COLLECTION.member).doc(id).set({ data: record });
    return { ...publicMember({ ...record, status: 'pending' }), submitted: true,
      message: '申请已提交。请先成为视频号：Global速览的会员，管理员核验后同步权益。' };
  }

  return { status, rankings, search, detail, streaks, anomalies, sectors, sectorSamples,
    'watchlist.list': watchlistList, 'watchlist.add': watchlistAdd, 'watchlist.remove': watchlistRemove,
    'user.status': userStatus, 'member.apply': memberApply };
}

module.exports = { createService, watchId, userId, COLLECTION };

