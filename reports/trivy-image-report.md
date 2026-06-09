# Trivy Image Report

- Image: `ghcr.io/araphik/botnethunter:main`
- Artifact: `ghcr.io/araphik/botnethunter:main`
- OS: `alpine 3.23.4`
- Created: `2026-06-09T18:51:10.840679913Z`
- Total vulnerabilities: **1**
- Reverse proxy HTML report: [https://botnethunter.duckdns.org/trivy/trivy-image-report.html](https://botnethunter.duckdns.org/trivy/trivy-image-report.html)

## Summary

| Severity | Count |
| --- | ---: |
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 1 |
| LOW | 0 |
| UNKNOWN | 0 |
| **TOTAL** | **1** |

## Findings

| Severity | ID | Package | Installed | Fixed | Type | Target | Title |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MEDIUM | [CVE-2026-48710](https://avd.aquasec.com/nvd/cve-2026-48710) | starlette | 0.52.1 | 1.0.1 | python-pkg | Python | starlette: Starlette: Security restriction bypass via malformed HTTP Host header |
