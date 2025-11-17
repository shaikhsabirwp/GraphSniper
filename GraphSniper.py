#!/usr/bin/env python3

import subprocess
import argparse
import os
import re
import json
import time
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, OrderedDict

# third-party
import requests
import jsbeautifier
from graphql import parse, print_ast
from graphql.error import GraphQLError


# -----------------------
# Configuration
# -----------------------
DEFAULT_WORKERS = 20          # Faster
REQUEST_TIMEOUT = 8
JS_SAVE_DIR = "js_files"
OUTPUT_DIR = "graphql_output"


# -----------------------
# Command Runner
# -----------------------
def run_cmd(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL) or ""
    except:
        return ""


def sanitize_domain(raw):
    d = raw.strip()
    d = re.sub(r"^https?://", "", d)
    return d.split("/")[0]


# -----------------------
# URL Download
# -----------------------
def fetch_url(url, timeout=REQUEST_TIMEOUT, retries=2):
    headers = {"User-Agent": "Mozilla/5.0"}
    for _ in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r.text
        except:
            time.sleep(0.15)
    return None


# -----------------------
# GraphQL Extraction
# -----------------------
OP_NAME_RE = re.compile(
    r'\b(query|mutation)\s+([A-Za-z0-9_]+)\s*(\([^)]*\))?\s*{',
    re.IGNORECASE
)

ENDPOINT_CANDIDATE_RE = re.compile(
    r'["\']([^"\']*graphql[^"\']*)["\']',
    re.IGNORECASE
)


def balanced_brace_extract(js, start):
    depth = 0
    i = start
    L = len(js)

    while i < L:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[start:i+1]
        i += 1

    return None


def extract_named_operations(js):
    queries = OrderedDict()
    mutations = OrderedDict()

    for m in OP_NAME_RE.finditer(js):
        op_type = m.group(1).lower()
        op_name = m.group(2)

        pos = js.find("{", m.end() - 1)
        if pos == -1:
            continue

        block = balanced_brace_extract(js, pos)
        if not block:
            continue

        raw = js[m.start():pos] + block

        if len(raw) > 30000:
            continue

        if op_type == "query" and op_name not in queries:
            queries[op_name] = raw
        elif op_type == "mutation" and op_name not in mutations:
            mutations[op_name] = raw

    return queries, mutations


# -----------------------
# Strict Endpoint Cleaner
# -----------------------
def looks_like_endpoint(s):
    s = s.strip()
    if len(s) < 5:
        return False
    if any(c in s for c in "{}();=<>\t\n"):
        return False
    if s.startswith(("http://", "https://", "//", "/")) and "graphql" in s.lower():
        return True
    return False


def normalize_endpoint(s):
    if s.startswith("//"):
        return "https:" + s
    return s


def find_endpoints_strict(js):
    eps = []
    for ep in ENDPOINT_CANDIDATE_RE.findall(js):
        ep = ep.strip()
        if looks_like_endpoint(ep):
            eps.append(normalize_endpoint(ep))
    return list(dict.fromkeys(eps))


# -----------------------
# Print / Variables
# -----------------------
def parse_and_pretty(txt):
    try:
        ast = parse(txt)
        pretty = print_ast(ast).strip()

        vars_found = []
        for d in ast.definitions:
            if hasattr(d, "variable_definitions") and d.variable_definitions:
                for v in d.variable_definitions:
                    vars_found.append(v.variable.name.value)

        return pretty, vars_found

    except GraphQLError:
        tmp = txt.replace("\\n", "\n")
        tmp = re.sub(r'\s*{\s*', ' {\n  ', tmp)
        tmp = re.sub(r'\s*}\s*', '\n}', tmp)

        vars_found = [
            v.lstrip("$").split(":")[0]
            for v in re.findall(r'\$[A-Za-z0-9_]+\s*:', txt)
        ]
        return tmp, vars_found


# -----------------------
# MAIN
# -----------------------
def main():
    p = argparse.ArgumentParser()
    p.add_argument("target")
    p.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = p.parse_args()

    domain = sanitize_domain(args.target)
    print(f"[+] Scanning: {domain}")

    os.makedirs(JS_SAVE_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Collect JS URLs
    way = run_cmd(f"waybackurls {domain}")
    gau = run_cmd(f"gau {domain}")
    kat = run_cmd(f"katana -u https://{domain} -silent")

    urls = []
    for src in (way, gau, kat):
        urls.extend([u.strip() for u in src.splitlines() if ".js" in u])

    urls = list(dict.fromkeys(urls))
    print(f"[+] JS URLs found: {len(urls)}")

    if not urls:
        print("[-] No JS files found. Exiting.")
        return

    # Parallel download + extraction
    aggregated_queries = OrderedDict()
    aggregated_mutations = OrderedDict()
    endpoints_counter = Counter()

    def process_js(url):
        out = {"queries": {}, "mutations": {}, "ep": []}

        body = fetch_url(url)
        if not body:
            return out

        try:
            beautified = jsbeautifier.beautify(body)
        except:
            beautified = body

        # Save JS
        fname = os.path.basename(urlparse(url).path) or "file.js"
        fname = re.sub(r"[^A-Za-z0-9_.-]", "_", fname)
        with open(f"{JS_SAVE_DIR}/{fname}", "w") as f:
            f.write(beautified)

        q, m = extract_named_operations(beautified)
        eps = find_endpoints_strict(beautified)

        out["queries"] = q
        out["mutations"] = m
        out["ep"] = eps
        return out

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(process_js, u) for u in urls]

        for fut in as_completed(futures):
            res = fut.result()

            for ep in res["ep"]:
                endpoints_counter[ep] += 1

            for k, v in res["queries"].items():
                if k not in aggregated_queries:
                    aggregated_queries[k] = v

            for k, v in res["mutations"].items():
                if k not in aggregated_mutations:
                    aggregated_mutations[k] = v

    print(f"[+] Final Queries: {len(aggregated_queries)}")
    print(f"[+] Final Mutations: {len(aggregated_mutations)}")

    # Auto-select most common endpoint
    endpoint = None
    if endpoints_counter:
        endpoint = endpoints_counter.most_common(1)[0][0]

    # Build JSON output
    final_schema = OrderedDict()
    final_schema["endpoint"] = endpoint
    final_schema["queries"] = OrderedDict()
    final_schema["mutations"] = OrderedDict()

    for name, raw in aggregated_queries.items():
        pretty, vars_ = parse_and_pretty(raw)
        final_schema["queries"][name] = {"query": pretty, "variables": vars_}

    for name, raw in aggregated_mutations.items():
        pretty, vars_ = parse_and_pretty(raw)
        final_schema["mutations"][name] = {"query": pretty, "variables": vars_}

    out_path = f"{OUTPUT_DIR}/{domain}_graphql_schema.json"
    with open(out_path, "w") as f:
        json.dump(final_schema, f, indent=2)

    print(f"[+] Saved JSON schema to: {out_path}")
    print("[+] Done.")


if __name__ == "__main__":
    main()
