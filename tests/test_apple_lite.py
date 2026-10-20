import os
import tempfile
import unittest
from pathlib import Path

from main import (
    APPLE_LITE_SOURCE,
    GEOSITE_TAGS,
    parse_loon_list,
    parse_loon_list_text,
    release_loon_file,
    validate_loon_file,
)


class ParseLoonListTests(unittest.TestCase):
    def test_parses_all_rule_types(self):
        content = (
            "# comment line\n"
            "\n"
            "DOMAIN,exact.example.com\n"
            "DOMAIN-SUFFIX,example.com\n"
            "DOMAIN-KEYWORD,example.akadns.net\n"
            "DOMAIN-SUFFIX,example.com\n"  # duplicate suffix
            "IP-CIDR,17.0.0.0/8,no-resolve\n"
            "IP-CIDR6,2620:149::/32,no-resolve\n"
        )
        domain, domain_suffix, domain_keyword, ip_cidr = parse_loon_list_text(
            content.encode("utf-8")
        )
        self.assertEqual(domain, ["exact.example.com"])
        self.assertEqual(domain_suffix, ["example.com"])
        self.assertEqual(domain_keyword, ["example.akadns.net"])
        self.assertEqual(ip_cidr, ["17.0.0.0/8", "2620:149::/32"])

    def test_rejects_unknown_rule_type(self):
        with self.assertRaisesRegex(ValueError, "Unsupported Loon rule type"):
            parse_loon_list_text(b"URL-REGEX,example\n")

    def test_rejects_ipv6_written_as_ip_cidr(self):
        with self.assertRaisesRegex(ValueError, "IPv4"):
            parse_loon_list_text(b"IP-CIDR,2620:149::/32,no-resolve\n")

    def test_rejects_invalid_cidr(self):
        with self.assertRaisesRegex(ValueError, "Invalid Loon CIDR"):
            parse_loon_list_text(b"IP-CIDR,999.0.0.0/8,no-resolve\n")

    def test_rejects_missing_value(self):
        with self.assertRaisesRegex(ValueError, "Invalid Loon rule line"):
            parse_loon_list_text(b"DOMAIN-SUFFIX\n")


class AppleLiteSourceTests(unittest.TestCase):
    def test_tag_registered(self):
        self.assertIn("apple-lite", GEOSITE_TAGS)

    def test_source_file_parses_to_the_lite_set(self):
        self.assertTrue(os.path.isfile(APPLE_LITE_SOURCE))
        domain, domain_suffix, domain_keyword, ip_cidr = parse_loon_list(
            APPLE_LITE_SOURCE
        )
        total = len(domain) + len(domain_suffix) + len(domain_keyword) + len(ip_cidr)
        # The whole point of the tag: two orders of magnitude smaller
        # than the full v2fly `apple` list (~1.6k rules). Guard both
        # directions so it cannot silently balloon or empty out.
        self.assertGreaterEqual(total, 30)
        self.assertLessEqual(total, 40)
        joined_suffix = "\n".join(domain_suffix)
        for expected in (
            "apple.com",
            "icloud.com",
            "mzstatic.com",
            "aaplimg.com",
            "cdn-apple.com",
        ):
            self.assertIn(expected, joined_suffix)
        self.assertEqual(len(domain_keyword), 3)
        self.assertIn("17.0.0.0/8", ip_cidr)


class LoonOutputTests(unittest.TestCase):
    def test_loon_output_round_trips_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path.cwd()
            try:
                Path(directory, "dist").mkdir()
                os.chdir(directory)
                release_loon_file(
                    "apple-lite",
                    ["exact.example.com"],
                    ["example.com"],
                    ["example.akadns.net"],
                    ["17.0.0.0/8", "2620:149::/32"],
                )
                validate_loon_file("dist/apple-lite-loon.list")
                content = Path("dist/apple-lite-loon.list").read_text().splitlines()
                self.assertEqual(
                    content,
                    [
                        "DOMAIN,exact.example.com",
                        "DOMAIN-SUFFIX,example.com",
                        "DOMAIN-KEYWORD,example.akadns.net",
                        "IP-CIDR,17.0.0.0/8,no-resolve",
                        "IP-CIDR6,2620:149::/32,no-resolve",
                    ],
                )
            finally:
                os.chdir(current)

    def test_loon_validator_rejects_policy_suffix(self):
        # Loon remote rules must not embed a policy; it is assigned
        # at subscription time.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "tag-loon.list")
            path.write_text("DOMAIN-SUFFIX,example.com,DIRECT\n")
            with self.assertRaisesRegex(ValueError, "invalid Loon line"):
                validate_loon_file(str(path))


if __name__ == "__main__":
    unittest.main()
