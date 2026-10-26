Page({
 data:{plans:[{name:'基础功能',tag:'当前可用',items:['日榜与基础查询','30/90日趋势查看','个人自选与站内观察标记']},{name:'进阶方案预览',tag:'暂未开放',items:['更长历史区间','高级筛选与导出','更多个性化观察工具']}]},
 close(){const pages=getCurrentPages();if(pages.length>1)wx.navigateBack();else wx.switchTab({url:'/pages/profile/profile'});},continue(){wx.switchTab({url:'/pages/index/index'});}
});

