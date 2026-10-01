import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import yaml

# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

BLOCK_DOMAIN_SUFFIX = (
    "miaozhen.com",
    "tqt.weibo.cn",
    "qzs.gdtimg.com",
    "adsmind.gdtimg.com",
    "gdt.qq.com",
    "mazu.m.qq.com",
    "e.kuaishou.cn",
    "e.kuaishou.com",
    "umeng.com",
    "umengcloud.com",
    "fapi.xdrun.com",
    "mix-mind.com",
    "in-neo.com",
    "rtbasia.com",
    "gridsum.com",
    "addnewer.com",
    "msmp.abchina.com.cn",
    "statics.adanxing.com",
    "promotion-partner.kuaishou.com",
    "qttunion.com",
    "1sapp.com",
    "shenshiads.com",
    "domob.cn",
    "aiclk.com",
    "guanggao-prod.cn-shanghai.log.aliyuncs.com",
)

DIRECT_DOMAIN = ("api.github.com",)

DIRECT_DOMAIN_SUFFIX = (
    "cn",
    "local",
    "steamserver.net",
    "steamcontent.com",
    "msftconnecttest.com",
    "msftncsi.com",
    "hotmail.com",
    "baozimh.com",
    "baozicdn.com",
)

# ═══════════════════════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Upstream
# ═══════════════════════════════════════════════════════════════════════════════

DomainResult = tuple[
    list[str],  # domain
    list[str],  # domain_suffix
    list[str],  # domain_keyword
    list[str],  # domain_regex
]

GeoSiteRules = dict[str, DomainResult]

GEOSITE_TAGS = (
    "reject",
    "loc-!cn",
    "loc-cn",
    "ai",
    "streaming-cn",
    "school",
    "apple",
    "douyin",
    "china",
    "ads",
    "ads-mini",
)

GFWLIST_TAGS = ("gfw", "gfw-skip")

# Tags built from hand-maintained local sources instead of upstream data.
# Format follows source/maintained.list: one domain per line,
# a leading dot marks a domain suffix, '#' starts a comment.
LOCAL_TAG_SOURCES = {
    "streaming-cn": "source/streaming-cn.list",
    "school": "source/school.list",
}
# Extra suffixes for the `douyin` tag: the upstream v2fly `douyin`
# category only covers Beijing Douyin Technology domains, but the Douyin
# app's core API (aweme.snssdk.com, api.snssdk.com, …) and its image CDNs
# live in the shared `bytedance` category.
DOUYIN_EXTRA_DOMAIN_SUFFIX = (
    "snssdk.com",
    "pstatp.com",
    "byteimg.com",
    "ibyteimg.com",
)

DOWNLOAD_TIMEOUT_SECONDS = 20
DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024
DOWNLOAD_RETRIES = 3
DOWNLOAD_RETRY_DELAY_SECONDS = 1.0
USER_AGENT = (
    "Mozilla/5.0 (compatible; Jovanykoch-rules/1.0; "
    "+https://github.com/Jovanykoch/rules)"
)

# Upstream source for the `china` tag: felixonmars/dnsmasq-china-list
# (same recipe as Loyalsoldier's geosite:china-list, ~110k domains).
DNSMASQ_CHINA_LIST_URL = (
    "https://raw.githubusercontent.com/felixonmars/dnsmasq-china-list/"
    "master/accelerated-domains.china.conf"
)

# Upstream sources for the `ads` / `ads-mini` tags: hagezi/dns-blocklists
# "domains only" lists (one bare domain per line, '#' starts a comment).
# Every entry matches the domain and its subdomains (wildcard semantics),
# so all entries become domain suffixes.
HAGEZI_ADS_URL = (
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/"
    "main/wildcard/pro-onlydomains.txt"
)
HAGEZI_ADS_MINI_URL = (
    "https://raw.githubusercontent.com/hagezi/dns-blocklists/"
    "main/wildcard/pro.mini-onlydomains.txt"
)


def fetch_url_bytes(url: str, *, timeout: int = DOWNLOAD_TIMEOUT_SECONDS) -> bytes:
    """Download bytes with retries and size guards."""
    last_error: Exception | None = None
    request = Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", None)
                if status is not None and status >= 400:
                    raise ValueError(f"HTTP {status} for {url}")
                content = response.read(DOWNLOAD_MAX_BYTES + 1)
            if not content:
                raise ValueError(f"Empty response from {url}")
            if len(content) > DOWNLOAD_MAX_BYTES:
                raise ValueError(
                    f"Response from {url} exceeds {DOWNLOAD_MAX_BYTES} bytes"
                )
            return content
        except Exception as error:
            last_error = error
            if attempt == DOWNLOAD_RETRIES:
                break
            log.warning(
                "Download failed (%d/%d): %s", attempt, DOWNLOAD_RETRIES, error
            )
            time.sleep(DOWNLOAD_RETRY_DELAY_SECONDS)
    raise RuntimeError(f"Failed to download {url}") from last_error


def parse_dlc_plain(url: str, tags: tuple[str, ...]) -> GeoSiteRules:
    """Extract flattened tags and supplement them with matching global attributes."""
    log.info("Downloading %s", url)
    loaded = yaml.safe_load(fetch_url_bytes(url)) or {}
    lists = loaded.get("lists", [])

    remaining = set(tags)
    result: GeoSiteRules = {}
    attribute_targets = {
        attribute: tag
        for attribute, tag in (("cn", "geolocation-cn"), ("ads", "category-ads-all"))
        if tag in tags
    }
    attribute_rules: GeoSiteRules = {
        attribute: ([], [], [], []) for attribute in attribute_targets
    }
    rule_indexes = {"full": 0, "domain": 1, "keyword": 2, "regexp": 3}
    for item in lists:
        tag = item.get("name")
        selected = tag in remaining
        if not selected and not attribute_targets:
            continue

        values: DomainResult = ([], [], [], [])
        for raw_rule in item.get("rules", []):
            # Upstream serializes attributes as :@attr1,@attr2.
            rule, _, attributes = raw_rule.partition(":@")
            matching_attributes = attribute_targets.keys() & set(attributes.split(",@"))
            if not selected and not matching_attributes:
                continue
            rule_type, separator, value = rule.partition(":")
            if not separator or rule_type not in rule_indexes:
                raise ValueError(f"Unsupported DLC rule: {raw_rule!r}")
            index = rule_indexes[rule_type]
            if selected:
                values[index].append(value)
            for attribute in matching_attributes:
                attribute_rules[attribute][index].append(value)

        if selected:
            result[tag] = values
            remaining.remove(tag)
        if not remaining and not attribute_targets:
            break

    if remaining:
        raise ValueError(f"Missing DLC tags: {', '.join(sorted(remaining))}")
    for attribute, target in attribute_targets.items():
        for destination, additions in zip(result[target], attribute_rules[attribute]):
            destination[:] = dict.fromkeys(destination + additions)
    return result


def _gfwlist_host(rule: str) -> str:
    """Extract a host from an AutoProxy URL or domain-anchor rule."""
    if rule.startswith("||"):
        return re.split(r"[/:^|]", rule[2:], maxsplit=1)[0]

    parsed = urlsplit(rule.removeprefix("|"))
    return parsed.hostname or ""


def _append_gfwlist_host(
    host: str, domain: list[str], domain_suffix: list[str], *, suffix: bool
) -> None:
    host = host.lower().strip(".")
    if not host:
        raise ValueError("GFWList rule has no host")

    if "*" in host:
        # Domain-only formats cannot retain a wildcard label. Keep the stable
        # suffix after the final wildcard so all generated clients agree.
        host = host.rsplit("*", maxsplit=1)[1].lstrip(".")
        if not host:
            raise ValueError("GFWList wildcard rule has no domain suffix")
        suffix = True

    (domain_suffix if suffix else domain).append(host)


def _regex_targets_url_path(pattern: str) -> bool:
    """Detect a literal slash outside character classes (i.e. a URL path).

    Slashes inside ``[...]`` classes (e.g. the ``[^\\/]`` "not a slash" idiom)
    are fine for domain matching; a slash anywhere else means the pattern was
    written against full URLs and can never match a bare domain.
    """
    in_class = False
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            if pattern[index + 1] == "/" and not in_class:
                return True
            index += 2
            continue
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            return True
        index += 1
    return False


def _append_gfwlist_rule(rule: str, values: DomainResult) -> None:
    domain, domain_suffix, _, domain_regex = values
    if rule.startswith("/"):
        pattern = rule[1:-1] if rule.endswith("/") else rule[1:]
        # GFWList regexes match complete URLs. Generated GeoSite/sing-box
        # files match domains, so remove the anchored URL-scheme portion.
        pattern = pattern.removeprefix(r"^https?:\/\/")
        lookahead = re.fullmatch(r"\(\?=\.\*\?\(([^)]+)\)\)(\[[^]]+\])\+(.*)", pattern)
        if lookahead:
            alternatives, character_class, tail = lookahead.groups()
            pattern = f"^{character_class}*(?:{alternatives}){character_class}*{tail}"
        if _regex_targets_url_path(pattern):
            log.warning(
                "Dropping GFWList regex that matches URL paths, not domains: %r",
                rule,
            )
            return
        domain_regex.append(pattern)
        return
    if rule.startswith("||"):
        _append_gfwlist_host(_gfwlist_host(rule), domain, domain_suffix, suffix=True)
        return
    if rule.startswith(("|http://", "|https://")):
        _append_gfwlist_host(_gfwlist_host(rule), domain, domain_suffix, suffix=False)
        return
    if re.fullmatch(r"[A-Za-z0-9._-]+", rule):
        _append_gfwlist_host(rule, domain, domain_suffix, suffix=False)
        return
    raise ValueError(f"Unsupported GFWList rule: {rule!r}")


def parse_gfwlist_text(content: bytes) -> GeoSiteRules:
    """Convert the plaintext AutoProxy GFWList into domain-only rule sets."""
    try:
        text = content.decode()
    except UnicodeDecodeError as error:
        raise ValueError("Invalid UTF-8 GFWList") from error
    if not text.startswith("[AutoProxy "):
        raise ValueError("Invalid GFWList header")

    result: GeoSiteRules = {
        "gfw": ([], [], [], []),
        "gfw-skip": ([], [], [], []),
    }

    for raw_rule in text.splitlines():
        rule = raw_rule.strip()
        if not rule or rule.startswith(("!", "[")):
            continue
        tag = "gfw"
        if rule.startswith("@@"):
            tag = "gfw-skip"
            rule = rule[2:]
        _append_gfwlist_rule(rule, result[tag])

    for tag, (domain, domain_suffix, _, domain_regex) in result.items():
        log.info(
            "Parsed GFWList tag %s: %d domains, %d suffixes, %d regexes",
            tag,
            len(domain),
            len(domain_suffix),
            len(domain_regex),
        )
    return result


def parse_gfwlist(url: str) -> GeoSiteRules:
    """Download and parse the official plaintext GFWList."""
    log.info("Downloading %s", url)
    return parse_gfwlist_text(fetch_url_bytes(url))


def parse_dnsmasq_china_list_text(
    content: bytes,
) -> tuple[list[str], list[str]]:
    """Parse felixonmars dnsmasq-china-list accelerated-domains.china.conf.

    Active lines look like ``server=/example.com/114.114.114.114``;
    disabled entries are commented out with '#'. A dnsmasq ``server=``
    entry matches the domain and all its subdomains, so every entry
    becomes a domain suffix. Returns (exact_domains, domain_suffixes).
    """
    suffixes: list[str] = []
    for raw_line in content.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line.startswith("server=/"):
            continue
        domain = line[len("server=/") :].split("/", 1)[0].strip().lower()
        if domain:
            suffixes.append(domain)
    return [], list(dict.fromkeys(suffixes))


def parse_dnsmasq_china_list(url: str) -> tuple[list[str], list[str]]:
    """Download and parse the dnsmasq-china-list accelerated domains file."""
    log.info("Downloading %s", url)
    return parse_dnsmasq_china_list_text(fetch_url_bytes(url))


def parse_hagezi_onlydomains_text(
    content: bytes,
) -> tuple[list[str], list[str]]:
    """Parse a hagezi/dns-blocklists ``*-onlydomains.txt`` file.

    One bare domain per line; lines starting with '#' and blank lines are
    ignored. Each entry matches the domain and all its subdomains, so
    every entry becomes a domain suffix.
    Returns (exact_domains, domain_suffixes) deduplicated in file order.
    """
    suffixes: list[str] = []
    for raw_line in content.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip().lower()
        if not line or line.startswith("#"):
            continue
        suffixes.append(line)
    return [], list(dict.fromkeys(suffixes))


def parse_hagezi_onlydomains(url: str) -> tuple[list[str], list[str]]:
    """Download and parse a hagezi/dns-blocklists onlydomains file."""
    log.info("Downloading %s", url)
    return parse_hagezi_onlydomains_text(fetch_url_bytes(url))


def parse_local_domain_list(path: str) -> tuple[list[str], list[str]]:
    """Parse a hand-maintained domain list (see source/maintained.list).

    One domain per line; a leading dot marks a domain suffix;
    lines starting with '#' and blank lines are ignored.
    Returns (exact_domains, domain_suffixes) deduplicated in file order.
    """
    domain: list[str] = []
    domain_suffix: list[str] = []
    with open(path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("."):
                domain_suffix.append(line[1:])
            else:
                domain.append(line)
    return list(dict.fromkeys(domain)), list(dict.fromkeys(domain_suffix))


# ═══════════════════════════════════════════════════════════════════════════════
# Release — generate output files for all supported proxy platforms
# ═══════════════════════════════════════════════════════════════════════════════


def extract_ip_cidrs(values: list[str]) -> tuple[list[str], list[str]]:
    """Split IP literals out of a domain list.

    Returns ``(domains, cidrs)`` where IP literals are converted to CIDR
    notation (``/32`` for IPv4, ``/128`` for IPv6). Domain-only outputs
    (Surge DOMAIN-SET, GeoSite) cannot express IP rules, so callers route the
    CIDRs to formats with native IP support instead of emitting dead rules.
    """
    domains: list[str] = []
    cidrs: list[str] = []
    for value in values:
        try:
            ip = ipaddress.ip_address(value)
        except ValueError:
            domains.append(value)
        else:
            cidrs.append(f"{ip}/{32 if ip.version == 4 else 128}")
    return domains, cidrs


def release(
    domain: list[str],
    domain_suffix: list[str],
    domain_keyword: list[str],
    domain_regex: list[str],
    tag: str,
    *,
    quanx_policy: str = "direct",
) -> DomainResult:
    """Generate output files (Surge, Clash, QuanX, sing-box) for *tag*."""
    log.info("Releasing tag: %s", tag)
    domain, domain_ips = extract_ip_cidrs(domain)
    domain_suffix, suffix_ips = extract_ip_cidrs(domain_suffix)
    ip_cidr = list(dict.fromkeys([*domain_ips, *suffix_ips]))
    if ip_cidr:
        log.info(
            "Tag %s: moved %d IP literal(s) to ip_cidr rules",
            tag,
            len(ip_cidr),
        )
    domain, domain_suffix = clean_domains(domain, domain_suffix)
    domain_keyword = list(dict.fromkeys(domain_keyword))
    domain_regex = list(dict.fromkeys(domain_regex))

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(release_surge_file, tag, domain, domain_suffix),
            pool.submit(release_clash_file, tag, domain, domain_suffix),
            pool.submit(
                release_quanx_file,
                tag,
                domain,
                domain_suffix,
                domain_keyword,
                quanx_policy,
                ip_cidr,
            ),
            pool.submit(
                release_singbox_file,
                tag,
                domain,
                domain_suffix,
                domain_regex,
                domain_keyword,
                ip_cidr,
            ),
        ]
        for future in futures:
            future.result()

    if ip_cidr:
        # Clash rule-providers require `behavior` to match the payload:
        # IP rules get their own `ipcidr`-behavior file.
        release_clash_ipcidr_file(tag, ip_cidr)

    return domain, domain_suffix, domain_keyword, domain_regex


def release_surge_file(tag: str, domain: list[str], domain_suffix: list[str]) -> None:
    filename = f"dist/{tag}.list"
    with open(filename, "w", encoding="utf-8") as f:
        f.writelines(s + "\n" for s in domain)
        f.writelines("." + s + "\n" for s in domain_suffix)


def release_clash_file(tag: str, domain: list[str], domain_suffix: list[str]) -> None:
    filename = f"dist/{tag}.yaml"
    with open(filename, "w", encoding="utf-8") as f:
        yaml.dump(
            {"payload": domain + ["." + s for s in domain_suffix]},
            f,
            default_flow_style=False,
            allow_unicode=True,
        )


def release_clash_ipcidr_file(tag: str, ip_cidr: list[str]) -> None:
    """Write a Clash `behavior: ipcidr` rule-provider file."""
    filename = f"dist/{tag}-ipcidr.yaml"
    with open(filename, "w", encoding="utf-8") as f:
        yaml.dump(
            {"payload": list(ip_cidr)},
            f,
            default_flow_style=False,
            allow_unicode=True,
        )


def release_quanx_file(
    tag: str,
    domain: list[str],
    domain_suffix: list[str],
    domain_keyword: list[str],
    policy: str = "direct",
    ip_cidr: list[str] = (),
) -> None:
    filename = f"dist/{tag}.quanx"
    with open(filename, "w", encoding="utf-8", buffering=65536) as f:
        f.writelines(f"host, {s}, {policy}\n" for s in domain)
        f.writelines(f"host-suffix, {s}, {policy}\n" for s in domain_suffix)
        f.writelines(f"host-keyword, {s}, {policy}\n" for s in domain_keyword)
        f.writelines(f"ip-cidr, {c}, {policy}\n" for c in ip_cidr)


def release_singbox_file(
    tag: str,
    domain: list[str],
    domain_suffix: list[str],
    domain_regex: list[str],
    domain_keyword: list[str],
    ip_cidr: list[str] = (),
) -> None:
    filename = f"tmp/{tag}.json"
    rule = {
        key: values
        for key, values in (
            ("domain", domain),
            ("domain_suffix", domain_suffix),
            ("domain_regex", domain_regex),
            ("domain_keyword", domain_keyword),
            ("ip_cidr", list(ip_cidr)),
        )
        if values
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump({"version": 2, "rules": [rule]}, f, separators=(",", ":"))

    output = f"dist/{tag}.srs"
    log.info("Compiling sing-box file: %s", filename)
    subprocess.run(
        ["sing-box", "rule-set", "compile", "--output", output, filename],
        check=True,
        timeout=300,
    )


def validate_surge_file(path: str) -> None:
    domain_line = re.compile(r"^\.?[A-Za-z0-9.-]+$")
    with open(path, encoding="utf-8") as f:
        for index, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                raise ValueError(f"{path}:{index} must not be empty")
            if not domain_line.fullmatch(line):
                raise ValueError(f"{path}:{index} invalid Surge domain line: {line}")
            try:
                ipaddress.ip_address(line.lstrip("."))
            except ValueError:
                pass
            else:
                raise ValueError(
                    f"{path}:{index} IP literal does not belong in a Surge "
                    f"DOMAIN-SET file: {line}"
                )


def validate_clash_yaml(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    payload = data.get("payload") if isinstance(data, dict) else None
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"{path} payload must be a non-empty list")
    if not all(isinstance(item, str) and item for item in payload):
        raise ValueError(f"{path} payload must contain non-empty strings")


def validate_quanx_file(path: str) -> None:
    line_pattern = re.compile(
        r"^(host|host-suffix|host-keyword|ip-cidr),\s*[^,\s]+,\s*(direct|proxy)$"
    )
    with open(path, encoding="utf-8") as f:
        for index, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                raise ValueError(f"{path}:{index} must not be empty")
            if not line_pattern.fullmatch(line):
                raise ValueError(f"{path}:{index} invalid QuanX line: {line}")


def validate_singbox_json(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") != 2:
        raise ValueError(f"{path} must use sing-box rule-set version 2")
    rules = data.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError(f"{path} must include at least one rule object")


def validate_binary_geosite(path: str) -> None:
    with open(path, "rb") as f:
        content = f.read()
    if not content:
        raise ValueError(f"{path} must be non-empty")
    if content[0] != 0x0A:
        raise ValueError(f"{path} does not look like a GeoSite protobuf payload")


def validate_non_empty_file(path: str) -> None:
    if os.path.getsize(path) <= 0:
        raise ValueError(f"{path} must be non-empty")


def validate_outputs(tags: tuple[str, ...]) -> None:
    for tag in tags:
        validate_surge_file(f"dist/{tag}.list")
        validate_clash_yaml(f"dist/{tag}.yaml")
        validate_quanx_file(f"dist/{tag}.quanx")
        validate_singbox_json(f"tmp/{tag}.json")
        validate_non_empty_file(f"dist/{tag}.srs")
        ipcidr_path = f"dist/{tag}-ipcidr.yaml"
        if os.path.exists(ipcidr_path):
            validate_clash_yaml(ipcidr_path)
    for geosite_path in (
        "dist/geosite.dat",
        "dist/geosite-cn.dat",
        "dist/geosite-gfw.dat",
    ):
        validate_binary_geosite(geosite_path)
    log.info("Validated generated outputs")


# ═══════════════════════════════════════════════════════════════════════════════
# V2Ray GeoSite output
# ══════════════════════════════════════════════════════════════════════════════


def _protobuf_varint(value: int) -> bytes:
    """Encode a non-negative integer using the protobuf varint format."""
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _protobuf_field(field_number: int, wire_type: int, value: bytes) -> bytes:
    key = _protobuf_varint((field_number << 3) | wire_type)
    if wire_type == 2:
        return key + _protobuf_varint(len(value)) + value
    return key + value


def _encode_geosite_domain(domain_type: int, value: str) -> bytes:
    # v2ray routercommon.Domain: type = 1, value = 2.
    return _protobuf_field(1, 0, _protobuf_varint(domain_type)) + _protobuf_field(
        2, 2, value.encode()
    )


def _encode_geosite(tag: str, rules: DomainResult) -> bytes:
    # v2ray routercommon.GeoSite: country_code = 1, domain = 2.
    domain, domain_suffix, domain_keyword, domain_regex = rules
    fields = [_protobuf_field(1, 2, tag.upper().encode())]
    typed_rules = (
        (3, domain),  # Full
        (2, domain_suffix),  # RootDomain
        (0, domain_keyword),  # Plain
        (1, domain_regex),  # Regex
    )
    for domain_type, values in typed_rules:
        for value in dict.fromkeys(values):
            encoded = _encode_geosite_domain(domain_type, value)
            fields.append(_protobuf_field(2, 2, encoded))
    return b"".join(fields)


def write_geosite_file(filename: str, rules_by_tag: GeoSiteRules) -> None:
    """Write a V2Ray-compatible GeoSiteList protobuf file."""
    # routercommon.GeoSiteList has one repeated GeoSite field named entry (1).
    with open(filename, "wb") as f:
        f.writelines(
            _protobuf_field(1, 2, _encode_geosite(tag, rules_by_tag[tag]))
            for tag in sorted(rules_by_tag)
        )
    log.info("Wrote GeoSite file: %s (%d tags)", filename, len(rules_by_tag))


def release_geosite_files(
    rules_by_tag: GeoSiteRules, gfwlist_rules: GeoSiteRules
) -> None:
    """Generate the general, CN-only, and GFWList GeoSite databases."""
    complete_rules = {tag: rules_by_tag[tag] for tag in GEOSITE_TAGS}
    write_geosite_file("dist/geosite.dat", complete_rules)
    write_geosite_file("dist/geosite-cn.dat", {"loc-cn": rules_by_tag["loc-cn"]})
    write_geosite_file(
        "dist/geosite-gfw.dat",
        {tag: gfwlist_rules[tag] for tag in GFWLIST_TAGS},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════════════════════════════════════════


def clean_domains(
    domain: list[str], domain_suffix: list[str]
) -> tuple[list[str], list[str]]:
    """Remove domains whose parent is already covered by a domain suffix."""
    log.info(
        "Cleaning domains: %d domains, %d suffixes", len(domain), len(domain_suffix)
    )

    unique_suffixes = dict.fromkeys(domain_suffix)

    def covered(value: str, *, include_self: bool) -> bool:
        if include_self and value in unique_suffixes:
            return True
        parent = value
        while (dot := parent.find(".")) >= 0:
            parent = parent[dot + 1 :]
            if parent in unique_suffixes:
                return True
        return False

    return (
        [value for value in dict.fromkeys(domain) if not covered(value, include_self=True)],
        [
            value
            for value in unique_suffixes
            if not covered(value, include_self=False)
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════


def main() -> None:
    # Anchor all relative paths (dist/, tmp/) to the repository root so
    # `uv run generate` works no matter where it is invoked from.
    os.chdir(Path(__file__).resolve().parent)
    shutil.rmtree("dist", ignore_errors=True)
    os.makedirs("dist")
    os.makedirs("tmp", exist_ok=True)

    try:
        _run()
    finally:
        shutil.rmtree("tmp", ignore_errors=True)


def _run() -> None:
    rule_tags = (
        ("category-ads-all", "reject", (), BLOCK_DOMAIN_SUFFIX),
        ("geolocation-!cn", "loc-!cn", (), ()),
        ("geolocation-cn", "loc-cn", DIRECT_DOMAIN, DIRECT_DOMAIN_SUFFIX),
        ("category-ai-!cn", "ai", (), ()),
        ("apple", "apple", (), ()),
        # `douyin`: upstream category lacks the shared ByteDance API/CDN
        # domains the Douyin app itself uses, so they are added here.
        ("douyin", "douyin", (), DOUYIN_EXTRA_DOMAIN_SUFFIX),
    )
    upstream_rules = parse_dlc_plain(
        "https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat_plain.yml",
        (*tuple(rule[0] for rule in rule_tags), "cn"),
    )

    geosite_rules: GeoSiteRules = {}
    for upstream_tag, output_tag, extra_domains, extra_suffixes in rule_tags:
        domain, domain_suffix, domain_keyword, domain_regex = upstream_rules[
            upstream_tag
        ]
        domain.extend(extra_domains)
        domain_suffix.extend(extra_suffixes)
        geosite_rules[output_tag] = release(
            domain, domain_suffix, domain_keyword, domain_regex, output_tag
        )

    # `china`: v2fly `cn` merged with felixonmars dnsmasq-china-list
    # (same recipe as Loyalsoldier's geosite:china-list, ~110k domains).
    cn_domain, cn_suffix, cn_keyword, cn_regex = upstream_rules["cn"]
    dq_domain, dq_suffix = parse_dnsmasq_china_list(DNSMASQ_CHINA_LIST_URL)
    geosite_rules["china"] = release(
        [*cn_domain, *dq_domain],
        [*cn_suffix, *dq_suffix],
        cn_keyword,
        cn_regex,
        "china",
    )

    # `ads` / `ads-mini`: hagezi Multi PRO / PRO mini blocklists
    # (ads, trackers, telemetry, malware, phishing, …). Both rebuild
    # daily from the upstream lists via CI.
    ads_domain, ads_suffix = parse_hagezi_onlydomains(HAGEZI_ADS_URL)
    geosite_rules["ads"] = release(
        ads_domain, ads_suffix, [], [], "ads"
    )
    ads_mini_domain, ads_mini_suffix = parse_hagezi_onlydomains(
        HAGEZI_ADS_MINI_URL
    )
    geosite_rules["ads-mini"] = release(
        ads_mini_domain, ads_mini_suffix, [], [], "ads-mini"
    )

    for output_tag, source_path in LOCAL_TAG_SOURCES.items():
        domain, domain_suffix = parse_local_domain_list(source_path)
        geosite_rules[output_tag] = release(
            domain, domain_suffix, [], [], output_tag
        )

    gfwlist_rules = parse_gfwlist(
        "https://raw.githubusercontent.com/gfwlist/gfwlist/master/list.txt"
    )
    gfwlist_rules["gfw-skip"][0].extend(DIRECT_DOMAIN)
    gfwlist_rules["gfw-skip"][1].extend(DIRECT_DOMAIN_SUFFIX)
    for tag, quanx_policy in (("gfw", "proxy"), ("gfw-skip", "direct")):
        gfwlist_rules[tag] = release(
            *gfwlist_rules[tag], tag, quanx_policy=quanx_policy
        )

    release_geosite_files(geosite_rules, gfwlist_rules)
    validate_outputs((*GEOSITE_TAGS, *GFWLIST_TAGS))


if __name__ == "__main__":
    main()
