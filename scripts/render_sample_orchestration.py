"""Render the worked 13-section sample for review. Writes files; reads no database.

Usage::

    python scripts/render_sample_orchestration.py            # JSON + HTML to tmp/
    python scripts/render_sample_orchestration.py --out DIR
    python scripts/render_sample_orchestration.py --package professional

This is a developer review view, not the paid report template and not a
marketing sample. It shows the orchestration payload as it actually assembles —
including the sections that are empty and the figures that are absent — so the
13-section flow can be checked before a producer sees one.

The absences are the point. A review view that hid the empty incentive amounts
would look finished and prove nothing.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_STYLE = """
:root {
  --bg: #ffffff; --fg: #16161a; --muted: #6b6b76; --line: #e3e3e8;
  --accent: #2d5bd7; --warn: #a8620a; --ok: #1f7a43;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #15151a; --fg: #ececf1; --muted: #9a9aa6; --line: #2c2c35;
    --accent: #7fa0f0; --warn: #e0a34a; --ok: #5fc98a;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 16px; background: var(--bg); color: var(--fg);
  font: 15px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 900px; margin: 0 auto; }
h1 { font-size: 24px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 32px 0 8px; padding-top: 16px; border-top: 1px solid var(--line); }
.sub { color: var(--muted); font-size: 13px; margin: 0 0 24px; }
.banner {
  border: 1px solid var(--warn); border-radius: 8px; padding: 12px 14px;
  margin: 0 0 24px; font-size: 13px; color: var(--warn);
}
.meta { display: flex; flex-wrap: wrap; gap: 8px 20px; font-size: 13px; color: var(--muted); }
.empty { color: var(--muted); font-style: italic; font-size: 14px; }
.counts { font-size: 13px; color: var(--muted); margin: 0 0 8px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
td, th { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
code { font: 13px ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--accent); }
.bucket { margin: 0 0 12px; }
.bucket b { font-weight: 600; }
.pass { color: var(--ok); } .fail { color: var(--warn); }
"""


def _fmt(value) -> str:
    if value is None:
        return '<span class="empty">not calculated</span>'
    if isinstance(value, (list, tuple)):
        return html.escape(", ".join(str(v) for v in value)) if value else (
            '<span class="empty">none</span>'
        )
    if isinstance(value, dict):
        return "<code>" + html.escape(json.dumps(value, default=str)) + "</code>"
    return html.escape(str(value))


def _records_table(records: list) -> str:
    if not records:
        return '<p class="empty">No records in this section.</p>'
    if not isinstance(records[0], dict):
        return "<p>" + _fmt(list(records)) + "</p>"
    columns: list[str] = []
    for record in records:
        for key in record:
            if key not in columns:
                columns.append(key)
    head = "".join(f"<th>{html.escape(c.replace('_', ' '))}</th>" for c in columns)
    rows = "".join(
        "<tr>" + "".join(f"<td>{_fmt(record.get(c))}</td>" for c in columns) + "</tr>"
        for record in records
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"


def render_html(payload: dict) -> str:
    parts: list[str] = []
    parts.append(
        '<div class="banner">Developer review view of the v2 orchestration '
        "payload. Not a paid report, not advice, and not a real production's "
        "prospects. Empty sections and absent figures are shown deliberately: "
        "they are what the engines actually produced.</div>"
    )
    parts.append(
        '<div class="meta">'
        f"<span>run <code>{html.escape(payload['report_run_id'])}</code></span>"
        f"<span>ProjectFacts <code>{html.escape(payload['projectfacts_snapshot_id'])}</code>"
        f" v{html.escape(payload['projectfacts_version'])}</span>"
        f"<span>generated {html.escape(payload['generated_at'])}</span>"
        "</div>"
    )

    qa = payload["qa"]
    status_class = "pass" if qa["status"] == "PASS" else "fail"
    checks = "".join(
        f"<tr><td>{html.escape(c['check'])}</td>"
        f"<td class=\"{'pass' if c['status'] == 'PASS' else 'fail'}\">"
        f"{html.escape(c['status'])}</td>"
        f"<td>{html.escape(str(c.get('detail', '')))}</td></tr>"
        for c in qa["checks"]
    )
    parts.append(
        f"<h2>QA <span class=\"{status_class}\">{html.escape(qa['status'])}</span></h2>"
        f"<table><tbody>{checks}</tbody></table>"
    )

    for section in payload["sections"]:
        title = f"{section['section_number']:02d} · {section['title']}"
        parts.append(f"<h2>{html.escape(title)}</h2>")
        engines = ", ".join(section["source_engines"])
        note = " · owns no calculation" if section.get("owns_no_calculation") else ""
        parts.append(
            f'<p class="counts">from {html.escape(engines)}{html.escape(note)}</p>'
        )
        if not section["blocks"]:
            parts.append(
                '<p class="empty">No engine output for this section in this run.</p>'
            )
        for block in section["blocks"]:
            data = block["data"]
            parts.append(
                f'<p class="counts">{data["displayed_count"]} shown of '
                f'{data["eligible_universe_count"]} eligible '
                f'(package depth {data["package_entitlement"]})</p>'
            )
            parts.append(_records_table(list(data["recommendations"])))

    parts.append("<h2>Financial readiness</h2>")
    for bucket, items in payload["financial_readiness"].items():
        label = bucket.replace("_", " ")
        if items:
            listed = ", ".join(html.escape(str(i.get("label", i))) for i in items)
            parts.append(f'<p class="bucket"><b>{html.escape(label)}</b>: {listed}</p>')
        else:
            parts.append(
                f'<p class="bucket"><b>{html.escape(label)}</b>: '
                '<span class="empty">empty</span></p>'
            )

    if payload.get("cross_engine_conflicts"):
        parts.append("<h2>Cross-engine routing</h2>")
        parts.append(_records_table(payload["cross_engine_conflicts"]))

    if payload.get("next_steps"):
        parts.append("<h2>Next steps</h2>")
        parts.append(_records_table(payload["next_steps"]))

    body = "\n".join(parts)
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Orchestration Review</title>"
        f"<style>{_STYLE}</style></head><body><main>"
        "<h1>v2 orchestration — review view</h1>"
        '<p class="sub">Worked 13-section sample built from the Devil Wears '
        "Prada regression fixture.</p>"
        f"{body}</main></body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "tmp")
    parser.add_argument("--package", default="producer")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from app.modules.reports.sample_orchestration import (
        as_payload,
        build_sample_orchestration,
    )

    payload = as_payload(build_sample_orchestration(args.package))
    args.out.mkdir(parents=True, exist_ok=True)

    json_path = args.out / "sample_orchestration.json"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    html_path = args.out / "sample_orchestration.html"
    html_path.write_text(render_html(payload), encoding="utf-8")

    print(f"Sections assembled: {len(payload['sections'])}")
    print(f"QA: {payload['qa']['status']}")
    print(
        "Committed finance entries: "
        f"{len(payload['financial_readiness']['documented_committed_finance'])}"
    )
    print(f"Routing conflicts: {len(payload.get('cross_engine_conflicts') or [])}")
    print(f"\n  {json_path}\n  {html_path}")
    print("\nNo database was read and no report was generated.")


if __name__ == "__main__":
    main()
