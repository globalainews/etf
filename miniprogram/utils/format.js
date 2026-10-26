function signed(value, digits = 2, suffix = '') {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '不可计算';
  const number = Number(value);
  return `${number > 0 ? '+' : ''}${number.toFixed(digits)}${suffix}`;
}

function percent(value, digits = 2) { return signed(value === null ? null : Number(value) * 100, digits, '%'); }
function shares(value) { return signed(value === null ? null : Number(value) / 1e8, 2, '亿份'); }
function totalShares(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  return `${(Number(value) / 1e12).toFixed(2)}万亿份`;
}
function tone(value) { return Number(value) > 0 ? 'positive' : Number(value) < 0 ? 'negative' : 'neutral'; }

function decorate(row) {
  return { ...row, deltaText: shares(row.delta_shares), rateText: percent(row.change_rate),
    priceText: percent(row.price_change_rate), tone: tone(row.delta_shares ?? row.change_rate) };
}

module.exports = { signed, percent, shares, totalShares, tone, decorate };

