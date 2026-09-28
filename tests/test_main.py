import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from main import parse_dlc_plain, parse_gfwlist_text, parse_dnsmasq_china_list_text, release_quanx_file


class ParseDLCTests(unittest.TestCase):
    def test_collects_ads_and_keeps_shared_cn_rules_in_both_lists(self):
        ad_rules = [
            "full:shared.example:@cn,@ads",
            "domain:ads.example:@ads,@other",
            "keyword:advertising:@ads",
            r"regexp:^ads[0-9]+\.example$:@ads",
        ]
        source = {
            "lists": [
                {"name": "category-ads-all", "rules": ["domain:base.example"]},
                {"name": "geolocation-cn", "rules": []},
                {
                    "name": "vendor",
                    "rules": ad_rules
                    + [
                        "full:negated.example:@!ads",
                        "full:similar.example:@ads2",
                        "full:untagged.example",
                    ],
                },
                {"name": "duplicate", "rules": ad_rules},
            ]
        }
        for tags in (("category-ads-all",), ("category-ads-all", "geolocation-cn")):
            with (
                self.subTest(tags=tags),
                patch(
                    "main.fetch_url_bytes",
                    return_value=yaml.safe_dump(source).encode(),
                ),
            ):
                rules = parse_dlc_plain("fixture", tags)
            self.assertEqual(
                rules["category-ads-all"],
                (
                    ["shared.example"],
                    ["base.example", "ads.example"],
                    ["advertising"],
                    [r"^ads[0-9]+\.example$"],
                ),
            )
            if "geolocation-cn" in tags:
                self.assertEqual(
                    rules["geolocation-cn"], (["shared.example"], [], [], [])
                )

    def test_collects_cn_from_all_lists_and_deduplicates_each_rule_type(self):
        apple = "full:init-p01st.push.apple.com:@cn"
        source = {
            "lists": [
                {"name": "geolocation-cn", "rules": ["domain:base.cn", apple]},
                {"name": "geolocation-!cn", "rules": ["domain:apple.com"]},
                {
                    "name": "apple",
                    "rules": [
                        apple,
                        "full:extra.example:@ads,@cn",
                        "domain:cdn.example:@cn,@other",
                        "keyword:china-service:@cn",
                        r"regexp:^cn[0-9]+\.example$:@cn",
                        "full:foreign.example:@!cn",
                        "full:similar.example:@cn2",
                        "full:untagged.example",
                    ],
                },
                {
                    "name": "duplicate",
                    "rules": [
                        apple,
                        "keyword:china-service:@cn",
                        r"regexp:^cn[0-9]+\.example$:@cn",
                    ],
                },
            ]
        }
        with patch(
            "main.fetch_url_bytes", return_value=yaml.safe_dump(source).encode()
        ):
            rules = parse_dlc_plain("fixture", ("geolocation-cn", "geolocation-!cn"))
        self.assertEqual(
            rules["geolocation-cn"],
            (
                ["init-p01st.push.apple.com", "extra.example"],
                ["base.cn", "cdn.example"],
                ["china-service"],
                [r"^cn[0-9]+\.example$"],
            ),
        )
        self.assertEqual(rules["geolocation-!cn"], ([], ["apple.com"], [], []))

    def test_missing_cn_list_is_still_an_error(self):
        source = b'lists:\n- name: apple\n  rules: ["full:example.com:@cn"]\n'
        with (
            patch("main.fetch_url_bytes", return_value=source),
            self.assertRaisesRegex(ValueError, "Missing DLC tags: geolocation-cn"),
        ):
            parse_dlc_plain("fixture", ("geolocation-cn",))


class ParseGFWListTests(unittest.TestCase):
    def test_converts_current_autoproxy_rule_forms(self) -> None:
        source = r"""[AutoProxy 0.2.9]
! metadata
||example.com
||cdn*.assets.example/path
|https://exact.example/a/path
|http://*.wild.example/
plain.example
/^https?:\/\/[^\/]+blogspot\.(.*)/
@@||direct.example
@@/^https?:\/\/(?=.*?(2x3|ni5|j5o))[a-z0-9.-]+\.xn--ngstr-lra8j\.com$
"""

        rules = parse_gfwlist_text(source.encode())
        domain, domain_suffix, domain_keyword, domain_regex = rules["gfw"]

        self.assertEqual(domain, ["exact.example", "plain.example"])
        self.assertEqual(
            domain_suffix, ["example.com", "assets.example", "wild.example"]
        )
        self.assertEqual(domain_keyword, [])
        self.assertEqual(domain_regex, [r"[^\/]+blogspot\.(.*)"])
        self.assertEqual(rules["gfw-skip"][1], ["direct.example"])
        self.assertEqual(
            rules["gfw-skip"][3],
            [r"^[a-z0-9.-]*(?:2x3|ni5|j5o)[a-z0-9.-]*\.xn--ngstr-lra8j\.com$"],
        )

    def test_rejects_non_gfwlist_data(self) -> None:
        with self.assertRaisesRegex(ValueError, "header"):
            parse_gfwlist_text(b"not a gfwlist")

    def test_drops_regexes_targeting_url_paths(self) -> None:
        source = (
            "[AutoProxy 0.2.9]\n"
            r"/^https?:\/\/example\.com\/ads\/banner/"
            "\n"
            r"/^https?:\/\/[^\/]+blogspot\.(.*)/"
            "\n"
        )
        with self.assertLogs("main", level="WARNING") as logs:
            rules = parse_gfwlist_text(source.encode())
        _, _, _, domain_regex = rules["gfw"]
        # The path-matching regex is dropped; the [^\/] idiom is kept.
        self.assertEqual(domain_regex, [r"[^\/]+blogspot\.(.*)"])
        self.assertTrue(
            any("matches URL paths" in message for message in logs.output)
        )

    def test_gfw_quanx_rules_use_proxy_policy(self) -> None:
        previous_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            os.chdir(directory)
            try:
                Path("dist").mkdir()
                release_quanx_file(
                    "gfw", ["exact.example"], ["suffix.example"], [], "proxy"
                )
                output = Path("dist/gfw.quanx").read_text()
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(
            output,
            "host, exact.example, proxy\nhost-suffix, suffix.example, proxy\n",
        )


class ParseDnsmasqChinaListTests(unittest.TestCase):
    def test_parses_server_lines_as_suffixes(self) -> None:
        source = (
            "server=/example.com/114.114.114.114\n"
            "server=/Sub.Example.CN/223.5.5.5\n"
            "#server=/disabled.example/114.114.114.114 # Disabled\n"
            "\n"
            "server=/example.com/114.114.114.114\n"
        )
        domain, suffix = parse_dnsmasq_china_list_text(source.encode())
        self.assertEqual(domain, [])
        self.assertEqual(suffix, ["example.com", "sub.example.cn"])


if __name__ == "__main__":
    unittest.main()
