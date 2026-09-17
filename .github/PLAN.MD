---
name: Runnable public RNA-seq
overview: 用 GSE107049 公開果蠅腦 counts 跑可復現的 CPM/DE/同源重疊管線，結果經 GET /rnaseq 顯示在戰情室；明確標這不是本專案對神經節定序。
todos:
  - id: rnaseq-pipeline
    content: ml/rnaseq/run.py + nd_sets.tsv + fixture 測試；just rnaseq / fetch.sh
    status: in_progress
  - id: rnaseq-api
    content: gateway GET /rnaseq 讀 rnaseq_bridge.json
    status: pending
  - id: rnaseq-ui
    content: OrthologPanel 改讀 API，顯示真正 CPM/log2FC 與誠實標籤
    status: pending
  - id: rnaseq-docs
    content: 更新 data/README、METHODS、FUTURE
    status: pending
isProject: false
---

# 可執行的公開 RNA-seq 橋

**做什麼：** `just rnaseq` 會真的下載公開果蠅腦 counts、做 CPM + 年輕 vs 年老差異表達、再和人類神經退化基因集做同源重疊，結果經 API 畫在戰情室。

**不做什麼：** 不定序這個專案的 HIL / 孿生；不把戰情室干預當成 RNA-seq 條件；不跑 FASTQ/salmon。橫幅維持 **not live RNA-seq of this prototype**，並標 **GSE107049** DOI。

```mermaid
flowchart LR
  geo[GSE107049_counts]
  run[ml_rnaseq_run.py]
  art[rnaseq_bridge.json]
  gw[GET_rnaseq]
  ui[OrthologPanel]

  geo --> run --> art --> gw --> ui
```

## 資料與對比

- 資料集：[GSE107049](https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE107049)（Davie / 老化果蠅全腦 RNA-seq；補充檔 `GSE107049_counts.txt.gz` ~576 KB）。
- 對比：**day 5 vs day 40** 全腦（Mann–Whitney U on log1p CPM）。這是公開老化腦轉錄體，不是蟑螂神經節修復實驗。
- 基因面板：現有 FlyBase ID（para、Shaker、Rdl、Dop1R1…）+ 小型 ALS/PD 同源表（Sod1/TBPH/caz/Pink1/park 等，文獻對照）。
- 下載進 [`data/raw/`](data/raw/)（已 gitignore）；CI 用 [`ml/rnaseq/fixtures/`](ml/rnaseq/fixtures/) 迷你 counts，**計算步驟與線上相同**。

## 管線（Python stdlib，不新增套件）

新增 [`ml/rnaseq/run.py`](ml/rnaseq/run.py)：

1. 下載或讀 cache / fixture。
2. 解析 counts、依 GEO design 標 age/sex。
3. CPM + log1p；對面板基因算 mean CPM、log2FC、U 檢定 p。
4. 與 [`ml/rnaseq/nd_sets.tsv`](ml/rnaseq/nd_sets.tsv)（人類 ALS/PD 符號 + 果蠅同源 + 出處）做重疊。
5. 寫 [`ml/artifacts/rnaseq_bridge.json`](ml/artifacts/rnaseq_bridge.json)（含 accession、下載 URL、`ran_at`、誠實聲明）。失敗時明確報錯，不靜默填假數字。

[`data/fetch.sh`](data/fetch.sh) 加 `rnaseq` 目標；[`justfile`](justfile) 加 `just rnaseq`。單元測試覆蓋正規化、DE 符號、缺檔錯誤。

## 線上讀取

- [`services/api-gateway`](services/api-gateway/src/main.rs) 新增 `GET /rnaseq`：讀 `RNASEQ_BRIDGE`（預設 `ml/artifacts/rnaseq_bridge.json`）；沒檔回 404 + `{status:"missing", hint:"just rnaseq"}`。Compose volume 已有 artifacts 則掛同一目錄，或複製進 gateway 映像。
- [`dashboard/lib/api.ts`](dashboard/lib/api.ts) `getRnaseq()`；[`OrthologPanel.tsx`](dashboard/components/OrthologPanel.tsx) 改吃 API：有 run 就顯示 CPM / log2FC / p 與 ND 重疊；沒 run 顯示「跑 `just rnaseq`」。橫幅改為：公開 GSE107049，**不是本機定序**。Transformer/GRU 仍不讀此 JSON。
- mock 模式回傳標了 `source: "fixture"` 的假橋檔，UI 一樣標 fixture。

## 文件

更新 [`data/README.md`](data/README.md)、[`docs/METHODS.md`](docs/METHODS.md)、[`docs/FUTURE.md`](docs/FUTURE.md)：寫清物種（果蠅腦）、對比（日齡）、與 HIL 無關。不改 [`proto/WIRE.md`](proto/WIRE.md)。
