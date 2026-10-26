const api=require('../../utils/api'); const fmt=require('../../utils/format');
Page({
 data:{isDemo:api.isDemo,loading:true,error:'',status:null,tab:'increase',items:[],tabs:[['increase','增加'],['decrease','减少'],['streak','连续'],['anomaly','异动']]},
 onShow(){this.load();},
 async load(){this.setData({loading:true,error:''});try{const status=await api.call('status');this.setData({status});await this.loadList(this.data.tab);}catch(e){this.setData({error:e.message||'加载失败'});}finally{this.setData({loading:false});}},
 async loadList(tab){let result;if(tab==='streak')result=await api.call('streaks',{direction:'increase',pageSize:5});else if(tab==='anomaly')result=await api.call('anomalies',{pageSize:5});else result=await api.call('rankings',{direction:tab,pageSize:5});this.setData({items:(result.items||[]).map(row=>{const item=fmt.decorate(row);return{...item,mainText:tab==='streak'?`${item.days}日`:tab==='anomaly'?(item.anomaly_ratio===null?'不可计算':`${Number(item.anomaly_ratio).toFixed(1)}倍`):item.deltaText};})});},
 async changeTab(e){const tab=e.currentTarget.dataset.tab;this.setData({tab,loading:true,error:''});try{await this.loadList(tab);}catch(err){this.setData({error:err.message||'加载失败'});}finally{this.setData({loading:false});}},
 openDetail(e){wx.navigateTo({url:`/pages/detail/detail?code=${e.currentTarget.dataset.code}`});},
 openFull(){const tab=this.data.tab;const url=tab==='streak'?'/pages/streaks/streaks':tab==='anomaly'?'/pages/anomalies/anomalies':`/pages/ranking/ranking?direction=${tab}`;wx.navigateTo({url});},
 openSectors(){wx.navigateTo({url:'/pages/sectors/sectors'});}
});

