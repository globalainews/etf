const env = require('../config/env');
const demo = require('../data/demo-data');

const wait = (value) => new Promise(resolve => setTimeout(() => resolve(value), 120));
const localKey = 'etf-demo-watchlist-v1';
const readWatch = () => wx.getStorageSync(localKey) || [];
const writeWatch = (items) => wx.setStorageSync(localKey, items);

function paginate(items, page=1, pageSize=20) {
  const start=(page-1)*pageSize; return {items:items.slice(start,start+pageSize),page,pageSize,hasMore:start+pageSize<items.length};
}
function demoCall(action,args){
  if(action==='status') return wait(demo.status);
  if(action==='rankings'){
    const direction=args.direction||'increase';
    const items=demo.funds.filter(i=>i.direction===direction).sort((a,b)=>direction==='increase'?b.delta_shares-a.delta_shares:a.delta_shares-b.delta_shares).slice(0,100);
    return wait({...paginate(items,args.page,args.pageSize),release:demo.status});
  }
  if(action==='search'){
    const keyword=String(args.keyword||'').trim().toLowerCase(); const items=demo.funds.filter(i=>i.code.startsWith(keyword)||i.name.toLowerCase().startsWith(keyword));
    return wait({items,keyword,release:demo.status,note:items.length?'':'演示数据中没有匹配记录；这不代表市场上不存在该ETF。'});
  }
  if(action==='detail'){
    const master=demo.funds.find(i=>i.code===args.code); if(!master) throw new Error('演示数据中没有该ETF');
    const all=demo.history(args.code,90); const period=Number(args.period||30); const hist=all.slice(-period);
    return wait({master,snapshot:master,history:hist,streak:demo.streaks.find(i=>i.code===args.code),anomaly:demo.anomalies.find(i=>i.code===args.code),requestedPeriod:period,availablePoints:hist.length,release:demo.status});
  }
  if(action==='streaks'){ const items=demo.streaks.filter(i=>i.direction===(args.direction||'increase')); return wait({...paginate(items,args.page,args.pageSize),release:demo.status,rule:'仅计算当日份额超过100亿份的ETF；份额不变、缺失或未达规模门槛均中断。'}); }
  if(action==='anomalies') return wait({...paginate(demo.anomalies.filter(i=>i.qualified),args.page,args.pageSize),release:demo.status,rule:{window:20,ratio_threshold:3,abs_pct_threshold:1,min_shares:10000000000},disclaimer:demo.status.disclaimer});
  if(action==='sectors') return wait({...paginate(demo.sectors,args.page,args.pageSize),release:demo.status});
  if(action==='sectorSamples'){ const items=demo.funds.filter(i=>i.sector===args.sector); return wait({...paginate(items,args.page,args.pageSize),release:demo.status,sector:args.sector}); }
  if(action==='watchlist.list'){ const codes=readWatch(); return wait({items:codes.map(code=>({code,snapshot:demo.funds.find(i=>i.code===code)})).filter(i=>i.snapshot),release:demo.status}); }
  if(action==='watchlist.add'){ const items=readWatch(); if(!items.includes(args.code)) writeWatch([args.code,...items]); return wait({code:args.code,added:true}); }
  if(action==='watchlist.remove'){ writeWatch(readWatch().filter(code=>code!==args.code)); return wait({code:args.code,removed:true}); }
  if(action==='user.status') return wait({tier:'member',membershipActive:true,applicationStatus:'active',memberStartDate:'2026-01-01',memberEndDate:'2099-12-31',source:'demo',plansOpen:false});
  if(action==='member.apply') return wait({tier:'member',membershipActive:true,applicationStatus:'active',source:'demo',submitted:false,message:'演示模式默认已开放会员功能，不会提交真实申请。'});
  throw new Error('未知演示操作');
}

async function call(action,args={}){
  if(env.mode==='demo') return demoCall(action,args);
  if(env.mode!=='real') throw new Error('运行模式配置无效');
  if(!env.cloudEnvId) throw new Error('real模式尚未配置云环境ID');
  const response=await wx.cloud.callFunction({name:env.cloudFunctionName,data:{action,...args},config:{env:env.cloudEnvId}});
  const result=response.result;
  if(!result||!result.ok){ const error=new Error(result&&result.error?result.error.message:'云函数返回异常'); error.code=result&&result.error&&result.error.code; throw error; }
  return result.data;
}

module.exports={call,isDemo:env.mode==='demo'};

