#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按宿主 app.asar 里 parseMarketIndex 的规则校验 format 1 索引。

WHY: 面板的报错（如 `$.plugins[0].metadata.labels[0] references undeclared id`）
     来自这套校验；发布前本地跑一遍，免得把红叉留给用户去点。
     规则是从宿主二进制里读出来的，不是照文档猜的。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urlparse

LANGUAGE_ID = re.compile(r"^[a-z]{2,8}(?:-[a-z0-9]{1,8})*$")
PACKAGE_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
NUM = r"(?:0|[1-9]\d*)"
PRE = rf"(?:{NUM}|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
SEMVER = re.compile(rf"^{NUM}\.{NUM}\.{NUM}(?:-{PRE}(?:\.{PRE})*)?(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._@+-]+$")
MAX_PUBLISH_TIMESTAMP = 100_000_000_000
MAX_NAME = 214

errors: list[str] = []


def fail(path: str, reason: str) -> None:
    errors.append(f"{path} {reason}")


def is_obj(v) -> bool:
    return isinstance(v, dict)


def string(v, path):
    if not isinstance(v, str) or not v.strip():
        fail(path, "must be a string with a non-whitespace character")
        return None
    return v


def localized(v, path):
    if isinstance(v, str):
        return string(v, path)
    if not is_obj(v):
        fail(path, "must be an object")
        return None
    out = {}
    for lang, member in v.items():
        if not LANGUAGE_ID.match(lang):
            fail(f"{path}.{lang}", "must be keyed by a lowercase language id")
            continue
        if string(member, f"{path}.{lang}") is not None:
            out[lang] = member
    if "en" not in out:
        fail(path, "must contain en")
    return out


def url_check(v, path, protocols=("https:", "http:")):
    if not isinstance(v, str) or not v.strip():
        fail(path, "must be a non-empty string")
        return None
    parsed = urlparse(v)
    if not parsed.scheme or not parsed.netloc:
        fail(path, "must be an absolute URL")
        return None
    want = tuple(x.rstrip(":") for x in protocols)
    if parsed.scheme not in want:
        fail(path, f"must use {' or '.join(protocols)}")
    if parsed.username or parsed.password:
        fail(path, "must not contain credentials")
    if parsed.query or parsed.fragment:
        fail(path, "must not contain a query or fragment")
    return v


def declarations(obj, path, need_order=False):
    out = {}
    if not is_obj(obj):
        fail(path, "must be an object")
        return out
    for id_, member in obj.items():
        if id_ == "":
            fail(path, "must not declare an empty id")
            continue
        if not is_obj(member):
            fail(f"{path}.{id_}", "must be an object")
            continue
        localized(member.get("title"), f"{path}.{id_}.title")
        if need_order:
            if member.get("order") not in ("asc", "desc"):
                fail(f"{path}.{id_}.order", 'must be "asc" or "desc"')
        out[id_] = member
    return out


def check(doc, index_url: str) -> int:
    errors.clear()
    if not is_obj(doc):
        print("[x] 顶层必须是对象")
        return 1
    if doc.get("format") != 1:
        fail("$.format", "must be 1")
    localized(doc.get("title"), "$.title")
    if "description" in doc:
        localized(doc.get("description"), "$.description")

    meta = doc.get("metadata", {})
    if meta and not is_obj(meta):
        fail("$.metadata", "must be an object")
        meta = {}
    cats = declarations(meta.get("categories", {}), "$.metadata.categories") if "categories" in meta else {}
    labels = declarations(meta.get("labels", {}), "$.metadata.labels") if "labels" in meta else {}
    sorts = declarations(meta.get("sortKeys", {}), "$.metadata.sortKeys", True) if "sortKeys" in meta else {}

    plugins = doc.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        fail("$.plugins", "must be a non-empty array")
        print(f"[x] 结构错误 {len(errors)} 条")
        for e in errors[:20]:
            print("   ", e)
        return 1

    seen: set[str] = set()
    for i, p in enumerate(plugins):
        at = f"$.plugins[{i}]"
        if not is_obj(p):
            fail(at, "must be an object")
            continue
        name = string(p.get("name"), f"{at}.name")
        if name:
            if len(name) > MAX_NAME or not PACKAGE_NAME.match(name):
                fail(f"{at}.name", "must be a valid npm package name")
            if name in seen:
                fail(f"{at}.name", f"duplicates {name}")
            seen.add(name)
        for key in ("title", "description"):
            if key in p:
                localized(p[key], f"{at}.{key}")
        for key in ("author",):
            if key in p:
                string(p[key], f"{at}.{key}")
        if "homepage" in p:
            url_check(p["homepage"], f"{at}.homepage", ("https:", "http:"))
        if "repository" in p:
            repo = p["repository"]
            if not is_obj(repo):
                fail(f"{at}.repository", "must be an object")
            else:
                url_check(repo.get("url"), f"{at}.repository.url")
                if "directory" in repo:
                    directory = repo["directory"]
                    if not isinstance(directory, str) or any(
                        seg in (".", "..") or not PATH_SEGMENT.match(seg or "")
                        for seg in directory.split("/")):
                        fail(f"{at}.repository.directory", "must be a relative directory")
        if "icon" in p:
            icon = p["icon"]
            if not (isinstance(icon, str) and (icon.startswith("data:") or icon.startswith("https://"))):
                fail(f"{at}.icon", "must be a data: URL or an https URL reference")
        if "metadata" in p:
            pm = p["metadata"]
            if not is_obj(pm):
                fail(f"{at}.metadata", "must be an object")
            else:
                if "category" in pm and string(pm["category"], f"{at}.metadata.category") not in (None,):
                    if pm["category"] not in cats:
                        fail(f"{at}.metadata.category", f'references undeclared id {pm["category"]!r}')
                if "keywords" in pm:
                    kw = pm["keywords"]
                    if not isinstance(kw, list):
                        fail(f"{at}.metadata.keywords", "must be an array")
                    elif any(not isinstance(x, str) for x in kw):
                        fail(f"{at}.metadata.keywords", "must be an array of strings")
                if "labels" in pm:
                    lb = pm["labels"]
                    if not isinstance(lb, list):
                        fail(f"{at}.metadata.labels", "must be an array")
                    else:
                        if len(set(lb)) != len(lb):
                            fail(f"{at}.metadata.labels", "must not repeat a label")
                        for j, one in enumerate(lb):
                            if one not in labels:
                                fail(f"{at}.metadata.labels[{j}]", f'references undeclared id {one!r}')
                if "sortKeys" in pm:
                    sk = pm["sortKeys"]
                    if not is_obj(sk):
                        fail(f"{at}.metadata.sortKeys", "must be an object")
                    else:
                        for k, v in sk.items():
                            if k not in sorts:
                                fail(f"{at}.metadata.sortKeys.{k}", f'references undeclared id {k!r}')
                            if not isinstance(v, (int, float)) or isinstance(v, bool):
                                fail(f"{at}.metadata.sortKeys.{k}", "must be a finite number")
        vs = p.get("versions")
        if not isinstance(vs, list) or not vs:
            fail(f"{at}.versions", "must be a non-empty array")
            continue
        dup: set[str] = set()
        for j, v in enumerate(vs):
            vat = f"{at}.versions[{j}]"
            if not is_obj(v):
                fail(vat, "must be an object")
                continue
            ver = string(v.get("version"), f"{vat}.version")
            if ver is not None:
                if not ver or not SEMVER.match(ver):
                    fail(f"{vat}.version", "must be an exact SemVer version")
                if ver in dup:
                    fail(f"{vat}.version", f"duplicates {ver}")
                dup.add(ver)
            ts = v.get("publishTimestamp")
            if not isinstance(ts, int) or isinstance(ts, bool) or ts < 0 or ts >= MAX_PUBLISH_TIMESTAMP:
                fail(f"{vat}.publishTimestamp", "must be a non-negative integer seconds")
            src = v.get("source")
            if not is_obj(src):
                fail(f"{vat}.source", "must be an object")
                continue
            kind = src.get("type")
            if kind == "npm":
                if "registry" in src:
                    url_check(src["registry"], f"{vat}.source.registry")
            elif kind == "git":
                gurl = url_check(src.get("url"), f"{vat}.source.url", ("https:",))
                if gurl:
                    parsed = urlparse(gurl)
                    if gurl != gurl.strip() or len([s for s in parsed.path.split("/") if s]) < 2:
                        fail(f"{vat}.source.url", "must name a repository with at least two path segments")
                    if parsed.path.lower().endswith((".tgz", ".tar.gz")):
                        fail(f"{vat}.source.url", "must name a repository, not a tarball")
                commit = src.get("commit")
                if not isinstance(commit, str) or not COMMIT.match(commit):
                    fail(f"{vat}.source.commit", "must be a lowercase 40-hex-digit commit SHA")
            else:
                fail(f"{vat}.source.type", 'must be "npm" or "git"')
        if len(errors) > 400:
            break

    if errors:
        print(f"[x] 校验失败：{len(errors)} 条（最多显示 400 条）")
        for e in errors[:25]:
            print("   ", e)
        return 1
    print(f"[+] 校验通过：{len(plugins)} 条 | 分类 {len(cats)} | 标签 {len(labels)} | 排序键 {len(sorts)}")
    return 0


def run(path: str, index_url: str) -> int:
    with open(path, encoding="utf-8-sig") as fh:
        try:
            doc = json.load(fh)
        except Exception as exc:
            print(f"[x] JSON 解析失败：{exc}")
            return 1
    return check(doc, index_url)


def main() -> int:
    ap = argparse.ArgumentParser(description="校验 dsh format 1 市场索引")
    ap.add_argument("path", nargs="?", default="index.json")
    ap.add_argument("--url", default="https://jack5920.github.io/dsh-hub-index/index.json",
                    help="仅用于报错定位，宿主会用真实索引地址做基准")
    args = ap.parse_args()
    try:
        return run(args.path, args.url)
    except FileNotFoundError:
        print(f"[x] 找不到文件 {args.path}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
