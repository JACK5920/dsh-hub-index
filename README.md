# DSH 插件市场索引源（由 dsh-plugin.org 目录转换）

填这一条（必须带 `/index.json`，dsh 抓取用 `redirect:"error"`，301 的地址一律失败）：

```
https://jack5920.github.io/dsh-hub-index/index.json
```

## 数据来自哪

`https://api.dsh-plugin.org/plugins.zh.json` —— DSH Plugin Hub 插件（npm 包 `dsh-plugin`）自己读的私有目录接口。
它顶层是数组、字段压成 `s/o/n/vr/c/t/r/ic/v/u/sg/fk`，而且响应不带 `Access-Control-Allow-Origin`，
本身**不能**当索引源。本仓库把它转成 format 1，并补上面板校验器要求的声明。

## 宿主校验规则（从 app.asar 的 parseMarketIndex 里读出来的，不是猜的）

- `metadata.categories` / `metadata.labels` / `metadata.sortKeys` 是**声明表**：条目里用到的每个 id 都必须先在声明表里出现，
  否则报 `references undeclared id`。sortKeys 的声明还必须带 `order: "asc"|"desc"`。
- `versions[].version` 必须是**精确 SemVer**，`publishTimestamp` 必须是整数秒 —— 缺版本号的条目不能留。
- 可选字段一旦存在就不能是空串（`author: ""` 直接判错），所以空值一律省略。
- `repository.url` / `source.registry` 必须是绝对 URL 且不带 query/fragment。
- 包名重复会整份索引判废，所以转换时按包名去重，只留时间最新的一条。

`scripts/validate_index.py` 是这套规则的本地复刻，发布前先跑它，别把红叉留给面板。

## 已知失真

- `publishTimestamp`：目录给了版本号的条目用目录更新时间；`--enrich` 模式下改用 npm 的 `modified`（最后一次发布时间）。
- `verified` 标签是上游目录自己标的，本索引没做安装实测。
- 没有任何条目钉 commit（上游目录不提供 sha）。
- 无图标、无 homepage：源数据里没有。

## 重建

```bash
python scripts/hub2index.py --out index.json --enrich --validate   # 全量，含 npm 补版本（约 10 分钟）
python scripts/hub2index.py --out index.json --validate            # 只用目录自带版本号（快，条目少一半）
python scripts/validate_index.py index.json                        # 单独校验
```

`.github/workflows/refresh.yml` 每天 00:00 UTC（北京 08:00）跑全量重建，产物有变化才提交。

## 免责

插件是第三方代码，装进宿主就以你的权限运行。本仓库只做格式转换，不审核、不背书任何条目。
