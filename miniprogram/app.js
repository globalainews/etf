const env = require('./config/env');

App({
  onLaunch() {
    if (env.mode === 'real') {
      if (!env.cloudEnvId) {
        console.error('real mode requires miniprogram/config/env.js cloudEnvId');
        return;
      }
      if (!wx.cloud) {
        console.error('当前基础库不支持云开发');
        return;
      }
      wx.cloud.init({ env: env.cloudEnvId, traceUser: false });
    }
  },
  globalData: { mode: env.mode }
});

