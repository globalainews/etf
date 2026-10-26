const api=require('../../utils/api'); const fmt=require('../../utils/format');
Page({data:{isDemo:api.isDemo,loading:true,error:'',items:[],release:null,rule:null,disclaimer:''},onLoad(){this.load();},
 async load(){this.setData({loading:true,error:''});try{const result=await api.call('anomalies',{pageSize:50});const items=(result.items||[]).map(item=>({...item,rateText:fmt.percent(item.change_rate),priceText:fmt.percent(item.price_change_rate),baselineText:fmt.percent(item.history_median_abs_rate),tone:fmt.tone(item.change_rate),ratioText:item.anomaly_ratio===null?'不可计算':Number(item.anomaly_ratio).toFixed(1)+'倍',barWidth:Math.min(100,Number(item.anomaly_ratio||0)*12)}));const rule={window:20,ratio_threshold:3,abs_pct_threshold:1,min_shares:10000000000,...(result.rule||items[0]&&items[0].rule||{})};rule.minSharesText=(Number(rule.min_shares)/1e8).toFixed(0)+'亿份';this.setData({items,release:result.release,rule,disclaimer:result.disclaimer});}catch(e){this.setData({error:e.message||'加载失败'});}finally{this.setData({loading:false});}},
 openDetail(e){wx.navigateTo({url:`/pages/detail/detail?code=${e.currentTarget.dataset.code}`});}
});

