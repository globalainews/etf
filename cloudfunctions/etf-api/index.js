const cloud = require('wx-server-sdk');
const { ApiError } = require('./lib/errors');
const validate = require('./lib/validate');
const { createService } = require('./lib/service');

cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();
const service = createService(db, () => cloud.getWXContext());

exports.main = async (event = {}) => {
  const requestId = String(event.requestId || '').slice(0, 64) || undefined;
  try {
    const action = validate.action(event.action);
    const data = await service[action](event);
    return { ok: true, data, error: null, meta: { requestId } };
  } catch (error) {
    if (error instanceof ApiError) {
      return { ok: false, data: null, error: { code: error.code, message: error.message, details: error.details }, meta: { requestId } };
    }
    console.error('etf-api failure', { name: error && error.name, code: error && error.code, requestId });
    return { ok: false, data: null, error: { code: 'SERVICE_ERROR', message: '服务暂时不可用，请稍后重试' }, meta: { requestId } };
  }
};

