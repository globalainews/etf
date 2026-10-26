const { badRequest } = require('./errors');

const CODE = /^\d{6}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const ACTIONS = new Set([
  'status', 'rankings', 'search', 'detail', 'streaks', 'anomalies', 'sectors',
  'sectorSamples', 'watchlist.list', 'watchlist.add', 'watchlist.remove', 'user.status', 'member.apply'
]);

function action(value) {
  if (typeof value !== 'string' || !ACTIONS.has(value)) throw badRequest('未知操作');
  return value;
}

function code(value) {
  const result = String(value || '');
  if (!CODE.test(result)) throw badRequest('ETF代码必须为6位数字');
  return result;
}

function date(value, optional = true) {
  if ((value === undefined || value === '') && optional) return undefined;
  if (typeof value !== 'string' || !DATE.test(value)) throw badRequest('日期格式须为 YYYY-MM-DD');
  return value;
}

function keyword(value) {
  const result = String(value || '').trim().toLowerCase().replace(/\s+/g, '');
  if (!result) throw badRequest('请输入代码或名称');
  if (result.length > 24) throw badRequest('搜索词不能超过24个字符');
  if (!/^[0-9a-z\u3400-\u9fff]+$/i.test(result)) throw badRequest('搜索词包含不支持的字符');
  return result;
}

function page(value) {
  const number = Number(value || 1);
  if (!Number.isInteger(number) || number < 1 || number > 1000) throw badRequest('页码无效');
  return number;
}

function pageSize(value) {
  const number = Number(value || 20);
  if (!Number.isInteger(number) || number < 1 || number > 50) throw badRequest('每页数量须为1到50');
  return number;
}

function period(value) {
  const number = Number(value || 30);
  if (![7, 30, 90].includes(number)) throw badRequest('趋势周期仅支持7、30、90个交易日');
  return number;
}

function oneOf(value, allowed, fallback, label) {
  const result = value === undefined ? fallback : value;
  if (!allowed.includes(result)) throw badRequest(`${label}无效`);
  return result;
}

function memberName(value, label, maxLength) {
  const result = String(value || '').trim();
  if (!result) throw badRequest(`请填写${label}`);
  if (result.length > maxLength) throw badRequest(`${label}不能超过${maxLength}个字符`);
  if (/[\u0000-\u001f\u007f]/.test(result)) throw badRequest(`${label}包含不支持的字符`);
  return result;
}

module.exports = { action, code, date, keyword, page, pageSize, period, oneOf, memberName };

