from __future__ import annotations

import json
import shutil
from pathlib import Path

from .errors import PackageValidationError


PREVIEW_NAMES = {
    "page_01_cover.html": "preview_cover.png",
    "page_02_increase.html": "preview_increase.png",
    "page_03_decrease.html": "preview_decrease.png",
    "page_04_close.html": "preview_close.png",
}


def _contact_sheet(paths: list[Path], target: Path) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise PackageValidationError("Pillow 未安装，无法生成页面总览图") from exc
    canvas = Image.new("RGB", (1080, 1920), "#030913")
    draw = ImageDraw.Draw(canvas)
    slots = ((0, 0), (540, 0), (0, 960), (540, 960))
    for path, (left, top) in zip(paths, slots):
        with Image.open(path) as source:
            image = source.convert("RGB").resize((540, 960), Image.Resampling.LANCZOS)
        canvas.paste(image, (left, top))
    draw.line((540, 0, 540, 1920), fill="#23364d", width=2)
    draw.line((0, 960, 1080, 960), fill="#23364d", width=2)
    canvas.save(target, format="PNG", optimize=True)


def run_visual_qa(package_dir: Path, audit_dir: Path, html_files: list[str]) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise PackageValidationError("Playwright 未安装，无法执行页面质检") from exc
    audit_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"canvas": "1080x1920", "render_mode": "render", "pages": []}
    issues: list[str] = []
    previews: list[Path] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)

            def route_handler(route):
                if route.request.url.lower().startswith(("http://", "https://")):
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", route_handler)
            for filename in html_files:
                source = package_dir / filename
                page.goto(source.as_uri() + "?mode=render", wait_until="load", timeout=10_000)
                page.wait_for_function("document.documentElement.dataset.renderReady === 'true'", timeout=10_000)
                metrics = page.evaluate(
                    """() => {
                      const rect = el => { const r=el.getBoundingClientRect(); return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,width:r.width,height:r.height}; };
                      const critical=[...document.querySelectorAll('[data-critical]')].map(rect);
                      const names=[...document.querySelectorAll('.rank-name')].map(el=>({font:parseFloat(getComputedStyle(el).fontSize),scrollHeight:el.scrollHeight,clientHeight:el.clientHeight,box:rect(el)}));
                      const values=[...document.querySelectorAll('.rank-value')].map(el=>parseFloat(getComputedStyle(el).fontSize));
                      const rankLayout=[...document.querySelectorAll('.rank-row')].map(row=>{const rr=rect(row),main=row.querySelector('.rank-main'),value=row.querySelector('.rank-side'),mr=rect(main),vr=rect(value);return {row:rr,main:mr,value:vr};});
                      const bars=[...document.querySelectorAll('.rank-row')].map(row=>({change:parseFloat(row.style.getPropertyValue('--bar')),width:rect(row.querySelector('.bar-fill')).width,track:rect(row.querySelector('.bar-track')).width}));
                      return {ready:document.documentElement.dataset.renderReady,renderMode:document.documentElement.dataset.renderMode,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,bodyScrollWidth:document.body.scrollWidth,bodyScrollHeight:document.body.scrollHeight,critical,names,values,rankLayout,bars,runningAnimations:document.getAnimations().filter(a=>a.playState==='running').length};
                    }"""
                )
                page_issues: list[str] = []
                if metrics["renderMode"] != "render" or metrics["runningAnimations"]:
                    page_issues.append("non_deterministic_render_state")
                if max(metrics["scrollWidth"], metrics["bodyScrollWidth"]) > 1080:
                    page_issues.append("horizontal_overflow")
                if max(metrics["scrollHeight"], metrics["bodyScrollHeight"]) > 1920:
                    page_issues.append("vertical_overflow")
                for box in metrics["critical"]:
                    if box["left"] < 71.5 or box["right"] > 1008.5 or box["top"] < 111.5 or box["bottom"] > 1608.5:
                        page_issues.append("critical_content_outside_safe_area")
                        break
                if metrics["names"] and min(row["font"] for row in metrics["names"]) < 28:
                    page_issues.append("fund_name_font_too_small")
                if metrics["values"] and min(metrics["values"]) < 46:
                    page_issues.append("share_value_font_too_small")
                for name in metrics["names"]:
                    if name["scrollHeight"] > name["clientHeight"] + 1:
                        page_issues.append("fund_name_clipped")
                        break
                for layout in metrics["rankLayout"]:
                    if layout["main"]["top"] < layout["row"]["top"] or layout["main"]["bottom"] > layout["row"]["bottom"] or layout["main"]["right"] > layout["value"]["left"] + 0.5 or layout["value"]["right"] > layout["row"]["right"] + 0.5:
                        page_issues.append("ranking_text_overflow")
                        break
                for bar in metrics["bars"]:
                    actual = bar["width"] / bar["track"] * 100 if bar["track"] else 0
                    if abs(actual - bar["change"]) > 0.3:
                        page_issues.append("bar_scale_mismatch")
                        break
                screenshot = audit_dir / f"{source.stem}.png"
                page.screenshot(path=str(screenshot), full_page=False)
                preview = audit_dir / PREVIEW_NAMES.get(filename, f"preview_{source.stem}.png")
                shutil.copyfile(screenshot, preview)
                previews.append(preview)
                report["pages"].append({"file": filename, "metrics": metrics, "issues": page_issues, "screenshot": str(screenshot), "preview": str(preview)})
                issues.extend(f"{filename}:{issue}" for issue in page_issues)
            browser.close()
        if len(previews) == 4:
            contact_sheet = audit_dir / "preview_contact_sheet.png"
            _contact_sheet(previews, contact_sheet)
            report["contact_sheet"] = str(contact_sheet)
    except PackageValidationError:
        raise
    except Exception as exc:
        raise PackageValidationError(f"Playwright 页面质检失败：{exc}") from exc
    report["status"] = "failed" if issues else "ok"
    report["issues"] = issues
    (audit_dir / "visual_qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if issues:
        raise PackageValidationError("页面布局校验失败：" + "; ".join(issues))
    return report
