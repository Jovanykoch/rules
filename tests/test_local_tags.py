import os
import tempfile
import unittest

from main import (
    GEOSITE_TAGS,
    LOCAL_TAG_SOURCES,
    parse_hagezi_onlydomains_text,
    parse_local_domain_list,
)


class ParseHageziOnlydomainsTests(unittest.TestCase):
    def test_parses_bare_domains_as_suffixes(self):
        content = (
            "# Title: HaGeZi's Multi PRO\n"
            "\n"
            "ads.example.com\n"
            "TRACKER.EXAMPLE.ORG\n"
            "ads.example.com\n"  # duplicate
        )
        domain, domain_suffix = parse_hagezi_onlydomains_text(
            content.encode("utf-8")
        )
        self.assertEqual(domain, [])
        self.assertEqual(domain_suffix, ["ads.example.com", "tracker.example.org"])


class ParseLocalDomainListTests(unittest.TestCase):
    def test_parses_suffix_exact_comments_and_blanks(self):
        content = (
            "# comment line\n"
            "\n"
            ".example.com\n"
            "exact.example.org\n"
            "   # indented comment\n"
            ".example.com\n"  # duplicate suffix
            "exact.example.org\n"  # duplicate exact
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".list", delete=False, encoding="utf-8"
        ) as f:
            f.write(content)
            path = f.name
        try:
            domain, domain_suffix = parse_local_domain_list(path)
        finally:
            os.unlink(path)
        self.assertEqual(domain, ["exact.example.org"])
        self.assertEqual(domain_suffix, ["example.com"])

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            parse_local_domain_list("/nonexistent/path.list")


class LocalTagConfigTests(unittest.TestCase):
    def test_new_tags_registered(self):
        self.assertIn("ai", GEOSITE_TAGS)
        self.assertIn("streaming-cn", GEOSITE_TAGS)
        self.assertIn("apple", GEOSITE_TAGS)
        self.assertIn("douyin", GEOSITE_TAGS)
        self.assertIn("china", GEOSITE_TAGS)
        self.assertIn("ads", GEOSITE_TAGS)
        self.assertIn("ads-mini", GEOSITE_TAGS)

    def test_local_sources_point_at_existing_files(self):
        for tag, path in LOCAL_TAG_SOURCES.items():
            with self.subTest(tag=tag):
                self.assertTrue(
                    os.path.isfile(path), f"source file missing: {path}"
                )
                domain, domain_suffix = parse_local_domain_list(path)
                self.assertTrue(
                    domain or domain_suffix,
                    f"source file {path} produced no domains",
                )

    def test_streaming_cn_entries_are_valid_domain_suffixes(self):
        path = LOCAL_TAG_SOURCES["streaming-cn"]
        _, domain_suffix = parse_local_domain_list(path)
        self.assertGreater(len(domain_suffix), 10)
        for suffix in domain_suffix:
            with self.subTest(suffix=suffix):
                self.assertRegex(suffix, r"^[a-z0-9.-]+\.[a-z]{2,}$")
                self.assertFalse(suffix.startswith("."))
        # Spot-check services the tag is meant to cover.
        joined = "\n".join(domain_suffix)
        for expected in (
            "douyin.com",
            "music.163.com",
            "bilibili.com",
            "iqiyi.com",
            "kuaishou.com",
        ):
            self.assertIn(expected, joined)


if __name__ == "__main__":
    unittest.main()
