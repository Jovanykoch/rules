// mmdb-build: generate a minimal MaxMind-format country database
// (chnroutes.mmdb) from a plain-text CIDR list, e.g. the cn.txt
// published by Loyalsoldier/geoip.
//
// Each line holds one CIDR ("1.0.1.0/24"), as published by
// Loyalsoldier/geoip. Tokens are also split on commas, so a
// comma-separated line still parses instead of being skipped.
//
// Every network in the list is mapped to country CN; any address
// not covered has no record. That makes Surge's GEOIP,CN rule match
// exactly the listed China ranges and nothing else.
//
// Usage: mmdb-build <cidr-list.txt> <out.mmdb>
package main

import (
	"bufio"
	"fmt"
	"net"
	"os"
	"strings"

	"github.com/maxmind/mmdbwriter"
	"github.com/maxmind/mmdbwriter/inserter"
	"github.com/maxmind/mmdbwriter/mmdbtype"
)

// cnRecord mirrors the GeoLite2-Country record layout so readers
// (Surge GEOIP rules, geoip2 libraries, ...) see a familiar structure.
// Only country.iso_code is actually used for GEOIP matching.
func cnRecord() mmdbtype.DataType {
	return mmdbtype.Map{
		"country": mmdbtype.Map{
			"geoname_id":           mmdbtype.Uint32(1814991),
			"is_in_european_union": mmdbtype.Bool(false),
			"iso_code":             mmdbtype.String("CN"),
			"names": mmdbtype.Map{
				"de":    mmdbtype.String("China"),
				"en":    mmdbtype.String("China"),
				"es":    mmdbtype.String("China"),
				"fr":    mmdbtype.String("Chine"),
				"ja":    mmdbtype.String("中国"),
				"pt-BR": mmdbtype.String("China"),
				"ru":    mmdbtype.String("China"),
				"zh-CN": mmdbtype.String("中国"),
			},
		},
	}
}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, format+"\n", args...)
	os.Exit(1)
}

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: mmdb-build <cidr-list.txt> <out.mmdb>")
		os.Exit(2)
	}

	writer, err := mmdbwriter.New(mmdbwriter.Options{
		DatabaseType: "GeoLite2-Country",
		RecordSize:   24,
		Languages:    []string{"de", "en", "es", "fr", "ja", "pt-BR", "ru", "zh-CN"},
		Description: map[string]string{
			"en": "China (CN) IP ranges database",
		},
	})
	if err != nil {
		fail("mmdbwriter.New: %v", err)
	}

	in, err := os.Open(os.Args[1])
	if err != nil {
		fail("open input: %v", err)
	}
	defer in.Close()

	record := cnRecord() // immutable: safe to reuse for every insert
	inserted := 0
	scanner := bufio.NewScanner(in)
	scanner.Buffer(make([]byte, 1024*1024), 1024*1024)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		// Split on commas: the upstream file packs many CIDRs per
		// line. A line without commas yields a single token, so
		// one-CIDR-per-line inputs keep working.
		for _, tok := range strings.Split(line, ",") {
			tok = strings.TrimSpace(tok)
			if tok == "" || strings.HasPrefix(tok, "#") {
				continue
			}
			_, network, err := net.ParseCIDR(tok)
			if err != nil {
				fmt.Fprintln(os.Stderr, "skip invalid CIDR:", tok)
				continue
			}
			// ReplaceWith keeps the result deterministic when input
			// ranges overlap each other.
			if err := writer.InsertFunc(network, inserter.ReplaceWith(record)); err != nil {
				fail("insert %s: %v", network, err)
			}
			inserted++
		}
	}
	if err := scanner.Err(); err != nil {
		fail("read input: %v", err)
	}
	if inserted == 0 {
		// Never publish an empty database: it would make every
		// GEOIP,CN lookup miss. Fail loudly instead so a bad
		// download or a format change is noticed immediately.
		fail("no networks inserted from %s: refusing to write an empty database", os.Args[1])
	}

	out, err := os.Create(os.Args[2])
	if err != nil {
		fail("create output: %v", err)
	}
	if _, err := writer.WriteTo(out); err != nil {
		fail("write mmdb: %v", err)
	}
	if err := out.Close(); err != nil {
		fail("close output: %v", err)
	}

	fmt.Printf("inserted %d networks -> %s\n", inserted, os.Args[2])
}
