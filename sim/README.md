# 未来投資シミュレーター — 戦略バックテスト版

過去40年(1985-2026)の日次データで投資戦略を検証する Web アプリ。

## 機能

- **4資産ポートフォリオ**: Cash / SP500 / Nasdaq100 / Gold
- **3パターン同時比較**:
  - パターン A: SP500 100% Buy & Hold (ベンチマーク、固定)
  - パターン B: ユーザー任意設定
  - パターン C: ユーザー任意設定
- **戦略バックテスト**(Excel版から移植):
  - ATHからのDD%閾値で機械的買い増し (Cash → Gold の順で資金調達)
  - Anchor(paused→ATH復帰の固定値) からの上昇率で利確
  - 利確時は売却額を Cash と Gold に按分
  - SP500は売却対象外(長期成長を温存)
- **歴史的イベントツールチップ**: 26件の主要イベント(ブラックマンデー〜AI相場)を日付近辺で表示
- **指標**: 最終資産、CAGR、最大DD、買い増し/利確回数、資金不足回数、円貨換算

## ファイル構成

| ファイル | 内容 |
|---|---|
| `index.html` | メインアプリ(UI + 戦略エンジン + Chart.js) |
| `market_data.js` | SP500/NDX/Gold の日次データ(312KB) |
| `manifest.json` | PWA 設定 |

## デプロイ手順 (GitHub Pages)

`rai0103.github.io/sim/` への上書きデプロイ:

### 1. ローカルでテスト

```bash
cd C:\Users\shini\rai0103.github.io\sim
# 既存ファイルをバックアップ
git checkout -b backup-old-sim
git commit -am "Backup old simulator"
git checkout main
```

### 2. 新ファイルを配置

3つのファイル(`index.html`, `market_data.js`, `manifest.json`)を `sim/` フォルダに上書きコピー。

### 3. ローカルでブラウザ確認

`sim/index.html` をブラウザで直接開いて動作確認。

### 4. コミット & プッシュ

```bash
git add sim/
git commit -m "戦略バックテスト版にアプリを更新"
git push origin main
```

数分後、`https://rai0103.github.io/sim/` で公開されます。

## カスタマイズ

### 配分の変更
パターンB・Cの各資産の数値(%)を編集 → 合計が100%になるよう調整。

### 戦略パラメータの編集
「▼ 戦略の詳細設定」をクリックして展開:
- **買い増しテーブル**: DD閾値ごとの投入率、SP500割合、Nasdaq100割合
- **利確テーブル**: 上昇率閾値ごとの売却率、Cash行先、Gold行先

### 期間の変更
開始日・終了日を変更して、特定の時期(コロナ期、ITバブル期など)に絞ったシミュレーションが可能。

## データ仕様

- **期間**: 1985-10-01 ~ 2026-05-08 (約40年, 10,230営業日)
- **出典**: Stooq
- **指数**:
  - SP500 (^SPX)
  - Nasdaq100 (^NDX)
  - Gold (XAUUSD, 金スポット価格)
- **日付一致率**: 99.6%(休日等は前日値引継ぎ)

## 既知の制限

- スマホアプリの Claude.ai 内蔵ブラウザでは表示不可(Chrome等の通常ブラウザで動作確認済み)
- PCのChrome、Edge、Firefox、Safari で動作確認
- アイコン未設定(必要に応じて `manifest.json` の `icons` 配列に追加)

## 戦略ロジック

詳細はExcelファイル `sp500_daily_1957-2026.xlsx` の README シートを参照。要点:

1. **Anchor 固定型**: DD-5%超え後の最初のATH復帰日の終値で固定。連日ATH更新中は変わらない。
2. **DD独立イベント**: ATH更新でリセット。各閾値の0→1遷移を1イベントとしてカウント。
3. **買い増し資金源**: Cash 優先、不足時 Gold 売却(Gold枯渇まで)。それ以上は Funds Short=1 としてスキップ。SP500 は絶対売らない。
4. **利確売却順序**: Nasdaq100 → SP500の順(Gold は温存)、売却額を Cash 行先と Gold 行先の比率で按分。
