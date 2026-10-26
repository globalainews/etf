(() => {
  const state = { payload: null, view: 'daily', expanded: new Set(), requestId: 0 };
  const $ = id => document.getElementById(id);
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[char]);
  const signed = (value, digits = 1) => value == null ? '—' : `${value >= 0 ? '+' : ''}${Number(value).toFixed(digits)}`;
  const percent = value => value == null ? '—' : `${signed(value)}%`;
  const amount = value => value == null ? '—' : `${signed(value)}亿`;
  const colorClass = value => value > 0 ? 'rk-up' : value < 0 ? 'rk-down' : 'rk-muted';

  function setMode(mode) {
    const rankings = mode === 'rankings';
    $('analysisView').hidden = rankings;
    $('rankingsView').hidden = !rankings;
    $('analysisModeButton').classList.toggle('active', !rankings);
    $('analysisModeButton').setAttribute('aria-selected', String(!rankings));
    $('rankingsModeButton').classList.toggle('active', rankings);
    $('rankingsModeButton').setAttribute('aria-selected', String(rankings));
    if (rankings && !state.payload) loadRankings();
  }

  function renderAudit(payload) {
    const { audit, meta } = payload;
    $('rkAudit').innerHTML = [
      `<span>交易日 <strong>${escapeHtml(meta.trade_date)}</strong></span>`,
      `<span>价格匹配 <strong>${audit.price_match_count}/${audit.scale_count}</strong> 只</span>`,
      `<span>缺价格 <strong>${audit.missing_price_count}</strong> 只</span>`
    ].join('');
    const warnings = audit.warnings || [];
    $('rkWarnings').hidden = warnings.length === 0;
    $('rkWarnings').innerHTML = warnings.map(warning => `<div>${escapeHtml(warning)}</div>`).join('');
  }

  function headline(payload) {
    const top = payload.daily.increase[0];
    if (!top) return '当日没有可用于收盘参考市值排名的ETF。';
    return `${top.name}当日份额${signed(top.delta_shares_yi)}亿份，对应收盘参考市值${amount(top.reference_value_yi)}，位居增加榜首。`;
  }

  function row(record, maxValue, weekly = false) {
    const reference = weekly ? record.reference_5d_yi : record.reference_value_yi;
    const direction = reference >= 0 ? 'up' : 'down';
    const width = Math.max(3, Math.abs(reference || 0) / (maxValue || 1) * 100);
    const extra = weekly
      ? `${record.positive_days_5}/5日增 · ${record.negative_days_5}/5日减`
      : `份额 <b class="${colorClass(record.share_change_pct)}">${percent(record.share_change_pct)}</b> · 价格 <b class="${colorClass(record.price_change_pct)}">${percent(record.price_change_pct)}</b>`;
    const spark = weekly ? sparkline(record.series_5 || [], direction) : '';
    return `<article class="rk-row ${weekly ? 'rk-week-row' : ''}" style="animation-delay:${Math.min(record.rank, 8) * 35}ms">
      <div class="rk-rank">${String(record.rank).padStart(2, '0')}</div>
      <div class="rk-name" title="${escapeHtml(record.name)}"><strong>${escapeHtml(record.name)}</strong><span>${escapeHtml(record.code)} · ${escapeHtml(record.asset_class || '待核验')}</span>${record.is_abnormal ? '<em class="rk-badge">历史异常</em>' : ''}${spark}</div>
      <div class="rk-bar-cell"><div class="rk-bar ${direction}"><i style="width:${width}%"></i></div></div>
      <div class="rk-row-meta"><strong class="rk-value ${colorClass(reference)}">${amount(reference)}</strong><span class="rk-submeta">${extra}</span></div>
    </article>`;
  }

  function sparkline(points, direction) {
    if (!points.length) return '';
    const values = points.map(point => point.shares_yi);
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    const coordinates = values.map((value, index) => `${index / Math.max(values.length - 1, 1) * 100},${30 - (value - min) / span * 25}`).join(' ');
    const color = direction === 'up' ? '#ff665f' : '#2ed28b';
    return `<svg class="rk-spark" viewBox="0 0 100 34" preserveAspectRatio="none" aria-label="6个交易日份额趋势"><line x1="0" y1="30" x2="100" y2="30" stroke="#23344a"/><polyline points="${coordinates}" fill="none" stroke="${color}" stroke-width="2.5" vector-effect="non-scaling-stroke"/></svg>`;
  }

  function board(id, title, subtitle, records, weekly = false) {
    if (!records.length) return `<section class="rk-board"><div class="rk-board-head"><h3>${title}</h3><span>${subtitle}</span></div><div class="rk-empty">本期无满足条件的ETF</div></section>`;
    const expanded = state.expanded.has(id);
    const shown = expanded ? records : records.slice(0, 5);
    const maxValue = Math.max(...records.map(record => Math.abs(weekly ? record.reference_5d_yi : record.reference_value_yi) || 0));
    return `<section class="rk-board"><div class="rk-board-head"><h3>${title}</h3><span>${subtitle}</span></div>${shown.map(record => row(record, maxValue, weekly)).join('')}${records.length > 5 ? `<div class="rk-board-foot"><button class="rk-toggle" data-rk-toggle="${id}">${expanded ? '收起TOP10' : '展开TOP10'}</button></div>` : ''}${weekly ? '<div class="rk-week-caption"><span>微型趋势显示6个份额观测点</span><span>5日参考市值使用期末收盘价估算</span></div>' : ''}</section>`;
  }

  function renderDaily() {
    const { daily, meta } = state.payload;
    const counts = daily.counts;
    $('rkContent').innerHTML = `<section class="rk-stat-grid"><div class="rk-stat"><label>份额增加</label><strong class="rk-up">${counts.increase}只</strong></div><div class="rk-stat"><label>份额减少</label><strong class="rk-down">${counts.decrease}只</strong></div><div class="rk-stat"><label>份额持平</label><strong class="rk-muted">${counts.flat}只</strong></div></section><section class="rk-board-grid">${board('daily-increase', '收盘参考市值增加 TOP 5', '单位：亿元', daily.increase)}${board('daily-decrease', '收盘参考市值减少 TOP 5', '单位：亿元', daily.decrease)}</section><div class="rk-disclaimer">${escapeHtml(meta.disclaimer)} 日期均为有效交易日；缺少当日收盘价的ETF不参与参考市值排名。</div>`;
  }

  function renderWeekly() {
    const { weekly, meta } = state.payload;
    $('rkContent').innerHTML = `<section class="rk-week-stack">${board('weekly-increase', '5日连续增加 TOP 5', `${meta.week_start || '历史不足'} — ${meta.trade_date}`, weekly.continuous_increase, true)}${board('weekly-decrease', '5日累计减少 TOP 5', '单位：亿元', weekly.cumulative_decrease, true)}</section><div class="rk-disclaimer">周榜使用6个完整份额观测点计算5个交易日变化；“连续增加”要求5个日变化全部为正。</div>`;
  }

  function render() {
    if (!state.payload) return;
    $('rkSummary').textContent = headline(state.payload);
    renderAudit(state.payload);
    document.querySelectorAll('.rk-tab').forEach(button => button.classList.toggle('active', button.dataset.rkTab === state.view));
    if (state.view === 'daily') renderDaily(); else renderWeekly();
  }

  async function loadRankings() {
    const requestId = ++state.requestId;
    const selectedDate = $('rkDate').value;
    $('rkContent').innerHTML = '<div class="rk-loading">正在计算龙虎榜…</div>';
    try {
      const response = await fetch(`/api/rankings${selectedDate ? `?date=${encodeURIComponent(selectedDate)}` : ''}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || '龙虎榜加载失败');
      if (requestId !== state.requestId) return;
      state.payload = payload;
      $('rkDate').value = payload.meta.trade_date;
      render();
    } catch (error) {
      if (requestId !== state.requestId) return;
      $('rkSummary').textContent = '龙虎榜暂不可用';
      $('rkAudit').textContent = '';
      $('rkWarnings').hidden = true;
      $('rkContent').innerHTML = `<div class="rk-error">${escapeHtml(error.message || '龙虎榜加载失败')}</div>`;
    }
  }

  function bind() {
    $('analysisModeButton').addEventListener('click', () => setMode('analysis'));
    $('rankingsModeButton').addEventListener('click', () => setMode('rankings'));
    $('rkRefresh').addEventListener('click', () => loadRankings());
    $('rkDate').addEventListener('change', () => loadRankings());
    document.querySelectorAll('.rk-tab').forEach(button => button.addEventListener('click', () => {
      state.view = button.dataset.rkTab;
      render();
    }));
    $('rkContent').addEventListener('click', event => {
      const toggle = event.target.closest('[data-rk-toggle]');
      if (!toggle) return;
      const key = toggle.dataset.rkToggle;
      if (state.expanded.has(key)) state.expanded.delete(key); else state.expanded.add(key);
      render();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind); else bind();
})();
