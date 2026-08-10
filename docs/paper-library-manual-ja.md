# QuantMind Codex Paper Library 日本語運用マニュアル

このマニュアルは、このforkで追加されたローカルPaper Libraryを、人間が安全に構築・運用し、論文を読む・探す・再利用するための入口です。初回構築の厳密なcommandとDB移行仕様は [DB構築・永続化ガイド](paper-library-setup.md)、画面とdata ownershipの仕様は [UI guide](paper-library-ui.md) を参照してください。

## 1. このシステムの目的

Paper Libraryは、論文ファイルを集めるだけの保管庫ではありません。次の3種類の情報を区別したまま、原典へ戻れる形で管理します。

| 種類 | 例 | 扱い |
|---|---|---|
| 原典と原典根拠 | PDF、page text、exact quote、source hash | canonical evidence。引用判断の基準 |
| Codex生成物 | ページ根拠付き要約、`source_fact`、`codex_interpretation`、日本語訳 | contractとhashを検証してcanonical DBへ登録。ただし翻訳は引用根拠にしない |
| 人間の管理情報 | 表示用書誌、星、読書状態、tag、collection、memo、説明画像、review | mutable sidecar。canonical evidenceを変更しない |

英文論文を日本語で理解しやすくしつつ、重要な主張は必ず英語原文とPDFページへ戻って確認できることが設計上の目標です。

## 2. オリジナルQuantMindとの関係

- オリジナル: [LLMQuant/quant-mind](https://github.com/LLMQuant/quant-mind)
- このfork: [tikeda123/quant-mind-codex](https://github.com/tikeda123/quant-mind-codex)

オリジナルのQuantMindは、金融情報を型付きknowledgeへ変換するPythonライブラリです。このforkは、その `PaperFlow`、paper knowledge model、`LocalKnowledgeLibrary`、SQLite persistence、retrieval contractを土台に、次を追加しています。

- 人間向けの6画面Streamlit UI
- Codexとの明示的なfile handoffによるページ根拠付き要約・注釈
- 全物理ページの日本語訳とページ別原文照合
- 説明画像と個人用書誌・読書管理
- 固定ローカル多言語embeddingによるAPI-key-free検索
- canonical/sidecar DB pairの完全migrationと受け入れ確認

完全な比較表とsource変更箇所は [root README](../README.md#オリジナルとの違い) にあります。

## 3. 運用上の重要な境界

### Codexはアプリの外にいる

StreamlitやPythonはCodexをAPIとして呼びません。処理は次の順序です。

1. UI/Pythonが原典とmanifestを準備する。
2. 人間が、このcheckoutで対話中のCodexへ明示的に作業を依頼する。
3. Codexが指定directoryへJSON draftを書く。
4. UI/Pythonがdraftを決定論的に検証する。
5. 人間がpreviewを確認し、登録を明示的に承認する。

この境界により、CodexをPython service、background worker、schedulerとして扱いません。

### canonicalとsidecarを混ぜない

- `paper-library.sqlite3`: 原典PDF、page text、artifact、vector、登録監査などのcanonical data。
- `paper-library-ui.sqlite3`: 個人用表示名、確認済み著者・公開情報、読書状態、star、memo、tag、collection、説明画像、review。

sidecar変更はcanonical evidenceを書き換えません。backupとmigrationでは2つのDBを必ず一組として扱います。

### 翻訳と画像は根拠ではない

日本語訳と説明画像は理解を助ける資料です。引用、数値、数式、主張を確認するときは、同じページ番号の英語原文と保存済みPDFを使用します。semantic similarityも信頼度や正しさの確率ではありません。

## 4. 初回セットアップ

### 必要なもの

- Python 3.10以上
- `uv`
- Git
- このcheckoutで利用できる対話型Codex
- 初回installとmodel cache取得時のnetwork
- text layerを持つPDF、または事前にOCRして目視確認したPDF

Paper Libraryの通常運用にOpenAI API keyやHugging Face API keyは不要です。

### install

```bash
git clone https://github.com/tikeda123/quant-mind-codex.git
cd quant-mind-codex

uv venv
source .venv/bin/activate
uv pip install -e ".[full,ui]"
```

### runtime rootとmodel cache

runtimeはGit checkoutの外に置きます。

```bash
export QUANTMIND_RUNTIME_ROOT="/absolute/path/to/quantmind-codex-data"
mkdir -p "$QUANTMIND_RUNTIME_ROOT/models" "$QUANTMIND_RUNTIME_ROOT/intake"

python scripts/cache_local_embedding_model.py \
  --cache-dir "$QUANTMIND_RUNTIME_ROOT/models"
```

使用するmodelは固定revisionの `intfloat/multilingual-e5-small`、384次元です。model選択UIや自動fallbackはありません。

### DB pathと起動

```bash
export QUANTMIND_LIBRARY_DB="$QUANTMIND_RUNTIME_ROOT/paper-library.sqlite3"
export QUANTMIND_UI_DB="$QUANTMIND_RUNTIME_ROOT/paper-library-ui.sqlite3"
export QUANTMIND_MODEL_CACHE="$QUANTMIND_RUNTIME_ROOT/models"
export QUANTMIND_INTAKE_ROOT="$QUANTMIND_RUNTIME_ROOT/intake"

streamlit run apps/paper_library/app.py \
  --server.address 127.0.0.1 \
  --server.port 8501
```

[http://127.0.0.1:8501](http://127.0.0.1:8501) を開きます。sidebarの「設定path」で4つのpathが意図したruntimeを指していることを、登録前に確認してください。

### 終了

Streamlitを起動したterminalで `Ctrl-C` を押します。DB migrationやbackupを行う前は、UIと他のwriterを停止してください。

## 5. 1本の論文を登録する

### 5.1 PDFを準備する

**取り込み**画面で、次のいずれかを選びます。

- `PDF upload`: 手元のPDFをuploadする。
- `公開HTTPS URL`: 認証不要の公開PDF URLを指定する。

`Prepare`を選ぶと、UUID directoryに次が作成されます。

```text
intake/<uuid>/
├── source.pdf
└── manifest.json
```

manifestはPDF hash、size、physical page、抽出text、parser identity、draft policy hashを固定します。この時点ではDB登録されていません。

### 5.2 scan PDFを判定する

抽出textが空、またはほとんどない場合は、scan-only PDFの可能性があります。このアプリにはOCR機能がありません。

1. 外部のOCR toolでtext layerを付ける。
2. 数式、ギリシャ文字、表、脚注、段組み、page順を目視確認する。
3. OCR後のPDFを新しいsourceとして再度Prepareする。

OCR前のPDFとOCR後のPDFはbytesもhashも異なります。登録に使ったPDFがcitation authorityです。

### 5.3 Codexへ要約・注釈を依頼する

UIに表示された絶対pathをそのまま使い、Codexへ次のように依頼します。

```text
<workdir>/source.pdf と <workdir>/manifest.json を読み、
contexts/usage/codex-paper-draft-v1.md に従って、
同じdirectoryへ draft.jsonだけを保存してください。
manifestにないID、page、chunk、quoteは作らないでください。
外部LLM APIは使わないでください。
```

Codexが作成する主な内容は、書誌候補、全体要約、citation、`source_fact`、`codex_interpretation`です。解釈は原典の事実と別labelで保存されます。

### 5.4 検証して登録する

1. **取り込み**へ戻る。
2. `再読込して検証`を選ぶ。
3. page数、annotation数、summary previewを確認する。
4. `検証済みbundleをcanonical DBへ登録します`をcheckする。
5. `登録する`を選ぶ。

検証ではsource hash、page参照、exact quote、citation coverage、chunk解決可能性などを確認します。失敗時はcanonical DBへ部分書き込みしません。error内容をCodexへ示し、`source.pdf`や`manifest.json`を変更せず `draft.json`だけを修正します。

## 6. タイトル・著者・公開情報を整える

PDFは、title、author、publication dateを機械可読metadataとして持たないことがあります。その場合、catalogに「未取得」と表示されるのは、推測で埋めないための安全な挙動です。

原論文のtitle pageやjournal citationを確認し、**論文詳細 → 読書管理**で次を入力します。

- `個人表示名`: 原論文に記載されたtitleへ忠実に入力する。
- `表示用著者`: 1行に1名。原論文の表記順を維持する。
- `表示用公開情報`: 原典の精度を維持する。月までなら `Mar. 1952` のようにし、存在しない日を補わない。

これらはsidecarの表示情報です。canonical source metadataを上書きしません。

## 7. 全ページ日本語訳を登録する

### 7.1 翻訳用fileを準備する

**論文詳細 → 日本語訳**で `Codex対話で日本語訳を作成・登録` を開き、`翻訳用ファイルを準備`を選びます。

```text
translation workdir/
├── source.pdf
└── translation_manifest.json
```

### 7.2 Codexへ翻訳を依頼する

```text
<workdir>/source.pdf と <workdir>/translation_manifest.json を読み、
contexts/usage/codex-paper-translation-v1.md に従って、
同じdirectoryへ translation_draft.jsonを保存してください。
物理pageを省略・追加・並べ替えないでください。
数式、記号、引用、固有名詞の意味を変えないでください。
外部LLM APIは使わないでください。
```

### 7.3 検証・登録・review

1. `translation_draft.jsonを検証`を選ぶ。
2. 全physical pageが1回ずつ順番に存在することを確認する。
3. `確認して日本語訳を登録`を選ぶ。
4. `日本語のみ`または`原文対訳`で読む。
5. pageごとに `未確認`、`要確認`、`原文照合済み`を保存する。

翻訳artifactはimmutableです。人間のreview状態とmemoだけがsidecarで更新されます。誤訳を発見した場合は、原文とcontractから新しい翻訳versionを作る運用にします。

## 8. 説明画像を注釈として使う

**論文詳細 → 画像注釈 → 説明画像を追加**でPNG、JPEG、WebPを登録できます。

必須項目:

- `見出し`: 画像が説明するconcept。
- `代替テキスト`: 画像を見なくても意味が分かる日本語説明。

推奨項目:

- `作成者・ツール`: 誰が、または何で作ったか。
- `入手元・作成条件`: URL、生成条件、加工内容など。
- `関連する文章注釈`: どのsource factやinterpretationの理解を助けるか。
- `確認状態`と`確認メモ`: 原典照合前は `未確認`または`要確認`。

画像は最大20 MB、40 megapixelです。画像bytesはsidecarに保存されます。画像内の説明がもっともらしくても、原論文のcitation evidenceやsemantic search対象にはなりません。

Codexへ画像説明を依頼するときは、誤りを事実化しないよう次の区別を求めます。

```text
この画像が視覚的に説明している内容を日本語で簡潔に説明してください。
原論文で確認できる内容と、画像作成者による補足・単純化・推論を分けてください。
数値や主張を原論文の事実として断定する場合は、対応する原典pageを示してください。
```

## 9. 蔵書を整理する

### ダッシュボード

保存数、検索準備済み、要確認、broken、未読、画像注釈あり、読書中、star付きを概観します。日常運用の開始点です。

### 蔵書

title/author/URI、source種別、整合性、読書状態、star、tag、collectionで絞り込みます。1 page最大50件です。

### 論文詳細

次のtabを使います。

| tab | 用途 |
|---|---|
| 概要 | cited global summaryと生成情報を読む |
| 注釈 | `source_fact`と`codex_interpretation`を区別して読む |
| 日本語訳 | 日本語のみ/原文対訳、page review、翻訳作成 |
| 画像注釈 | 説明画像、alternative text、provenance、review |
| 原典 | citation pageのpreview、exact quote、full PDF |
| 登録履歴 | artifact/model/登録versionを確認 |
| 読書管理 | 表示用書誌、読書状態、star、memo、tag、collection |

個人memoは未検証の人間stateです。原典事実として再利用するときは、必ずcitationを別途確認してください。

## 10. 日本語・英語で検索する

**検索**で質問または検索語を入力し、`ローカル意味検索`を選びます。

- 検索対象: `summary`、`chunk`
- filter: 読書状態、star、tag、collection
- query: 日本語または英語
- result: 類似度、matched projection text、citation、exact quote

登録時にdocument projectionをembeddingし、検索時にはqueryを1回embeddingして保存済みvectorと近傍照合します。modelは固定で、通常検索中にnetwork downloadしません。

検索resultの類似度はranking用です。正しさや論文品質のscoreではありません。結果を開き、citation pageと原典を確認します。

## 11. 監査とhealth check

**監査**では、paper数、登録履歴、embedding model identity、fast health、deep validation、JSON export、sidecar orphanを確認します。

repositoryのnetwork-free検証:

```bash
bash scripts/verify.sh
```

UI shell検証:

```bash
python scripts/verify_paper_library_ui.py
```

cache済みmodelを使う登録・検索E2E:

```bash
python scripts/verify_local_paper_e2e.py \
  --cache-dir "$QUANTMIND_MODEL_CACHE"
```

## 12. backupとmigration

### 原則

- Streamlitとすべてのwriterを停止する。
- canonical DBとsidecar DBを一組で移す。
- 移行先directoryは存在していてはいけない。
- sourceを上書きしない。
- raw copyだけで完了とせず、logical digestとpublic API reopenを確認する。

### 実行例

```bash
python scripts/migrate_paper_library.py \
  --source-library-db "$QUANTMIND_LIBRARY_DB" \
  --source-ui-db "$QUANTMIND_UI_DB" \
  --destination-root "/absolute/path/to/quantmind-backups/YYYY-MM-DD" \
  --model-cache "$QUANTMIND_MODEL_CACHE" \
  --query "portfolio covariance" \
  --query "ポートフォリオの共分散"
```

生成されるreport:

- `migration-manifest.json`: schema object、全table/row/BLOBの論理digest、件数。
- `migration-acceptance.json`: paper、PDF hash、翻訳page、画像hash、sidecar参照、検索の再open結果。

model cacheと未登録のintake履歴はSQLite pairに含まれません。再構築可能なmodel cacheを保存するか、restore先で固定revisionを明示的にcacheしてください。詳しい失敗時のatomicityとrestore手順は [DB構築・永続化ガイド](paper-library-setup.md#9-migrate-every-value-from-an-existing-database-pair) にあります。

## 13. トラブルシューティング

| 症状 | 主な原因 | 対応 |
|---|---|---|
| UI起動時にpath error | 相対path、canonical/sidecarが同じfile | 4つの環境変数を絶対pathで設定し、DB pathを分ける |
| `8501` が使用中 | 別Streamlit processが起動中 | 既存processを確認するか、loopbackの別portを明示する |
| Prepare後にpage textが空 | scan-only PDF | OCRして数式・表・page順を目視確認後、新規Prepare |
| URL取り込み失敗 | HTTP、認証URL、private/localhost、unsafe redirect、非PDF | 認証不要の公開HTTPS PDFかlocal uploadを使う |
| `draft.json`検証失敗 | source hash、page、quote、chunk、schemaの不一致 | manifestを変更せず、errorに沿ってdraftだけを修正する |
| title/author/公開情報が未取得 | PDF metadataがない、または不確か | 原論文を確認し、読書管理の表示用書誌へ忠実に入力する |
| 日本語訳を登録できない | page不足、重複、順序違い、source hash不一致 | 全physical pageをexactly onceで作り直す |
| 検索時にmodel error | 固定revisionがcacheされていない、path違い | `QUANTMIND_MODEL_CACHE`とcache commandを確認する |
| 検索resultが0件 | filter過多、未登録vector、query不一致 | filter解除、監査の検索準備状態、query言い換えを確認する |
| 画像upload失敗 | format、20 MB、40 megapixel制限 | PNG/JPEG/WebPへ変換し、size・pixel数を下げる |
| sidecar情報だけ消えた | 異なるsidecar pathで起動、sidecar未restore | sidebarの設定pathとpair backupを確認する |
| migrationがdestinationを拒否 | destinationが既に存在 | 新しい空のpath名を指定する。既存directoryを上書きしない |

errorを解消できない場合は、次を揃えて調査します。秘密情報やPDF本文を公開issueへ貼らないでください。

- 実行したcommand
- error typeとmessage
- sidebarに表示された4つのpath（個人情報をredact）
- `git rev-parse --short HEAD`
- `python --version`
- 監査exportまたはmigration report（原典内容を含まない範囲）

## 14. できること・できないこと

### 対話中のCodexでできること

- local PDFとmanifestを読み、contract準拠の要約・注釈draftを作る。
- 全ページ翻訳draftを作る。
- 説明画像を読み、日本語のcaptionとalternative textを提案する。
- 原論文のtitle、author、publication表記を確認する。
- migration reportや監査結果を読み、問題を診断する。

### アプリが自動では行わないこと

- PythonからCodexや他のLLMをAPIとして呼ぶこと。
- Codex作業のbackground pollingや無人定期実行。
- OCR、翻訳品質の自動保証、画像内容の自動真偽判定。
- model選択、複数backend、hybrid search、reranking、最適化。
- canonical artifactの削除・上書き・自動repair。
- 認証、複数user、remote hosting。

## 15. 日常運用checklist

### 取り込み時

- source PDFを目視できた。
- page数とtext layerを確認した。
- Codexにmanifestとcontractを指定した。
- validationを通し、previewを読んだ。
- 登録前に明示checkboxをcheckした。

### 読解時

- 翻訳や説明画像と原典根拠を区別した。
- 重要な数値・数式・結論を英語原文で確認した。
- title、author、publicationは原典の表記と精度に忠実に入力した。
- review状態とmemoを更新した。

### 終了・backup時

- 重要なsidecar変更後にpair backupを作った。
- migration manifest/acceptanceが成功している。
- runtime dataやPDFをGitへstageしていない。
- 必要に応じてStreamlitを `Ctrl-C` で停止した。

## 16. 関連ドキュメント

| ドキュメント | 役割 |
|---|---|
| [root README](../README.md) | fork概要、オリジナルとの差分、最短起動 |
| [DB構築・永続化ガイド](paper-library-setup.md) | 新規DB、schema、migration、backup、acceptance |
| [UI guide](paper-library-ui.md) | 画面、data ownership、安全境界、制限 |
| [Library guide](library.md) | `LocalKnowledgeLibrary`の保存・検索contract |
| [Registration procedure](../contexts/usage/codex-paper-registration.md) | Codexとの取り込みfile handoff |
| [Cited draft contract](../contexts/usage/codex-paper-draft-v1.md) | `draft.json` schemaと根拠制約 |
| [Translation contract](../contexts/usage/codex-paper-translation-v1.md) | `translation_draft.json` schemaとpage制約 |
| [Component catalog](README.md) | 公開operation、example、verification command |
