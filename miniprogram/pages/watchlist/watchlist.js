const api=require('../../utils/api'); const fmt=require('../../utils/format');
Page({
 data:{isDemo:api.isDemo,loading:true,error:'',items:[],release:null},
 onShow(){this.load();},
 async load(){this.setData({loading:true,error:''});try{const result=await api.call('watchlist.list');this.setData({items:(result.items||[]).map(item=>({...item,snapshot:fmt.decorate(item.snapshot||{})})),release:result.release});}catch(e){this.setData({error:e.message||'加载失败'});}finally{this.setData({loading:false});}},
 openSearch(){wx.switchTab({url:'/pages/search/search'});},openDetail(e){wx.navigateTo({url:`/pages/detail/detail?code=${e.currentTarget.dataset.code}`});},
 async remove(e){const code=e.currentTarget.dataset.code;const old=this.data.items;this.setData({items:old.filter(item=>item.code!==code)});try{await api.call('watchlist.remove',{code});wx.showToast({title:'已移除',icon:'success'});}catch(err){this.setData({items:old});wx.showToast({title:err.message||'移除失败，已回滚',icon:'none'});}},
 async mark(e){const code=e.currentTarget.dataset.code;try{await api.call('watchlist.add',{code,observation:true});this.setData({items:this.data.items.map(item=>item.code===code?{...item,observation:true}:item)});wx.showToast({title:'已标记观察',icon:'success'});}catch(err){wx.showToast({title:err.message||'标记失败',icon:'none'});}}
});

