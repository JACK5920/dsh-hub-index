#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 dsh-plugin.org 的私有目录 API 转成 dsh「自定义插件市场」能读的 format 1 索引。

WHY: 官方只发 plugins.zh.json 这种压缩字段（s/o/n/vr/c/t/r/ic/v/u/sg/fk），
     不是 format 1，且响应不带 Access-Control-Allow-Origin，填进索引源面板必然失败。
     这里离线转换成本地 index.json，再由你自己托管（GitHub Pages / 任意静态 HTTPS）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.dsh-plugin.org/plugins.zh.json"

# 目录里出现过的分类码 → 面板显示名（未知分类原样保留，不造假名）
CATEGORY_ZH = {
    "interface": "界面", "tools": "工具", "skills": "技能", "memory": "记忆知识",
    "providers": "模型接入", "usage": "用量与费用", "integrations": "集成",
    "appearance": "外观主题", "workflow": "工作流", "notify": "通知",
    "search": "联网搜索", "agents": "智能体编排", "dev": "开发工作流",
    "office": "办公文档", "creator": "内容创作", "skin": "皮肤",
    "security": "安全与权限", "mobile": "移动端", "media": "媒体视觉",
    "data": "数据处理", "other": "其他",
}


def load_source(src: str, timeout: float) -> list:
    """URL 走网络（失败退回同名本地缓存），本地路径直读。"""
    if re.match(r"https?://", src):
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "hub2index/1.0"})
            with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
                blob = r.read()
            cache = src.rstrip("/").rsplit("/", 1)[-1] or "hub.json"
            with open(cache, "wb") as fh:
                fh.write(blob)
        except Exception as exc:
            fallback = src.rstrip("/").rsplit("/", 1)[-1]
            try:
                with open(fallback, "rb") as fh:
                    blob = fh.read()
                print(f"[!] 在线拉取失败（{type(exc).__name__}），改用本地缓存 {fallback}", file=sys.stderr)
            except Exception as exc2:
                raise SystemExit(f"[x] 在线与本地缓存都读不到：{exc} / {exc2}")
    else:
        with open(src, "rb") as fh:
            blob = fh.read()
    if not blob:
        raise SystemExit("[x] 输入为空")
    data = json.loads(blob.decode("utf-8-sig"))
    if not isinstance(data, list):
        raise SystemExit(f"[x] 期望顶层是数组，实际是 {type(data).__name__}（API 结构变了，别硬转）")
    return data


def to_ts(raw) -> int | None:
    """把 '2026-10-11T12:25:55Z' / '2026-08-22' 转成秒级时间戳；解析不出返回 None。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    for candidate in (text, text + "T00:00:00+00:00"):
        try:
            return int(dt.datetime.fromisoformat(candidate).timestamp())
        except ValueError:
            continue
    return None


def clean_version(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    v = raw.strip().lstrip("vV")
    return v if re.match(r"^\d+\.\d+(?:\.\d+)?(?:[-+][\w.+-]+)?$", v) else None


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


def convert(items: list, require_version: bool) -> tuple[list, dict]:
    plugins, stats = [], {"total": len(items), "no_name": 0, "no_source": 0,
                          "no_version": 0, "skipped": 0, "npm": 0, "git": 0, "verified": 0}
    cats: dict[str, dict] = {}
    for it in items:
        if not isinstance(it, dict):
            stats["skipped"] += 1
            continue
        repo = it.get("r") if isinstance(it.get("r"), dict) else {}
        npm_pkg = (repo.get("npmPackage") or "").strip()
        gh_repo = (repo.get("repo") or "").strip()
        name = npm_pkg or gh_repo or (it.get("n") or "").strip() or (it.get("s") or "").strip()
        if not name:
            stats["no_name"] += 1
            stats["skipped"] += 1
            continue
        version = clean_version(it.get("vr"))
        if not version and require_version:
            stats["no_version"] += 1
            stats["skipped"] += 1
            continue
        source: dict | None = None
        if npm_pkg:
            source = {"type": "npm", "registry": "https://registry.npmjs.org/"}
            stats["npm"] += 1
        elif gh_repo:
            # 无 npm 包时退回 git 源；目录没给 commit，所以不钉版本（诚实标注，不假造 sha）
            source = {"type": "git", "url": f"https://github.com/{gh_repo}"}
            stats["git"] += 1
        if source is None:
            stats["no_source"] += 1
            continue
        cat = (it.get("c") or "other").strip() or "other"
        cats.setdefault(cat, {"title": {"en": cat, "zh": CATEGORY_ZH.get(cat, cat)}})
        labels = []
        if (it.get("v") or "").lower() == "verified":
            labels.append("verified")
            stats["verified"] += 1
        bucket = star_bucket(it.get("sg"))
        if bucket:
            labels.append(bucket)
        updated_ts = to_ts(it.get("u"))
        version_obj: dict = {}
        if version:
            version_obj["version"] = version
        if updated_ts:
            version_obj["publishTimestamp"] = updated_ts
        version_obj["source"] = source
        entry = {
            "name": name,
            "title": {"en": it.get("n") or name, "zh": it.get("n") or name},
            "description": {"zh": it.get("d") or "", "en": it.get("d") or ""},
            "author": it.get("o") or "",
            "repository": {"url": f"https://github.com/{gh_repo}"} if gh_repo else {},
            "metadata": {"category": cat,
                         "keywords": [str(x) for x in (it.get("t") or [])][:12],
                         "labels": labels,
                         "sortKeys": {"stars": it.get("sg") or 0}},
            "versions": [version_obj],
        }
        if it.get("f"):
            entry["metadata"]["keywords"] += [str(x) for x in it["f"]][:6]
        plugins.append(entry)
    stats["categories"] = len(cats)
    # 目录里同一 npm 包会被不同 slug 重复收录；面板按包名匹配已安装项，重名必须先去重
    best: dict[str, dict] = {}
    dropped = 0
    for p in plugins:
        keep = best.get(p["name"])
        if keep is None:
            best[p["name"]] = p
            continue
        dropped += 1
        new_ts = (p["versions"][0].get("publishTimestamp") or 0)
        old_ts = (keep["versions"][0].get("publishTimestamp") or 0)
        if new_ts > old_ts:
            best[p["name"]] = p
    stats["deduped"] = dropped
    return list(best.values()), stats


def main() -> int:
    ap = argparse.ArgumentParser(description="dsh-plugin.org 私有目录 → format 1 索引转换器")
    ap.add_argument("--input", default=API, help="API URL 或本地 JSON 路径")
    ap.add_argument("--out", default="hub-index.json")
    ap.add_argument("--require-version", action="store_true", help="丢弃目录里没给版本号的条目")
    ap.add_argument("--gzip", action="store_true", help="额外产出 .gz（托管时省带宽）")
    ap.add_argument("--check", action="store_true", help="转换后回读校验产物结构")
    args = ap.parse_args()

    try:
        items = load_source(args.input, 60.0)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[x] 读取失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    plugins, stats = convert(items, args.require_version)
    doc = {
        "format": 1,
        "title": {"en": "DSH Plugin Hub catalog (converted)", "zh": "DSH Plugin Hub 目录（本地转换）"},
        "description": {"en": "Converted from api.dsh-plugin.org private schema. publishTimestamp is the "
                              "catalog update time, not the npm publish time; git entries are unpinned "
                              "because the source provides no commit.",
                        "zh": "由 api.dsh-plugin.org 私有结构转换。条目时间是目录更新时间而非 npm 发布时间；"
                              "git 条目因源未提供 commit 而未钉版本。"},
        "metadata": {"categories": {c: {"title": {"en": c, "zh": CATEGORY_ZH.get(c, c)}}
                                    for c in sorted({p["metadata"]["category"] for p in plugins})}},
        "plugins": plugins,
    }
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    print(f"[+] {args.out}  {len(text.encode('utf-8'))/1048576:.2f} MB (UTF-8) | 条目 {len(plugins)}")
    print("    源统计:", json.dumps(stats, ensure_ascii=False))
    if args.gzip:
        with gzip.open(args.out + ".gz", "wb", compresslevel=9) as fh:
            fh.write(text.encode("utf-8"))
        import os
        print(f"[+] {args.out}.gz {os.path.getsize(args.out + '.gz')/1024/1024:.2f} MB")
    if args.check:
        with open(args.out, encoding="utf-8") as fh:
            back = json.load(fh)
        assert back["format"] == 1 and isinstance(back["plugins"], list)
        bad = [p["name"] for p in back["plugins"]
               if not p.get("versions") or not p["versions"][0].get("source")]
        dup = len(back["plugins"]) - len({p["name"] for p in back["plugins"]})
        print(f"[+] 校验：format=1 条目 {len(back['plugins'])} | 缺 source {len(bad)} | 重名 {dup}")
        if bad[:3]:
            print("    缺 source 示例:", bad[:3])
        print("    首条:", json.dumps(back["plugins"][0], ensure_ascii=False)[:300])
        if bad or dup:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
