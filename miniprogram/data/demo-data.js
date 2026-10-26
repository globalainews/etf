const funds = [
  { code:'510300', name:'沪深300ETF示例', market:'上海', fund_type:'跨市', sector:'宽基', delta_shares:2760000000, change_rate:.035, price_change_rate:-.004 },
  { code:'588000', name:'科创50ETF示例', market:'上海', fund_type:'跨市', sector:'科技', delta_shares:1380000000, change_rate:.021, price_change_rate:.012 },
  { code:'159915', name:'创业板ETF示例', market:'深圳', fund_type:'', sector:'宽基', delta_shares:920000000, change_rate:.014, price_change_rate:.008 },
  { code:'512880', name:'证券ETF示例', market:'上海', fund_type:'跨市', sector:'金融', delta_shares:-810000000, change_rate:-.018, price_change_rate:-.006 },
  { code:'512010', name:'医药ETF示例', market:'上海', fund_type:'跨市', sector:'医药', delta_shares:-660000000, change_rate:-.012, price_change_rate:.003 },
  { code:'513100', name:'纳指ETF示例', market:'上海', fund_type:'跨境', sector:'跨境/海外', delta_shares:410000000, change_rate:.009, price_change_rate:.016 }
].map((item,index)=>({ ...item, rank:index+1, direction:item.delta_shares>0?'increase':'decrease', shares:30000000000+index*5000000000,
  baseline_shares:29000000000+index*5000000000, statistic_date:'2026-10-23', baseline_date:'2026-10-22', quality_status:'DEMO' }));

function dates(count) {
  const result=[]; const date=new Date('2026-10-23T00:00:00Z');
  while(result.length<count){ const day=date.getUTCDay(); if(day!==0&&day!==6) result.unshift(date.toISOString().slice(0,10)); date.setUTCDate(date.getUTCDate()-1); }
  return result;
}
function history(code, count=90) {
  const fund=funds.find(item=>item.code===code)||funds[0]; const days=dates(count); const seed=Number(code.slice(-2));
  return days.map((date,index)=>({ date, code, shares:fund.baseline_shares+(index-count+1)*fund.delta_shares/8+Math.sin(index+seed)*110000000,
    close:1.15+index*.002+Math.cos(index/3+seed)*.025, share_status:index===18?'MISSING':'OK', price_status:index===31?'MISSING':'OK' }))
    .map(item=>({ ...item, shares:item.share_status==='MISSING'?null:item.shares, close:item.price_status==='MISSING'?null:item.close }));
}

const status={ release_id:'demo-release', release_seq:1, statistic_date:'2026-10-23', baseline_date:'2026-10-22', updated_at:'2026-10-23T18:00:00+08:00',
  coverage:{fund_total:1801,latest_share_count:6,comparable_count:6,latest_price_count:6,classification_count:5,total_shares:255000000000,increase_count:4,decrease_count:2,unchanged_count:0,unavailable_change_count:0}, quality_status:'DEMO',
  disclaimer:'份额变化不代表未来涨跌，不等同资金净流入。', share_unit:'份', display_share_unit:'亿份', price_currency:'CNY', price_adjustment:'前复权', is_stale:false };
const streaks=funds.map((item,index)=>({ ...item, days:2+(index%5), start_date:'2026-10-16', end_date:'2026-10-23', cumulative_change_rate:item.change_rate*(2+index%5), status:'OK' }));
const anomalies=funds.map((item,index)=>({ ...item, anomaly_ratio:5.8-index*.6, history_median_abs_rate:.004, qualified:index<4, status:'OK',
  rule:{window:20,min_history:20,ratio_threshold:3,abs_pct_threshold:1,excludes_current:true} }));
const sectors=['宽基','科技','金融','医药','跨境/海外'].map((sector,index)=>({ sector,median_change_rate:[.018,.012,-.009,-.006,.005][index],valid_sample_count:20-index*2,missing_sample_count:index%3,sample_count:22-index }));

module.exports={ funds,status,history,streaks,anomalies,sectors };

