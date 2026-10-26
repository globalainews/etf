const api=require('../../utils/api'); const fmt=require('../../utils/format'); const chart=require('../../utils/chart');
Page({
 data:{isDemo:api.isDemo,code:'',period:30,loading:true,error:'',detail:null,history:[],selected:null,watched:false,watchBusy:false,user:null},
 onLoad(options){this.setData({code:options.code||''});this.load();},
 async load(){this.setData({loading:true,error:''});try{const detail=await api.call('detail',{code:this.data.code,period:this.data.period});const history=(detail.history||[]).map(item=>this.decoratePoint({...item,shares_yi:item.shares===null?null:item.shares/1e8}));detail.snapshot=fmt.decorate(detail.snapshot||{});detail.anomalyText=detail.anomaly&&detail.anomaly.anomaly_ratio!==null&&detail.anomaly.anomaly_ratio!==undefined?`${Number(detail.anomaly.anomaly_ratio).toFixed(2)}倍`:'不可计算';this.setData({detail,history,selected:history[history.length-1]||null});this.checkWatch();setTimeout(()=>this.draw(),30);}catch(e){this.setData({error:e.message||'加载失败'});}finally{this.setData({loading:false});}},
 decoratePoint(item){return{...item,sharesText:item.shares_yi===null?'缺失':`${Number(item.shares_yi).toFixed(2)}亿份`,closeText:item.close===null||item.close===undefined?'缺失':Number(item.close).toFixed(3)};},
 draw(){chart.drawLine('shareChart',this.data.history,'shares_yi','#0b8f84','亿',this.selectedIndex());chart.drawLine('priceChart',this.data.history,'close','#c89d36','',this.selectedIndex());},
 selectedIndex(){return this.data.selected?this.data.history.findIndex(item=>item.date===this.data.selected.date):-1;},
 async changePeriod(e){const period=Number(e.currentTarget.dataset.period);if(period===this.data.period)return;this.setData({period});await this.load();},
 selectPoint(e){if(!this.data.history.length)return;const x=e.detail.x||0;const index=Math.max(0,Math.min(this.data.history.length-1,Math.round((x-42)/(335-54)*(this.data.history.length-1))));this.setData({selected:this.data.history[index]});this.draw();},
 async checkWatch(){try{const [result,user]=await Promise.all([api.call('watchlist.list'),api.call('user.status')]);this.setData({watched:(result.items||[]).some(item=>item.code===this.data.code),user});}catch(e){}},
 async toggleWatch(){if(this.data.watchBusy)return;const previous=this.data.watched;if(!previous&&(!this.data.user||!this.data.user.membershipActive)){wx.navigateTo({url:'/pages/membership/membership?from=detail'});return;}this.setData({watched:!previous,watchBusy:true});try{await api.call(previous?'watchlist.remove':'watchlist.add',{code:this.data.code});wx.showToast({title:previous?'已移除':'已加入',icon:'success'});}catch(e){this.setData({watched:previous});wx.showToast({title:e.message||'操作失败',icon:'none'});}finally{this.setData({watchBusy:false});}}
});

