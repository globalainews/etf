from __future__ import annotations

from html import escape


COMMON_CSS = r"""
:root{--bg:#06101d;--bg-deep:#030913;--panel:#0b1b2d;--panel-strong:#10263d;--brand:#4f8cff;--share-up:#2fd3c5;--share-down:#f2b84b;--price-up:#ef5b67;--price-down:#39b77a;--text:#f5f8fc;--muted:#9aabc0;--subtle:#6f829a;--line:rgba(163,190,222,.18);--safe-x:72px}
*{box-sizing:border-box}html,body{margin:0;width:1080px;height:1920px;overflow:hidden;background:var(--bg-deep);color:var(--text)}
body{font-family:"Microsoft YaHei UI","Microsoft YaHei","PingFang SC",Arial,sans-serif;font-variant-numeric:tabular-nums}.page{position:relative;width:1080px;height:1920px;padding:112px var(--safe-x) 206px;overflow:hidden;background:linear-gradient(155deg,#0a1b2d 0%,var(--bg) 46%,var(--bg-deep) 100%)}
.page:before{content:"";position:absolute;inset:0;background-image:linear-gradient(rgba(157,184,216,.026) 1px,transparent 1px),linear-gradient(90deg,rgba(157,184,216,.026) 1px,transparent 1px);background-size:64px 64px;pointer-events:none}.page:after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(79,140,255,.08),transparent 24%,transparent 78%,rgba(3,9,19,.36));pointer-events:none}
.content{position:relative;height:1602px;z-index:1}.masthead{display:flex;align-items:center;justify-content:space-between;min-height:48px}.brand-lockup{display:flex;align-items:center;gap:18px}.brand{font-size:25px;letter-spacing:6px;color:#8bb1ff;font-weight:800}.column-badge{padding:7px 13px;border:1px solid rgba(79,140,255,.42);font-size:22px;letter-spacing:1px;color:#c8d9f7;background:rgba(79,140,255,.08)}.date{font-size:28px;color:var(--muted);letter-spacing:1px}.rule{width:100%;height:1px;margin:24px 0 34px;background:linear-gradient(90deg,rgba(79,140,255,.8),var(--line) 52%,transparent)}
h1{font-size:76px;line-height:1.12;margin:0;font-weight:850;letter-spacing:-2px;white-space:nowrap}.eyebrow{margin-bottom:18px;font-size:25px;color:#7fa4db;letter-spacing:5px;font-weight:700}.increase{color:var(--share-up)}.decrease{color:var(--share-down)}.section-note{margin-top:18px;display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:25px}.legend{display:flex;gap:22px}.legend span:before{content:"";display:inline-block;width:12px;height:12px;margin-right:9px;background:currentColor}
.cover-layout{height:1338px;display:grid;grid-template-rows:auto 1fr auto;gap:42px}.cover-title{font-size:88px}.cover-lead{align-self:center}.cover-kicker{font-size:28px;letter-spacing:3px;color:var(--share-up);font-weight:750}.cover-insight{max-width:900px;margin-top:24px;font-size:58px;line-height:1.34;font-weight:820;letter-spacing:-1px}.cover-insight strong{color:var(--text);text-decoration:underline;text-decoration-color:rgba(47,211,197,.45);text-underline-offset:12px}.cover-value{display:flex;align-items:baseline;gap:16px;margin-top:34px}.cover-value b{font-size:86px;line-height:1;font-weight:900}.cover-value span{font-size:27px;color:var(--muted)}
.cover-pair{display:grid;grid-template-columns:1fr 1fr;gap:22px}.cover-card{min-height:244px;padding:28px 28px 24px;border-top:3px solid var(--line);background:linear-gradient(180deg,rgba(16,38,61,.88),rgba(8,23,39,.78))}.cover-card.up{border-color:var(--share-up)}.cover-card.down{border-color:var(--share-down)}.cover-card-label{font-size:25px;color:var(--muted)}.cover-card strong{display:block;margin-top:14px;font-size:37px;line-height:1.22;min-height:90px}.cover-card b{display:block;margin-top:12px;font-size:47px;white-space:nowrap}.cover-card small{display:block;margin-top:8px;font-size:22px;color:var(--subtle)}
.rank-list{display:flex;flex-direction:column;gap:14px;margin-top:30px}.rank-row{position:relative;height:218px;display:grid;grid-template-columns:86px minmax(0,1fr) 252px;grid-template-rows:1fr 42px;column-gap:18px;align-items:center;padding:23px 27px 20px;border:1px solid var(--line);background:linear-gradient(90deg,rgba(16,38,61,.93),rgba(7,20,35,.88));animation:rise .62s ease both}.rank-row:nth-child(2){animation-delay:.06s}.rank-row:nth-child(3){animation-delay:.12s}.rank-row:nth-child(4){animation-delay:.18s}.rank-row:nth-child(5){animation-delay:.24s}.rank-no{grid-row:1;font-size:58px;font-weight:900;color:#6f87a4}.rank-main{grid-row:1;min-width:0}.rank-name{font-size:var(--name-size,40px);line-height:1.14;font-weight:790;overflow-wrap:anywhere}.rank-code{display:block;margin-top:8px;font-size:23px;letter-spacing:2px;color:#8196b0}.rank-side{grid-row:1;text-align:right;align-self:center}.rank-value{font-size:47px;line-height:1.05;font-weight:900;white-space:nowrap}.rank-pct{margin-top:10px;font-size:23px;color:var(--muted);white-space:nowrap}.bar-track{grid-column:2/4;grid-row:2;align-self:end;height:9px;background:rgba(150,178,210,.12);overflow:hidden}.bar-fill{display:block;height:100%;width:var(--bar);background:var(--share-up);transform-origin:left center;animation:grow .72s ease both}.rank-list.decrease-list .bar-fill{background:var(--share-down)}.rank-row:first-child{border-color:rgba(184,205,231,.36);background:linear-gradient(90deg,rgba(20,51,78,.98),rgba(9,26,43,.92))}.rank-row:first-child .rank-no{color:#f7d27d}.rank-row:first-child:before{content:"榜首";position:absolute;right:24px;top:15px;font-size:20px;letter-spacing:2px;color:#c6d4e5}
.close-layout{height:1338px;display:grid;grid-template-rows:auto auto 1fr auto;gap:38px}.close-finding{padding:36px 0 26px;border-bottom:1px solid var(--line);font-size:48px;line-height:1.42;font-weight:800}.close-finding strong{color:var(--share-up)}.compare{align-self:center;display:grid;gap:30px}.compare-row{display:grid;grid-template-columns:1fr 270px;gap:24px;align-items:end}.compare-label{font-size:26px;color:var(--muted)}.compare-name{display:block;margin-top:11px;font-size:40px;line-height:1.25}.compare-value{text-align:right;font-size:54px;font-weight:900;white-space:nowrap}.compare-track{grid-column:1/3;height:16px;background:rgba(150,178,210,.12)}.compare-fill{display:block;height:100%;width:var(--bar);background:var(--share-up)}.compare-row.down .compare-fill{background:var(--share-down)}
.method-box{padding:28px 30px;background:rgba(10,28,47,.82);border-left:4px solid var(--brand)}.method-box strong{font-size:27px}.method-box p{margin:12px 0 0;font-size:25px;line-height:1.55;color:#b8c5d5}.disclaimer{padding-top:28px;border-top:1px solid rgba(79,140,255,.38);font-size:31px;line-height:1.55;color:#d3dbe7}.footer{position:absolute;left:var(--safe-x);right:var(--safe-x);bottom:78px;display:flex;justify-content:space-between;align-items:center;font-size:23px;color:var(--subtle);z-index:2}.footer strong{color:#9cb8dd;font-weight:700}.footer-source{text-align:right}
@keyframes rise{from{opacity:.72;transform:translateY(18px)}to{opacity:1;transform:none}}@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}html[data-render-mode="render"] *,html[data-render-mode="render"] *:before,html[data-render-mode="render"] *:after{animation:none!important;transition:none!important}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


def _pct(value: float | None) -> str:
    return "变化率不可计算" if value is None else f"较前日 {value:+.2f}%"


def _shell(title: str, trade_date: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=1080,initial-scale=1"><title>{escape(title)}</title><style>{COMMON_CSS}</style></head>
<body><main class="page"><section class="content" data-layout-root><header class="masthead"><div class="brand-lockup"><span class="brand">GLOBAL 速览</span><span class="column-badge">自制份额统计榜</span></div><time class="date">{escape(trade_date)}</time></header><div class="rule"></div>{body}</section><footer class="footer"><strong>ETF份额龙虎榜</strong><span class="footer-source">上交所 / 深交所 · 相邻交易日份额</span></footer></main>
<script>(()=>{{const root=document.documentElement;const params=new URLSearchParams(location.search);root.dataset.renderMode=params.get("mode")==="render"?"render":"preview";const images=Array.from(document.images).map(img=>img.complete?Promise.resolve():new Promise(resolve=>{{img.addEventListener("load",resolve,{{once:true}});img.addEventListener("error",resolve,{{once:true}})}}));Promise.all([document.fonts.ready].concat(images)).then(()=>requestAnimationFrame(()=>requestAnimationFrame(()=>{{root.dataset.renderReady="true";}})));}})();</script></body></html>"""


def _rows(items: list[dict], direction: str) -> str:
    cls = "increase" if direction == "增加" else "decrease"
    maximum = max(abs(float(item["share_change"])) for item in items) or 1
    rendered = []
    for item in items:
        width = max(8.0, abs(float(item["share_change"])) / maximum * 100)
        name_size = 40 if len(item["fund_name"]) <= 16 else 34 if len(item["fund_name"]) <= 24 else 28
        rendered.append(
            f'<article class="rank-row" data-critical style="--bar:{width:.2f}%;--name-size:{name_size}px"><div class="rank-no">{item["rank"]}</div>'
            f'<div class="rank-main"><div class="rank-name">{escape(item["fund_name"])}<span class="rank-code">{escape(item["fund_code"])}</span></div></div>'
            f'<div class="rank-side"><div class="rank-value {cls}">{escape(item["display_value"])}</div><div class="rank-pct">{escape(_pct(item.get("share_change_pct")))}</div></div>'
            '<div class="bar-track" aria-hidden="true"><span class="bar-fill"></span></div></article>'
        )
    return "".join(rendered)


def render_pages(payload: dict) -> dict[str, str]:
    trade_date = payload["trade_date"]
    inc, dec = payload["rankings"]["increase"], payload["rankings"]["decrease"]
    inc_top, dec_top = inc[0], dec[0]
    leader_gap = abs(float(inc_top["share_change"])) - abs(float(dec_top["share_change"]))
    gap_display = f"{leader_gap / 100_000_000:.2f}".rstrip("0").rstrip(".") + "亿份"
    dec_ratio = abs(float(dec_top["share_change"])) / abs(float(inc_top["share_change"])) * 100
    cover = _shell("ETF份额龙虎榜", trade_date,
        '<div class="cover-layout" data-critical><div><div class="eyebrow">ETF SHARE MONITOR</div><h1 class="cover-title">ETF份额龙虎榜</h1></div>'
        f'<div class="cover-lead"><div class="cover-kicker">今日份额变化焦点</div><div class="cover-insight"><strong>{escape(inc_top["fund_name"])}</strong>单日份额增加居首</div><div class="cover-value"><b class="increase">{escape(inc_top["display_value"])}</b><span>较前一交易日</span></div></div>'
        f'<div class="cover-pair"><div class="cover-card up"><div class="cover-card-label">增加榜首</div><strong>{escape(inc_top["fund_name"])}</strong><b class="increase">{escape(inc_top["display_value"])}</b><small>{escape(inc_top["fund_code"])}</small></div><div class="cover-card down"><div class="cover-card-label">减少榜首</div><strong>{escape(dec_top["fund_name"])}</strong><b class="decrease">{escape(dec_top["display_value"])}</b><small>{escape(dec_top["fund_code"])}</small></div></div></div>')
    increase = _shell("份额增加 TOP5", trade_date,
        '<div class="eyebrow">SHARE INCREASE</div><h1>份额增加 <span class="increase">TOP5</span></h1><div class="section-note"><span>按份额变化绝对值排序</span><span class="legend"><span class="increase">份额增加</span></span></div>'
        f'<div class="rank-list">{_rows(inc, "增加")}</div>')
    decrease = _shell("份额减少 TOP5", trade_date,
        '<div class="eyebrow">SHARE DECREASE</div><h1>份额减少 <span class="decrease">TOP5</span></h1><div class="section-note"><span>按份额变化绝对值排序</span><span class="legend"><span class="decrease">份额减少</span></span></div>'
        f'<div class="rank-list decrease-list">{_rows(dec, "减少")}</div>')
    close = _shell("ETF份额龙虎榜 · 今日结论", trade_date,
        '<div class="close-layout" data-critical><div><div class="eyebrow">DAILY TAKEAWAY</div><h1>今日数据结论</h1></div>'
        f'<div class="close-finding">增加榜首的变化量，比减少榜首的绝对变化量高 <strong>{gap_display}</strong></div>'
        f'<div class="compare"><div class="compare-row"><div><span class="compare-label">增加榜首</span><strong class="compare-name">{escape(inc_top["fund_name"])}</strong></div><b class="compare-value increase">{escape(inc_top["display_value"])}</b><div class="compare-track"><span class="compare-fill" style="--bar:100%"></span></div></div><div class="compare-row down"><div><span class="compare-label">减少榜首</span><strong class="compare-name">{escape(dec_top["fund_name"])}</strong></div><b class="compare-value decrease">{escape(dec_top["display_value"])}</b><div class="compare-track"><span class="compare-fill" style="--bar:{dec_ratio:.2f}%"></span></div></div></div>'
        '<div><div class="method-box"><strong>统计口径</strong><p>当日基金份额 − 前一有效交易日基金份额；排名按变化绝对值。</p></div><div class="disclaimer">份额变化不等于资金净流入，也不代表价格涨跌。<br>公开数据仅供参考，不构成投资建议。</div></div></div>')
    return {"page_01_cover.html":cover,"page_02_increase.html":increase,"page_03_decrease.html":decrease,"page_04_close.html":close}
