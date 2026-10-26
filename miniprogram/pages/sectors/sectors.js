const api=require('../../utils/api'); const fmt=require('../../utils/format');
Page({
 data:{isDemo:api.isDemo,loading:true,error:'',items:[],release:null,expanded:'',samples:[]},
 onLoad(){this.load();},
 async load(){this.setData({loading:true,error:''});try{const result=await api.call('sectors',{pageSize:50});this.setData({items:(result.items||[]).map(item=>({...item,rateText:Number(item.median_change_rate)===0?'中位数持平':fmt.percent(item.median_change_rate),tone:fmt.tone(item.median_change_rate)})),release:result.release});}catch(e){this.setData({error:e.message||'加载失败'});}finally{this.setData({loading:false});}},
 async expand(e){const sector=e.currentTarget.dataset.sector;if(this.data.expanded===sector){this.setData({expanded:'',samples:[]});return;}this.setData({expanded:sector,samples:[]});try{const result=await api.call('sectorSamples',{sector,pageSize:50});this.setData({samples:(result.items||[]).map(fmt.decorate)});}catch(err){wx.showToast({title:err.message||'样本加载失败',icon:'none'});}},
 openDetail(e){wx.navigateTo({url:`/pages/detail/detail?code=${e.currentTarget.dataset.code}`});}
});

