#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 dsh-plugin.org 的私有目录 API 转成 dsh format 1 索引。

WHY: 上游 api.dsh-plugin.org/plugins.zh.json 顶层是数组、字段压缩、且不带
     Access-Control-Allow-Origin，订阅方读不了；本脚本离线转成可托管的 format 1。
     条目字段规则按宿主 app.asar 里的 parseMarketIndex 实测对齐：
     version 必须是精确 SemVer、publishTimestamp 必须是整数、
     labels/sortKeys/category 用的 id 都必须在 metadata 里先声明、
     可选字段一旦存在就不能是空串。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import ssl
import sys
import urllib.request

API = "https://api.dsh-plugin.org/plugins.zh.json"

LANGUAGE_ID = re.compile(r"^[a-z]{2,8}(?:-[a-z0-9]{1,8})*$")
PACKAGE_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
SEMVER = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
                    r"(?:-(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
                    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*)?"
                    r"(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?$")
MAX_PUBLISH_TIMESTAMP = 100_000_000_000

CATEGORY_ZH = {
    "interface": "界面", "tools": "工具", "skills": "技能", "memory": "记忆知识",
    "providers": "模型接入", "usage": "用量与费用", "integrations": "集成",
    "appearance": "外观主题", "workflow": "工作流", "notify": "通知",
    "search": "联网搜索", "agents": "智能体编排", "dev": "开发工作流",
    "office": "办公文档", "creator": "内容创作", "skin": "皮肤",
    "security": "安全与权限", "mobile": "移动端", "media": "媒体视觉",
    "data": "数据处理", "other": "其他",
}

# 面板只会渲染声明过的标签；未声明的 id 直接判错，所以用到的必须在这里登记
LABEL_TITLES = {
    "verified": {"en": "Verified by upstream catalog", "zh": "上游目录标注已验证"},
    "star-gain": {"en": "Rising stars", "zh": "Star 上涨中"},
    "updated-recent": {"en": "Updated recently", "zh": "近期更新"},
}
SORTKEY_TITLES = {
    "stars": {"en": "GitHub stars", "zh": "GitHub Star 数", "order": "desc"},
    "forks": {"en": "GitHub forks", "zh": "GitHub Fork 数", "order": "desc"},
    "updated": {"en": "Catalog update time", "zh": "目录更新时间", "order": "desc"},
}


def load_source(src: str, timeout: float) -> list:
    """URL 走网络（失败退回同名本地缓存），本地路径直读。"""
    cache = os.path.basename(src.rstrip("/")) or "hub.json"
    if re.match(r"https?://", src):
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "hub2index/2.0"})
            with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
                blob = r.read()
            with open(cache, "wb") as fh:
                fh.write(blob)
        except Exception as exc:
            if not os.path.exists(cache):
                raise SystemExit(f"[x] 在线拉取失败（{type(exc).__name__}）且无本地缓存 {cache}")
            print(f"[!] 在线拉取失败（{type(exc).__name__}），改用本地缓存 {cache}", file=sys.stderr)
            blob = open(cache, "rb").read()
    else:
        with open(src, "rb") as fh:
            blob = fh.read()
    if not blob:
        raise SystemExit("[x] 输入为空")
    data = json.loads(blob.decode("utf-8-sig"))
    if not isinstance(data, list):
        raise SystemExit(f"[x] 期望顶层数组，实际 {type(data).__name__}（上游结构变了，别硬转）")
    return data


def to_ts(raw) -> int | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    for candidate in (text, text + "T00:00:00+00:00"):
        try:
            stamp = int(dt.datetime.fromisoformat(candidate).timestamp())
        except ValueError:
            continue
        if 0 <= stamp < MAX_PUBLISH_TIMESTAMP:
            return stamp
    return None


def exact_version(raw) -> str | None:
    """目录给的是 'v0.4.2' 这种；format 1 只收精确 SemVer，其它一律丢，不造假。"""
    if not isinstance(raw, str):
        return None
    v = raw.strip()
    if v[:1] in ("v", "V"):
        v = v[1:]
    return v if SEMVER.match(v) else None


def safe_localized(value: str, fallback: str) -> dict:
    """localizedText 要求 en 键存在且非空串；中文放 zh，语言码必须小写。"""
    text = (value or "").strip() or fallback
    return {"en": text, "zh": text}


def star_bucket(n) -> str | None:
    try:
        s = int(n)
    except (TypeError, ValueError):
        return None
    for lo, tag in ((10000, "stars-10000"), (5000, "stars-5000"), (1000, "stars-1000"),
                    (500, "stars-500"), (100, "stars-100"), (50, "stars-50")):
        if s >= lo:
            return tag
    return None


def npm_resolve(names: list[str], cache_path: str, workers: int = 16) -> dict:
    """目录没给版本号的条目，去 npm 取 dist-tags.latest 与 modified 时间。

    WHY: format 1 的 version 必须是精确 SemVer、publishTimestamp 必须是整数，
         缺了就不能发；宁可多花一次网络请求，也不编造版本号。
    """
    cache: dict = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cache = json.load(fh)
        except Exception:
            cache = {}
    todo = [n for n in names if n not in cache]
    if not todo:
        return cache
    import urllib.parse
    import urllib.error
    from concurrent.futures import ThreadPoolExecutor

    def one(name: str):
        url = "https://registry.npmjs.org/" + urllib.parse.quote(name, safe="@")
        req = urllib.request.Request(url, headers={
            "User-Agent": "hub2index/2.0",
            "Accept": "application/vnd.npm.install-v1+json"})
        try:
            with urllib.request.urlopen(req, timeout=30, context=ssl.create_default_context()) as r:
                doc = json.loads(r.read())
            return name, {"version": (doc.get("dist-tags") or {}).get("latest"),
                          "modified": doc.get("modified")}
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError, OSError):
            return name, None

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for name, value in pool.map(one, todo):
            cache[name] = value or {}
            done += 1
            if done % 400 == 0:
                print(f"    npm 解析进度 {done}/{len(todo)}", file=sys.stderr)
    try:
        with open(cache_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cache, fh, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        print(f"[!] 缓存写盘失败（不影响本次输出）：{exc}", file=sys.stderr)
    return cache


def convert(items: list, npm: dict | None = None) -> tuple[list, dict, dict]:
    stats = {"total": len(items), "no_name": 0, "bad_name": 0, "no_version": 0, "no_source": 0,
             "no_timestamp": 0, "npm": 0, "git": 0, "verified": 0, "deduped": 0, "enriched": 0}
    used_labels: set[str] = set()
    used_sorts: set[str] = set()
    used_cats: set[str] = set()
    best: dict[str, dict] = {}

    for it in items:
        if not isinstance(it, dict):
            continue
        repo = it.get("r") if isinstance(it.get("r"), dict) else {}
        npm_pkg = (repo.get("npmPackage") or "").strip()
        gh_repo = (repo.get("repo") or "").strip()
        name = npm_pkg or gh_repo
        if not name:
            stats["no_source"] += 1
            continue
        if not PACKAGE_NAME.match(name) or len(name) > 214:
            stats["bad_name"] += 1
            continue
        version = exact_version(it.get("vr"))
        stamp = to_ts(it.get("u"))
        if not version and npm is not None:
            info = npm.get(npm_pkg) or {}
            version = exact_version(info.get("version"))
            if version:
                stats["enriched"] += 1
                # npm 的 modified 是该包最后一次发布时间，比目录更新时间更接近面板要的东西
                stamp = to_ts(info.get("modified")) or stamp
        if not version:
            stats["no_version"] += 1
            continue
        if stamp is None:
            stats["no_timestamp"] += 1
            continue

        if npm_pkg:
            source = {"type": "npm", "registry": "https://registry.npmjs.org/"}
            stats["npm"] += 1
        else:
            source = {"type": "git", "url": f"https://github.com/{gh_repo}"}
            stats["git"] += 1

        cat = ((it.get("c") or "other").strip() or "other").lower()
        if not LANGUAGE_ID.match(cat) and not re.match(r"^[a-z0-9][a-z0-9._~-]*$", cat):
            cat = "other"
        used_cats.add(cat)

        labels = []
        if (it.get("v") or "").lower() == "verified":
            labels.append("verified")
            stats["verified"] += 1
        bucket = star_bucket(it.get("sg"))
        if bucket:
            labels.append(bucket)
            LABEL_TITLES.setdefault(bucket, {"en": bucket.replace("-", " ").title(),
                                             "zh": f"Star {bucket.split('-', 1)[1]}+"})
        used_labels.update(labels)

        sort_keys = {"stars": int(it.get("sg") or 0), "updated": stamp}
        if it.get("fk") is not None:
            sort_keys["forks"] = int(it["fk"])
        used_sorts.update(sort_keys)

        entry = {
            "name": name,
            "title": safe_localized(it.get("n"), name),
            "description": safe_localized(it.get("d"), name),
            "metadata": {
                "category": cat,
                "keywords": [str(x) for x in (it.get("t") or []) if isinstance(x, str)][:12],
                "labels": labels,
                "sortKeys": sort_keys,
            },
            "versions": [{"version": version, "publishTimestamp": stamp, "source": source}],
        }
        author = (it.get("o") or "").strip()
        if author:
            entry["author"] = author
        if gh_repo and "/" in gh_repo:
            entry["repository"] = {"url": f"https://github.com/{gh_repo}"}
        # 面板按包名匹配已安装项，同名条目必须只留时间最新的一条
        keep = best.get(name)
        if keep is None:
            best[name] = entry
        else:
            stats["deduped"] += 1
            if stamp > keep["versions"][0]["publishTimestamp"]:
                best[name] = entry

    plugins = sorted(best.values(), key=lambda p: (-p["metadata"]["sortKeys"]["stars"], p["name"]))
    metadata = {
        "categories": {c: {"title": {"en": c, "zh": CATEGORY_ZH.get(c, c)}} for c in sorted(used_cats)},
        "labels": {k: {"title": LABEL_TITLES[k]} for k in sorted(used_labels) if k in LABEL_TITLES},
        "sortKeys": {k: {"title": {kk: vv for kk, vv in v.items() if kk != "order"}, "order": v["order"]}
                     for k, v in SORTKEY_TITLES.items() if k in used_sorts},
    }
    return plugins, stats, metadata


def main() -> int:
    ap = argparse.ArgumentParser(description="dsh-plugin.org 私有目录 → format 1 索引转换器")
    ap.add_argument("--input", default=API)
    ap.add_argument("--out", default="index.json")
    ap.add_argument("--gzip", action="store_true")
    ap.add_argument("--validate", action="store_true", help="写完立刻用同目录的校验器复检")
    ap.add_argument("--enrich", action="store_true", help="目录缺版本号的条目去 npm 查 latest（慢，但条目全）")
    ap.add_argument("--npm-cache", default="npm-cache.json")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    try:
        items = load_source(args.input, 90.0)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[x] 读取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    npm = None
    if args.enrich:
        need = sorted({(it.get("r") or {}).get("npmPackage", "").strip()
                       for it in items
                       if isinstance(it, dict) and isinstance(it.get("r"), dict)
                       and not exact_version(it.get("vr")) and (it.get("r") or {}).get("npmPackage")})
        print(f"[*] 目录缺精确版本的包 {len(need)} 个，解析 npm dist-tags…", file=sys.stderr)
        npm = npm_resolve(list(need), args.npm_cache, args.workers)

    plugins, stats, metadata = convert(items, npm)
    doc = {
        "format": 1,
        "title": {"en": "DSH Plugin Hub catalog (converted)", "zh": "DSH Plugin Hub 目录（转换）"},
        "description": {
            "en": "Converted from api.dsh-plugin.org. publishTimestamp is the upstream catalog update "
                  "time, not the npm publish time. Entries whose catalog record carries no exact "
                  "version are dropped rather than guessed. 'verified' is the upstream label, not an "
                  "install test by this index.",
            "zh": "由 api.dsh-plugin.org 私有目录转换。条目时间是目录更新时间而非 npm 发布时间；"
                  "目录里没给精确版本号的条目直接丢弃而不是编造；verified 是上游标注，本索引未做安装实测。",
        },
        "metadata": metadata,
        "plugins": plugins,
    }
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"[+] {args.out}  {len(text.encode('utf-8'))/1048576:.2f} MB (UTF-8) | 条目 {len(plugins)}")
    print("    源统计:", json.dumps(stats, ensure_ascii=False))
    print("    声明:", json.dumps({k: len(v) for k, v in metadata.items()}, ensure_ascii=False))
    if args.gzip:
        with gzip.open(args.out + ".gz", "wb", compresslevel=9) as fh:
            fh.write(text.encode("utf-8"))
        print(f"[+] {args.out}.gz {os.path.getsize(args.out + '.gz')/1048576:.2f} MB")
    if args.validate:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, here)
        import validate_index
        code = validate_index.run(args.out, "https://example.invalid/index.json")
        if code != 0:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
