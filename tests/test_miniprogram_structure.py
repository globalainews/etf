from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ten_native_pages_are_routable():
    app = json.loads((ROOT / "miniprogram" / "app.json").read_text(encoding="utf-8"))
    assert len(app["pages"]) == 10
    assert {item["text"] for item in app["tabBar"]["list"]} == {"龙虎榜", "自选", "搜索", "我的"}
    for page in app["pages"]:
        base = ROOT / "miniprogram" / page
        for suffix in (".js", ".json", ".wxml", ".wxss"):
            assert base.with_suffix(suffix).exists(), f"missing {base.with_suffix(suffix)}"


def test_real_mode_does_not_fall_back_to_demo_and_has_no_secret():
    source = (ROOT / "miniprogram" / "utils" / "api.js").read_text(encoding="utf-8")
    real_branch = source.split("if(env.mode!=='real')", 1)[1]
    assert "demoCall" not in real_branch
    all_client_source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "miniprogram").rglob("*.js"))
    assert "CLOUDBASE_API_KEY" not in all_client_source
    assert "AppSecret" not in all_client_source


def test_security_rules_block_direct_database_access():
    rules = json.loads((ROOT / "cloud-database" / "database-security-rules.json").read_text(encoding="utf-8"))
    assert rules == {"read": False, "write": False}

