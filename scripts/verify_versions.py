#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐个核对索引里声明的版本在 npm 上是否真的存在，不存在就退回 latest，包本身没了就丢条目。

WHY: 上游目录的 vr 字段会过期（实测抽样 20 条里 1 条指向 npm 上不存在的版本），
     面板按索引声明的版本装，版本不存在就是安装失败；这一步把这种条目修干净。
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

SEMVER = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
                    r"(?:-(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
                    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*)?"
                    r"(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?$")


def load_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def npm_doc(name: str, timeout: float = 30.0):
    url = "https://registry.npmjs.org/" + urllib.parse.quote(name, safe="@")
    req = urllib.request.Request(url, headers={"User-Agent": "verify-versions/1.0",
                                              "Accept": "application/vnd.npm.install-v1+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return None if exc.code == 404 else {"_retry": True}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {"_retry": True}


def main() -> int:
    ap = argparse.ArgumentParser(description="核对索引声明版本在 npm 上是否存在")
    ap.add_argument("index", nargs="?", default="index.json")
    ap.add_argument("--cache", default="npm-verify.json")
    ap.add_argument("--out", default="index.verified.json")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--report", default="verify-report.json")
    args = ap.parse_args()

    try:
        with open(args.index, encoding="utf-8-sig") as fh:
            doc = json.load(fh)
    except Exception as exc:
        print(f"[x] 读索引失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    plugins = doc.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        print("[x] 索引里没有 plugins 数组", file=sys.stderr)
        return 2

    cache = load_cache(args.cache)
    doc_cache: dict[str, dict] = {}

    def key(name: str, version: str) -> str:
        return f"{name}@{version}"

    def check(entry):
        name = entry["name"]
        version = entry["versions"][0]["version"]
        k = key(name, version)
        if k in cache:
            hit = cache[k]
            # 缓存存的是 {verdict, latest}；早先这里直接把 dict 当 verdict 返回，
            # 结果二跑时全部落到 drop 分支——判据必须是字符串 verdict
            if isinstance(hit, dict):
                return name, version, hit.get("verdict", "missing")
            return name, version, str(hit)
        meta = doc_cache.get(name)
        if meta is None:
            meta = npm_doc(name) or {}
            if meta.get("_retry"):
                return name, version, "retry"
            doc_cache[name] = meta
        if not meta:
            verdict = "nopkg"
        elif version in (meta.get("versions") or {}):
            verdict = "ok"
        else:
            verdict = "missing"
        cache[k] = {"verdict": verdict, "latest": (meta.get("dist-tags") or {}).get("latest")}
        return name, version, verdict

    fixed = {"ok": 0, "fallback": 0, "dropped": 0, "retry": 0, "bad_semver": 0}
    detail = []
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(check, plugins))
    by_name = {p["name"]: p for p in plugins}
    for name, version, verdict in results:
        entry = by_name.get(name)
        if entry is None:
            continue
        if verdict == "retry":
            fixed["retry"] += 1
            detail.append({"name": name, "action": "retry", "version": version})
            continue
        if verdict == "ok":
            fixed["ok"] += 1
            continue
        latest = (cache.get(key(name, version)) or {}).get("latest")
        if verdict == "missing" and isinstance(latest, str) and SEMVER.match(latest or ""):
            entry["versions"][0]["version"] = latest
            fixed["fallback"] += 1
            detail.append({"name": name, "action": "fallback", "from": version, "to": latest})
        else:
            fixed["dropped"] += 1
            detail.append({"name": name, "action": "drop", "version": version, "verdict": verdict})

    kept = [p for p in plugins
            if p["name"] not in {d["name"] for d in detail if d["action"] == "drop"}]
    for p in kept:
        v = p["versions"][0]["version"]
        if not SEMVER.match(v):
            fixed["bad_semver"] += 1
    doc["plugins"] = kept
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    with open(args.cache, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cache, fh, ensure_ascii=False, separators=(",", ":"))
    with open(args.report, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(detail, fh, ensure_ascii=False, indent=1)
    print(f"[+] {args.out} 条目 {len(kept)}（{len(text.encode('utf-8'))/1048576:.2f} MB）")
    print("    统计:", json.dumps(fixed, ensure_ascii=False))
    if fixed["retry"]:
        print("    [!] 有条目因网络失败未判定，重跑一次本脚本即可（结果已缓存）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
