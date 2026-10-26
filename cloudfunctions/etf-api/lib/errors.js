class ApiError extends Error {
  constructor(code, message, status = 400, details = undefined) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

const badRequest = (message, details) => new ApiError('INVALID_ARGUMENT', message, 400, details);
const noData = (message = '暂无可用数据') => new ApiError('NO_DATA', message, 404);
const forbidden = (message = '无权执行此操作') => new ApiError('FORBIDDEN', message, 403);

module.exports = { ApiError, badRequest, noData, forbidden };

