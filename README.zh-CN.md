# rules

[English](README.md)

一个为代理与网络工具自动生成规则的仓库。项目拉取 `domain-list-community` 与 `GFWList` 的上游数据，解析后统一生成 Surge、Clash、Quantumult X、sing-box 与 V2Ray GeoSite 格式的规则文件。

## 更新计划

- 每天 00:00 UTC 自动构建（生成产物无变化时跳过发布）
- 推送到 `main` 分支时自动构建（仅文档的改动会被忽略）
- 支持手动触发 workflow

## 本地构建

依赖：

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- sing-box（`sing-box` 可执行文件需在 `PATH` 中）

```bash
uv sync
uv run generate
```

`uv run generate` 可在任意目录下执行，所有路径都锚定到仓库根目录。

运行测试：

```bash
uv run python -m unittest discover -s tests
```

## 规则标签

- `reject`：广告与屏蔽域名规则
- `loc-cn`：中国大陆直连规则
- `loc-!cn`：中国大陆以外地区的规则
- `gfw`：来自 GFWList 的代理规则
- `gfw-skip`：来自 GFWList 的直连白名单规则
- `ai`：中国大陆以外的 AI 服务域名（OpenAI、Anthropic、Gemini 等）
- `streaming-cn`：中国大陆流媒体/音视频服务域名（手工维护，回国场景用）
- `apple`：Apple 服务域名（App Store、iCloud、Apple Music、系统更新等）
- `douyin`：抖音服务域名（douyin.com、snssdk.com、抖音火山版、汽水音乐、抖音支付/电商等）
- `china`：中国大陆域名列表——v2fly `cn` 合并 felixonmars dnsmasq-china-list（约 11 万域名，与 Loyalsoldier 的 `geosite:china-list` 同配方）

## `rel` 分支的产物

主要产物包括：

- `*.list`：Surge DOMAIN-SET 文件（仅域名）
- `*.yaml`：Clash `behavior: domain` 规则集文件
- `*-ipcidr.yaml`：Clash `behavior: ipcidr` 规则集文件，仅当某标签含有 IP 规则时发布
- `*.quanx`：Quantumult X `filter_remote` 文件
- `*.srs`：sing-box 二进制规则集文件
- `geosite.dat` / `geosite-cn.dat` / `geosite-gfw.dat`：V2Ray GeoSite 数据（仅域名）
- `ext/*`：`source/` 目录下手工维护的扩展文件

> **注意：** `rel` 是自动发布的分支，workflow 会强制推送生成的内容，请勿手动修改。

### IP 规则

上游数据偶尔包含 IP 字面量（例如来自 GFWList）。它们会被从域名列表中提取出来，只发布到原生支持 IP 的格式中：

- sing-box：编译进 `.srs` 的 `ip_cidr`
- Quantumult X：`*.quanx` 中的 `ip-cidr` 行
- Clash：`behavior: ipcidr` 的 `*-ipcidr.yaml`

Surge DOMAIN-SET 文件与 V2Ray GeoSite 数据按设计只含域名，因此不包含 IP 规则。

## 常用下载地址

- https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.list
- https://raw.githubusercontent.com/Jovanykoch/rules/rel/loc-cn.list
- https://raw.githubusercontent.com/Jovanykoch/rules/rel/loc-!cn.list
- https://raw.githubusercontent.com/Jovanykoch/rules/rel/gfw.list
- https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.srs
- https://raw.githubusercontent.com/Jovanykoch/rules/rel/geosite.dat

## 客户端使用示例

- Surge：`DOMAIN-SET,https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.list,REJECT`
- Clash：将 `rule-providers` 指向 `https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.yaml`（`behavior: domain`）。若存在 `{tag}-ipcidr.yaml`（例如 `gfw-ipcidr.yaml`），再加一个 `behavior: ipcidr` 的 provider：

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

- Quantumult X：将 `filter_remote` 指向 `https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.quanx`
- sing-box：将远端规则集 URL 设为 `https://raw.githubusercontent.com/Jovanykoch/rules/rel/reject.srs`

## 生成流程

1. 下载 DLC / GFWList 数据
2. 解析并清洗规则，包括去重、父域名合并、IP 字面量提取、丢弃只匹配 URL 的正则
3. 并行生成多种格式的文件
4. 编译 sing-box `.srs` 文件
5. 生成 GeoSite protobuf 数据
6. 校验格式并发布到 `rel` 分支

## 许可证与上游归属

本仓库代码基于 MIT 许可证发布（见 `LICENSE`）。

上游规则数据（包括 `domain-list-community`、`GFWList`、`Loyalsoldier/geoip`（`tools/mmdb` 中国区 MMDB 生成器所用）及其他第三方来源）仍受各自上游项目的许可证与使用条款约束。本仓库仅做聚合、转换与分发。
