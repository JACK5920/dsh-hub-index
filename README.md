# DSH 插件市场索引源（由 dsh-plugin.org 目录转换）

format 1 索引，可直接填进 dsh → 插件 → 发现 → 管理索引源：

```
https://jack5920.github.io/dsh-hub-index/index.json
```

## 数据来自哪

`https://api.dsh-plugin.org/plugins.zh.json` —— DSH Plugin Hub 插件（npm 包 `dsh-plugin`）自己读的私有目录接口。
那个接口顶层是数组、字段压成 `s/o/n/vr/c/t/r/ic/v/u/sg/fk`，且响应不带 `Access-Control-Allow-Origin`，
所以它本身**不能**当索引源用；本仓库是把它转成 format 1 的结果。

## 转换规则与已知失真

- 包名取 `r.npmPackage`，安装源一律 `{"type":"npm","registry":"https://registry.npmjs.org/"}`。
- 同一包名被不同 slug 重复收录的，只保留时间最新的一条（面板按包名匹配已安装项，重名会互相顶）。
- `publishTimestamp` 来自目录的 `u` 字段，那是**目录更新时间**，不等于 npm 发布时间。
- 目录里约一半条目没给版本号（`vr` 缺失），这些条目不写 `version`，由 dsh 安装时解析 latest。
- 目录不提供 commit sha，因此**没有任何条目钉 commit**；`verified` 标签是上游目录自己标的，不是本仓库实测。
- 无图标、无 homepage：源数据里没有。

## 自己重建 / 每日刷新

```bash
python scripts/hub2index.py --out index.json --check
```

`.github/workflows/refresh.yml` 每天 08:00（北京时间）重跑一次，条目数或字节数变了就提交并触发 Pages 重建。

## 免责

插件是第三方代码，装进宿主就以你的权限运行。本仓库只做格式转换，不审核、不背书任何条目。
