const api=require('../../utils/api');
Page({
 data:{isDemo:api.isDemo,loading:true,error:'',user:null,showHelp:false},
 onShow(){this.load();},
 async load(){this.setData({loading:true,error:''});try{this.setData({user:await api.call('user.status')});}catch(e){this.setData({error:e.message||'加载失败'});}finally{this.setData({loading:false});}},
 openMembership(){wx.navigateTo({url:'/pages/membership/membership?from=profile'});},toggleHelp(){this.setData({showHelp:!this.data.showHelp});}
});

