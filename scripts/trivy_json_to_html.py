#!/usr/bin/env python3
"""Build a self-contained HTML report from a Trivy JSON report."""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path


SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
SEVERITY_RANK = {severity: index for index, severity in enumerate(SEVERITIES)}


def text(value: object, fallback: str = "n/a") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def escape(value: object, fallback: str = "n/a") -> str:
    return html.escape(text(value, fallback), quote=True)


def load_findings(report: dict) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for result in report.get("Results") or []:
        target = text(result.get("Target"))
        result_type = text(result.get("Type"))
        for vulnerability in result.get("Vulnerabilities") or []:
            findings.append(
                {
                    "severity": text(vulnerability.get("Severity"), "UNKNOWN"),
                    "id": text(vulnerability.get("VulnerabilityID")),
                    "url": text(vulnerability.get("PrimaryURL"), ""),
                    "package": text(vulnerability.get("PkgName")),
                    "installed": text(vulnerability.get("InstalledVersion")),
                    "fixed": text(vulnerability.get("FixedVersion"), "no fix"),
                    "type": result_type,
                    "target": target,
                    "title": text(
                        vulnerability.get("Title")
                        or vulnerability.get("Description")
                        or "n/a"
                    ),
                }
            )
    return sorted(
        findings,
        key=lambda item: (
            SEVERITY_RANK.get(item["severity"], SEVERITY_RANK["UNKNOWN"]),
            item["id"],
            item["package"],
        ),
    )


def render_report(report: dict, findings: list[dict[str, str]], image_ref: str) -> str:
    counts = Counter(item["severity"] for item in findings)
    total = len(findings)
    created_at = text(report.get("CreatedAt"))
    artifact_name = text(report.get("ArtifactName"), image_ref)
    os_name = text((report.get("Metadata") or {}).get("OS"), "n/a")

    cards = [
        f'<button class="card" data-filter="ALL"><strong>{total}</strong><span>ALL</span></button>'
    ]
    for severity in SEVERITIES:
        cards.append(
            f'<button class="card" data-filter="{severity}">'
            f"<strong>{counts[severity]}</strong><span>{severity}</span></button>"
        )

    rows = []
    for finding in findings:
        severity = escape(finding["severity"])
        vuln_id = escape(finding["id"])
        url = finding["url"]
        if url:
            vuln_cell = f'<a href="{escape(url)}">{vuln_id}</a>'
        else:
            vuln_cell = vuln_id
        rows.append(
            '<tr data-severity="{severity}" data-search="{search}">'.format(
                severity=severity,
                search=escape(" ".join(finding.values()).lower()),
            )
            + f'<td><span class="badge {severity.lower()}">{severity}</span></td>'
            + f"<td>{vuln_cell}</td>"
            + f"<td>{escape(finding['package'])}</td>"
            + f"<td>{escape(finding['installed'])}</td>"
            + f"<td>{escape(finding['fixed'])}</td>"
            + f"<td>{escape(finding['type'])}</td>"
            + f'<td class="title">{escape(finding["title"])}</td>'
            + f"<td>{escape(finding['target'])}</td>"
            + "</tr>"
        )

    empty_message = ""
    if not rows:
        empty_message = '<p class="empty">No vulnerabilities found.</p>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trivy image report - {escape(image_ref)}</title>
<style>
:root {{
  color-scheme: light dark;
  --bg: #0f1419;
  --panel: #161c23;
  --text: #e7edf3;
  --muted: #9aa7b4;
  --line: #2a3440;
  --link: #6db6ff;
  --critical: #8b1d2c;
  --high: #c2410c;
  --medium: #a16207;
  --low: #2563eb;
  --unknown: #64748b;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--text);
}}
main {{ max-width: 1280px; margin: 0 auto; padding: 28px; }}
h1 {{ margin: 0 0 6px; font-size: 26px; letter-spacing: 0; }}
.meta {{ color: var(--muted); margin-bottom: 22px; }}
.cards {{
  display: grid;
  grid-template-columns: repeat(6, minmax(110px, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}}
.card {{
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--text);
  border-radius: 8px;
  padding: 14px;
  text-align: left;
  cursor: pointer;
}}
.card strong {{ display: block; font-size: 28px; }}
.card span {{ color: var(--muted); font-size: 12px; }}
.toolbar {{ display: flex; gap: 10px; margin: 16px 0; }}
.toolbar input,
.toolbar select {{
  background: var(--panel);
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px;
}}
.toolbar input {{ flex: 1; min-width: 220px; }}
.table-wrap {{ border: 1px solid var(--line); border-radius: 8px; overflow: auto; background: var(--panel); }}
table {{ border-collapse: collapse; width: 100%; min-width: 1100px; }}
th,
td {{ padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
th {{ position: sticky; top: 0; background: #111820; text-align: left; color: #cbd5df; }}
a {{ color: var(--link); }}
.title {{ max-width: 420px; }}
.badge {{
  display: inline-block;
  min-width: 72px;
  text-align: center;
  border-radius: 999px;
  padding: 3px 8px;
  font-size: 12px;
  font-weight: 700;
}}
.critical {{ background: var(--critical); }}
.high {{ background: var(--high); }}
.medium {{ background: var(--medium); }}
.low {{ background: var(--low); }}
.unknown {{ background: var(--unknown); }}
.empty {{ color: var(--muted); }}
@media (max-width: 760px) {{
  main {{ padding: 16px; }}
  .cards {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .toolbar {{ flex-direction: column; }}
}}
</style>
</head>
<body>
<main>
<h1>Trivy Image Report</h1>
<div class="meta">
  Image: <strong>{escape(image_ref)}</strong> · Artifact: {escape(artifact_name)}
  · OS: {escape(os_name)} · Created: {escape(created_at)}
  · Total vulnerabilities: <strong id="total">{total}</strong>
</div>
<div class="cards">
{''.join(cards)}
</div>
<div class="toolbar">
  <input id="q" placeholder="Search CVE, package, version, target, title">
  <select id="severity">
    <option value="ALL">All severities</option>
    {''.join(f'<option>{severity}</option>' for severity in SEVERITIES)}
  </select>
</div>
{empty_message}
<div class="table-wrap">
<table>
<thead>
<tr><th>Severity</th><th>ID</th><th>Package</th><th>Installed</th><th>Fixed</th><th>Type</th><th>Title</th><th>Target</th></tr>
</thead>
<tbody id="tbody">
{''.join(rows)}
</tbody>
</table>
</div>
</main>
<script>
const search = document.querySelector("#q");
const severity = document.querySelector("#severity");
const rows = Array.from(document.querySelectorAll("tbody tr"));
function applyFilters() {{
  const query = search.value.trim().toLowerCase();
  const selected = severity.value;
  let visible = 0;
  rows.forEach((row) => {{
    const matchesSeverity = selected === "ALL" || row.dataset.severity === selected;
    const matchesQuery = query === "" || row.dataset.search.includes(query);
    const show = matchesSeverity && matchesQuery;
    row.style.display = show ? "" : "none";
    if (show) visible += 1;
  }});
  document.querySelector("#total").textContent = visible;
}}
document.querySelectorAll(".card").forEach((button) => {{
  button.addEventListener("click", () => {{
    severity.value = button.dataset.filter;
    applyFilters();
  }});
}});
search.addEventListener("input", applyFilters);
severity.addEventListener("change", applyFilters);
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--image-ref", default="unknown")
    args = parser.parse_args()

    with args.input.open(encoding="utf-8") as file:
        report = json.load(file)

    findings = load_findings(report)
    args.output.write_text(
        render_report(report, findings, args.image_ref),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
