const api=require('../../utils/api'); const fmt=require('../../utils/format');
Page({data:{isDemo:api.isDemo,direction:'increase',items:[],release:null,rule:'',loading:true,error:''},onLoad(){this.load();},
 async load(){this.setData({loading:true,error:''});try{const result=await api.call('streaks',{direction:this.data.direction,pageSize:50});this.setData({items:(result.items||[]).map(item=>({...item,cumulativeText:fmt.percent(item.cumulative_change_rate),tone:item.direction==='increase'?'positive':'negative'})),release:result.release,rule:result.rule});}catch(e){this.setData({error:e.message||'加载失败'});}finally{this.setData({loading:false});}},
 change(e){this.setData({direction:e.currentTarget.dataset.value});this.load();},openDetail(e){wx.navigateTo({url:`/pages/detail/detail?code=${e.currentTarget.dataset.code}`});}
});

