> # rules

[中文](README.zh-CN.md)

An automated rule-generation repository for proxy and network tools. The project fetches `domain-list-community` and `GFWList`, parses the upstream data, and generates unified outputs for Surge, Clash, Quantumult X, sing-box, and V2Ray GeoSite.

## Update Schedule

- Automated build daily at 00:00 UTC (skipped when generated output is unchanged)
- Automatic build on pushes to `main` (documentation-only changes are ignored)
- Manual workflow dispatch is supported

## Local Build

Prerequisites:

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- sing-box (the `sing-box` executable must be available in `PATH`)

```bash
uv sync
uv run generate
```

`uv run generate` can be invoked from any directory; all paths are anchored to the repository root.

Run tests:

```bash
uv run python -m unittest discover -s tests
```

## Rule Tags

- `reject`: Advertising and blocked-domain rules
- `loc-cn`: Rules for direct connections in mainland China
- `loc-!cn`: Rules for destinations outside mainland China
- `gfw`: Proxy rules from GFWList
- `gfw-skip`: Direct-connection allowlist rules from GFWList
- `ai`: Non-mainland-China AI service domains (OpenAI, Anthropic, Gemini, …)
- `streaming-cn`: Mainland-China streaming/media service domains (hand-maintained, for back-to-China routing)
- `apple`: Apple service domains (App Store, iCloud, Apple Music, software updates, …)
- `china-list`: Mainland-China domain list (geosite `cn` category)

## Artifacts in the `rel` Branch

Main artifacts include:

- `*.list`: Surge DOMAIN-SET files (domain-only)
- `*.yaml`: Clash `behavior: domain` payload files
- `*-ipcidr.yaml`: Clash `behavior: ipcidr` payload files, published only when a tag contains IP rules
- `*.quanx`: Quantumult X `filter_remote` files
- `*.srs`: sing-box binary rule-set files
- `geosite.dat` / `geosite-cn.dat` / `geosite-gfw.dat`: V2Ray GeoSite data (domain-only)
- `ext/*`: Extension files maintained under `source/`

> **Note:** `rel` is an automatically published branch. The workflow force-pushes generated content, so do not edit it manually.

### IP Rules

Upstream data occasionally contains IP literals (e.g. from GFWList). They are extracted from the domain lists and published where the format natively supports them:

- sing-box: `ip_cidr` in the compiled `.srs`
- Quantumult X: `ip-cidr` lines in `*.quanx`
- Clash: `*-ipcidr.yaml` with `behavior: ipcidr`

Surge DOMAIN-SET files and V2Ray GeoSite data are domain-only by design, so IP rules are not included there.

## Common Download URLs

- https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.list
- https://raw.githubusercontent.com/Jovanykoch/rules/rel/loc-cn.list
- https://raw.githubusercontent.com/Jovanykoch/rules/rel/loc-!cn.list
- https://raw.githubusercontent.com/Jovanykoch/rules/rel/gfw.list
- https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.srs
- https://raw.githubusercontent.com/Jovanykoch/rules/rel/geosite.dat

## Client Usage Examples

- Surge: `DOMAIN-SET,https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.list,REJECT`
- Clash: Set `rule-providers` to `https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.yaml` with `behavior: domain`. When a `{tag}-ipcidr.yaml` file exists (e.g. `gfw-ipcidr.yaml`), add a second provider with `behavior: ipcidr`:

```yaml
rule-providers:
  gfw:
    type: http
    behavior: domain
    url: https://raw.githubusercontent.com/Jovanykoch/rules/rel/gfw.yaml
    path: ./gfw.yaml
    interval: 86400
  gfw-ipcidr:
    type: http
    behavior: ipcidr
    url: https://raw.githubusercontent.com/Jovanykoch/rules/rel/gfw-ipcidr.yaml
    path: ./gfw-ipcidr.yaml
    interval: 86400
```

- Quantumult X: Set `filter_remote` to `https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.quanx`
- sing-box: Set the remote rule-set URL to `https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.srs`

## Generation Pipeline

1. Download DLC/GFWList data
2. Parse and clean rules, including deduplication, parent-domain consolidation, IP-literal extraction, and dropping URL-only regexes
3. Generate files in multiple formats in parallel
4. Compile sing-box `.srs` files
5. Generate GeoSite protobuf data
6. Validate formats and publish the results to `rel`

## License and Upstream Attribution

The code in this repository is released under the MIT License (see `LICENSE`).

Upstream rule data, including `domain-list-community`, `GFWList`, and other third-party sources, remains subject to the licenses and terms of use of the respective upstream projects. This repository only aggregates, converts, and distributes the data.
