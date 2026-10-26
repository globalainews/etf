const api=require('../../utils/api'); const fmt=require('../../utils/format');
Page({
 data:{isDemo:api.isDemo,direction:'increase',items:[],page:1,hasMore:false,loading:false,error:'',release:null},
 onLoad(options){if(['increase','decrease'].includes(options.direction))this.setData({direction:options.direction});this.reload();},
 async reload(){this.setData({items:[],page:1,hasMore:false,error:''});await this.loadMore();},
 async loadMore(){if(this.data.loading)return;this.setData({loading:true,error:''});try{const result=await api.call('rankings',{direction:this.data.direction,page:this.data.page,pageSize:20});this.setData({items:this.data.items.concat((result.items||[]).map(fmt.decorate)),hasMore:result.hasMore,page:this.data.page+1,release:result.release});}catch(e){this.setData({error:e.message||'加载失败'});}finally{this.setData({loading:false});}},
 changeDirection(e){this.setData({direction:e.currentTarget.dataset.value});this.reload();},
 openDetail(e){wx.navigateTo({url:`/pages/detail/detail?code=${e.currentTarget.dataset.code}`});}
});

