from __future__ import annotations

from datetime import date

from . import CONTENT_TYPE, SCHEMA_VERSION
from .data import Snapshot
from .utils import chinese_date, iso_now, spoken_name, spoken_share_change


DISCLAIMER = "数据仅供参考，不构成投资建议。"
METHODOLOGY = "按相邻有效交易日基金份额变化的绝对值排序；份额变化不等于资金净流入。"


def _ranking_copy(items: list[dict], direction: str) -> tuple[str, list[str]]:
    prefix = "先看份额增加榜。" if direction == "增加" else "再看份额减少榜。"
    spoken = [prefix]
    subtitles = [prefix.rstrip("。")]
    for item in items:
        spoken.append(f"第{item['rank']}名{spoken_name(item['fund_name'])}。")
        subtitles.append(f"第{item['rank']}名{item['fund_name']}")
    return "".join(spoken), subtitles


def build_payload(snapshot: Snapshot, package_id: str, publish_date: str, generated_at: str | None = None) -> dict:
    trade = date.fromisoformat(snapshot.trade_date)
    inc_top = snapshot.increase[0]
    dec_top = snapshot.decrease[0]
    inc_tts, inc_subtitles = _ranking_copy(snapshot.increase, "增加")
    dec_tts, dec_subtitles = _ranking_copy(snapshot.decrease, "减少")
    leader_gap = abs(float(inc_top["share_change"])) - abs(float(dec_top["share_change"]))
    cover_tts = (
        f"{chinese_date(trade)}ETF份额龙虎榜。"
        f"{spoken_name(inc_top['fund_name'])}单日份额增加{spoken_share_change(inc_top['share_change'])}，位居增加榜首。"
        f"减少榜首是{spoken_name(dec_top['fund_name'])}。"
    )
    close_tts = (
        f"今日增加榜首的变化量，比减少榜首的绝对变化量高{spoken_share_change(leader_gap)}。"
        "份额变化不等于资金净流入，也不代表价格涨跌。公开数据仅供参考，不构成投资建议。"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "content_type": CONTENT_TYPE,
        "package_id": package_id,
        "trade_date": snapshot.trade_date,
        "publish_date": publish_date,
        "timezone": "Asia/Shanghai",
        "generated_at": generated_at or iso_now(),
        "title": "ETF龙虎榜",
        "metric": {"name": "share_change", "unit": "share", "period": "1d"},
        "source": {
            "provider": "上交所/深交所，经本地ETF数据库",
            "dataset": "fund_scale相邻有效交易日份额快照",
            "as_of": generated_at or iso_now(),
            "license_note": "上游由本地AKShare更新程序采集交易所公开数据。",
        },
        "rankings": {"increase": snapshot.increase, "decrease": snapshot.decrease},
        "pages": [
            {
                "sequence": 1,
                "page_id": "cover",
                "page_type": "cover",
                "html_file": "page_01_cover.html",
                "headline": f"{inc_top['fund_name']}单日份额增加居首",
                "tts_text": cover_tts,
                "subtitle_sentences": [
                    f"{trade.month}月{trade.day}日ETF份额龙虎榜",
                    f"{inc_top['fund_name']}单日份额增加{inc_top['display_value']}，位居增加榜首",
                    f"减少榜首：{dec_top['fund_name']}",
                ],
                "min_duration_sec": 6,
                "max_duration_sec": 13,
            },
            {
                "sequence": 2,
                "page_id": "increase_top5",
                "page_type": "ranking",
                "html_file": "page_02_increase.html",
                "headline": "份额增加 TOP5",
                "tts_text": inc_tts,
                "subtitle_sentences": inc_subtitles,
                "min_duration_sec": 8,
                "max_duration_sec": 18,
            },
            {
                "sequence": 3,
                "page_id": "decrease_top5",
                "page_type": "ranking",
                "html_file": "page_03_decrease.html",
                "headline": "份额减少 TOP5",
                "tts_text": dec_tts,
                "subtitle_sentences": dec_subtitles,
                "min_duration_sec": 8,
                "max_duration_sec": 18,
            },
            {
                "sequence": 4,
                "page_id": "close",
                "page_type": "close",
                "html_file": "page_04_close.html",
                "headline": "今日数据结论",
                "tts_text": close_tts,
                "subtitle_sentences": [
                    "增加榜首变化量高于减少榜首绝对变化量",
                    "份额变化不等于资金净流入，也不代表价格涨跌",
                    "公开数据仅供参考，不构成投资建议",
                ],
                "min_duration_sec": 5,
                "max_duration_sec": 14,
            },
        ],
        "compliance": {"disclaimer": DISCLAIMER, "methodology_note": METHODOLOGY},
    }

