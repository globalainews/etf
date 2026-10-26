const test = require('node:test');
const assert = require('node:assert/strict');
const v = require('../lib/validate');
const { watchId, createService, userId } = require('../lib/service');

test('caps page size and rejects invalid inputs', () => {
  assert.equal(v.pageSize(50), 50);
  assert.throws(() => v.pageSize(51), /1到50/);
  assert.throws(() => v.code('51030'), /6位/);
  assert.throws(() => v.keyword('.*'), /不支持/);
  assert.throws(() => v.action('user.update'), /未知操作/);
  assert.throws(() => v.memberName('', '昵称', 32), /请填写/);
  assert.throws(() => v.memberName('x'.repeat(33), '昵称', 32), /不能超过/);
});

function fakeDb(seed = {}) {
  const data = Object.fromEntries(Object.entries(seed).map(([name, rows]) => [name, new Map(rows.map(row => [row._id, { ...row }]))]));
  const rows = name => [...(data[name] || new Map()).values()];
  const matcher = (row, where) => Object.entries(where).every(([key, value]) => row[key] === value);
  const collection = name => ({
    where(where) {
      const chain = {
        limit() { return chain; }, skip() { return chain; }, orderBy() { return chain; },
        get: async () => ({ data: rows(name).filter(row => matcher(row, where)) })
      };
      return chain;
    },
    doc(id) { return { set: async ({ data: value }) => { if (!data[name]) data[name] = new Map(); data[name].set(id, { ...value }); } }; }
  });
  return { collection, command: {}, serverDate: () => 'SERVER_DATE', data };
}

test('member application never trusts client membership fields and search is server-gated', async () => {
  const db = fakeDb();
  const service = createService(db, () => ({ OPENID: 'open-a' }));
  await assert.rejects(() => service.search({ keyword: '510300' }), error => error.code === 'MEMBERSHIP_REQUIRED');
  const result = await service['member.apply']({ nickname: '测试用户', videoMemberName: '视频号名称', is_member: true, status: 'active' });
  assert.equal(result.applicationStatus, 'pending');
  const stored = db.data.user_members.get(userId('open-a'));
  assert.equal(stored.is_member, false);
  assert.equal(stored.status, 'pending');
  assert.equal(stored.openid, 'open-a');
});

test('watch ids isolate users and stay idempotent', () => {
  assert.equal(watchId('u1', '510300'), watchId('u1', '510300'));
  assert.notEqual(watchId('u1', '510300'), watchId('u2', '510300'));
});

