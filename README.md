# W杯配信Bot ⚽🔥

FIFAワールドカップ2026の「その日の試合結果」を、灼熱テンションの実況キャラ
「W杯配信Bot」としてLINEグループに**毎日15時（JST）に自動配信**するBot。

GitHub Actions の cron で動くので、PCを起動しておく必要はありません。

---

## 仕組み

```
毎日15:00 JST（=06:00 UTC）
  └ GitHub Actions が起動
       ├ football-data.org から直近に終了したW杯の試合を取得
       ├ 灼熱キャラに整形（Claude API、無ければテンプレ）
       └ LINE Messaging API でグループへ push
```

一度送った試合は `sent_matches.json` に記録され、翌日に重複配信されません。

---

## セットアップ（初回だけ）

### 1. このリポジトリを作る
GitHubで新規リポジトリを作成（**private 推奨**）し、以下3ファイルを置く：

```
wc_bot.py
README.md
.github/workflows/wc-bot.yml
```

### 2. 無料APIキーを2つ取得

| キー | 取得先 | 必須 | 備考 |
|------|--------|------|------|
| football-data.org | https://www.football-data.org/client/register | ✅必須 | 無料。メール登録だけ。W杯は無料枠に含まれる（10req/分） |
| Anthropic API | https://console.anthropic.com/ | 任意 | 灼熱文をClaudeが生成。**未設定でもテンプレ整形で動く**。1日1回なので費用は数円規模 |

### 3. シークレットを登録
リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で、以下を登録：

| Name | Value |
|------|-------|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Developersで発行した長期トークン |
| `LINE_GROUP_ID` | `C` で始まるグループID |
| `FOOTBALL_DATA_API_KEY` | football-data.org のAPIキー |
| `ANTHROPIC_API_KEY` | Anthropic APIキー（任意。入れなければテンプレ動作） |

> 🔒 トークン類はSecretsにのみ入れること。コードやログには絶対に書かない。

### 4. テスト実行
**Actions タブ → 「W杯配信Bot」→ Run workflow**（手動実行）で1回走らせる。
- グループに速報が届けば成功 🎉
- 届かない／赤くなった場合は、Actionsの実行ログを開いてエラー内容を確認。

### 5. あとは放置
以降は毎日 15:00 JST 前後に自動で配信されます。

---

## カスタマイズ

- **キャラの温度調整** … `wc_bot.py` の `PERSONA`（Claude生成時）と
  `build_with_template`（テンプレ時）を編集。
- **配信時刻** … `.github/workflows/wc-bot.yml` の `cron` を変更（UTC指定。
  例: 12:00 JST にしたいなら `0 3 * * *`）。
- **モデルを安く** … `wc_bot.py` の `ANTHROPIC_MODEL` を
  `claude-haiku-4-5-20251001` に変更。
- **国名の日本語表記**（テンプレ時）… `JA_COUNTRY` に追記。
  Claude生成時はプロンプト側で日本語化されます。

---

## 注意

- GitHub Actionsのcronは混雑時に数分〜十数分ずれることがあります（15:00ちょうど保証ではない）。
- LINE無料プラン（コミュニケーションプラン）は月200通まで。グループへのpushは
  **人数ぶん**カウントされる点に注意（例: 7人 × 30日 ≈ 210通で上限超過）。
  超えても勝手に課金はされず、配信が止まるだけ。
- 試合が無い日は「休息日」の煽りが1通送られます（不要なら `main()` の
  該当分岐を調整）。
