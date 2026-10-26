const test = require('node:test');
const assert = require('node:assert/strict');
const v = require('../lib/validate');
const { watchId } = require('../lib/service');

test('caps page size and rejects invalid inputs', () => {
  assert.equal(v.pageSize(50), 50);
  assert.throws(() => v.pageSize(51), /1到50/);
  assert.throws(() => v.code('51030'), /6位/);
  assert.throws(() => v.keyword('.*'), /不支持/);
  assert.throws(() => v.action('user.update'), /未知操作/);
});

test('watch ids isolate users and stay idempotent', () => {
  assert.equal(watchId('u1', '510300'), watchId('u1', '510300'));
  assert.notEqual(watchId('u1', '510300'), watchId('u2', '510300'));
});

