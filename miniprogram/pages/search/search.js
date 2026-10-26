const api=require('../../utils/api');
Page({
 data:{isDemo:api.isDemo,keyword:'',loading:false,searched:false,error:'',note:'',items:[],watchCodes:[]},timer:null,
 onShow(){this.loadWatch();},onUnload(){if(this.timer)clearTimeout(this.timer);},
 async loadWatch(){try{const result=await api.call('watchlist.list');const watchCodes=(result.items||[]).map(i=>i.code);this.setData({watchCodes,items:this.data.items.map(item=>({...item,watched:watchCodes.indexOf(item.code)>=0}))});}catch(e){}},
 onInput(e){const keyword=e.detail.value.slice(0,24);this.setData({keyword,error:''});if(this.timer)clearTimeout(this.timer);if(!keyword.trim()){this.setData({items:[],searched:false,note:''});return;}this.timer=setTimeout(()=>this.search(),350);},
 async search(){const keyword=this.data.keyword.trim();if(!keyword)return;this.setData({loading:true,error:'',searched:true});try{const result=await api.call('search',{keyword,pageSize:20});this.setData({items:(result.items||[]).map(item=>({...item,watched:this.data.watchCodes.indexOf(item.code)>=0})),note:result.note||''});}catch(e){this.setData({error:e.message||'搜索失败',items:[]});}finally{this.setData({loading:false});}},
 openDetail(e){wx.navigateTo({url:`/pages/detail/detail?code=${e.currentTarget.dataset.code}`});},
 async add(e){const code=e.currentTarget.dataset.code;if(this.data.watchCodes.indexOf(code)>=0)return;const old=this.data.watchCodes;this.setData({watchCodes:[...old,code],items:this.data.items.map(item=>item.code===code?{...item,watched:true}:item)});try{await api.call('watchlist.add',{code});wx.showToast({title:'已加入自选',icon:'success'});}catch(err){this.setData({watchCodes:old,items:this.data.items.map(item=>item.code===code?{...item,watched:false}:item)});wx.showToast({title:err.message||'添加失败',icon:'none'});}}
});

