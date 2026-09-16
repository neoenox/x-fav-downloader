# Design: Xお気に入り画像・動画ダウンローダー

- Date: 2026-09-16
- Status: 初版承認済み（ヒアリング→方式→設計3セクションまで合意）
- Path: Architectural（新規プロジェクトのため）

## 1. Objective

X（旧Twitter）の自分の「いいね」直近100件から画像・動画を自動保存する。
手動保存の手間をなくし、再実行時は差分だけ落とせるようにする。

成功基準：
- 直近100件中、公開中ツイートの画像はorig画質、動画は最高画質mp4で `downloads/` に揃う
- `metadata.jsonl` と保存ファイルが一致する
- 2回目実行は新規分だけDLされる（重複なし）
- 削除・鍵垢が混ざっても途中で止まらない

対象外：
- 1000件超の大規模アーカイブ、定期自動実行（タスクスケジューラ等）
- 公式X API使用、有料APIプラン前提の実装
- 他人のいいねの無断大量収集

## 2. 前提・合意事項（ヒアリング結果）

- 取得方式: B) 非公式方式OK（ブラウザログイン流用、経験あり）
- 実行環境: C) Python本体 + 起動用bat
- 対象範囲: C) 直近100件程度
- 保存形式: C) おまかせ（本書で提案）
- ログイン: A) Chrome/Edgeでログイン済みを流用。ただし実装はChrome本体プロファイルを直接触らず、初回手動ログイン→storage_state保存方式とする（ロック・競合回避のため）

ASSUMPTIONS:
1. Windows 10/11 + Python 3.10+ が使える
2. 初回のみ手動ログイン操作が可能（ID/PASS/2FAはユーザーが入力）
3. 100件規模なので逐次DLで十分（並列化しない）
4. XのDOM変更時はセレクタ修正で対応する前提（自動追従はしない）

## 3. 方式選定

採用：案1 Python + Playwright + yt-dlp ハイブリッド

- Playwrightで `x.com/i/likes` をスクロール→ツイートURL100件抽出
- 画像は `pbs.twimg.com ... ?name=orig` で直接DL
- 動画/GIFは yt-dlp で最高画質mp4化
- メタデータはJSONL、重複はseen.jsonで管理

不採用：
- 案2 gallery-dl一本：コード最小だがX変更に弱く制御しにくい
- 案3 半手動：自動化にならない

## 4. Architecture / 構成

置き場所： `C:/gemini-desktop/x-fav-downloader/`

```
x-fav-downloader/
  run.bat              # ダブルクリック用。venv作成→pip install→main.py起動
  requirements.txt     # playwright, yt-dlp
  config.json          # target_count=100, out_dir=downloads, headless=False等
  main.py              # ①取得→②DL→③メタ保存。--login / --dry-run対応
  auth/
    storage_state.json # 初回ログイン保存、2回目以降自動利用（gitignore）
  downloads/
    {date}_{tweetid}_{idx}_{username}.{ext}
    metadata.jsonl
    seen.json
  logs/
    run_YYYYMMDD_HHMM.log
  docs/superpowers/specs/2026-09-16-x-fav-downloader-design.md  # 本書
```

コンポーネント：
- LikesFetcher（Playwright）：ログイン状態利用、自動スクロール、ツイートURL抽出
- MediaDownloader：画像= requests直DL、動画= yt-dlp subprocess呼出
- MetaStore：metadata.jsonl追記、seen.json更新
- Runner/CLI：config読込、--login/--dry-run/--count、ログ・リトライ統括

## 5. Data Flow

1. `run.bat` → venv確認→ `python main.py`
2. `storage_state.json` が無ければブラウザを開き手動ログイン→保存して終了
3. `x.com/i/likes` を開き、 target_count(100) に達するまでスクロール（2秒待機、空スクロール5回で打切り）
4. ツイートURL一覧から `seen.json` 差分を抽出
5. 各ツイート詳細を開かず一覧時のメディアURLから、画像はorig直DL、動画はyt-dlpでDL
6. 成功ごとに `metadata.jsonl` 追記＋ `seen.json` 更新（中断時も再開可）
7. 失敗はログに残しスキップ、最後にサマリ表示（成功/スキップ/失敗件数）

## 6. ファイル名・画質・メタデータ

命名： `{ツイート日付}_{tweet_id}_{idx}_{username}.{ext}`
例： `20250910_1968234567890123456_0_twitteruser.jpg`
- 日付はツイート投稿日、取れなければ取得日
- idxはツイート内メディア連番

画質：
- 画像： `?format=jpg&name=orig`、失敗時 `large` フォールバック、拡張子維持
- 動画/GIF： `yt-dlp -f "bestvideo+bestaudio/best" --merge-output-format mp4`

メタ（metadata.jsonl、1行1ツイート）：
`{tweet_url, tweet_id, username, created_at, text(先頭200字), media_type, saved_files[], fetched_at}`

設定（config.json）：
`{target_count:100, scroll_wait_sec:2, max_empty_scrolls:5, retry:3, headless:false, out_dir:"downloads"}`

## 7. Error Handling / 運用

- 削除・鍵・年齢制限：スキップ＋ログ、止めない
- ネットワーク失敗：3回リトライ（2→4→8秒バックオフ）、一時ファイル削除
- 抽出0件：DOM変更疑いのため即エラー終了、空上書きしない。セレクタは `a[href*="/status/"]` 中心＋予備2種
- レート制限配慮：逐次DL、スクロール2秒間隔
- 有効期限切れ：再ログイン操作で復旧（ブラウザを開いて手動ログイン）
- 運用は手動ダブルクリックのみ、定期実行なし
- auth/ と downloads/実ファイルはgitignore、configとコードのみ管理

## 8. Testing

- `python main.py --dry-run --count 5` ：DLせず抽出のみ
- 受け入れ：公開中5件でURL抽出→100件本番で画像/動画が揃いmetadataと一致→2回目は0件DL（差分確認）
- 手動確認のみ、自動テストは作らない（100件規模・個人利用のため）

## 9. Open Questions（実装前に確定）

- [x] 方式BでOK → 確定
- [x] Python+batでOK → 確定
- [x] 100件でOK → 確定
- [ ] Xのユーザー名（@xxx）をconfigに書くか、`/i/likes` 固定で行くか → 実装時に `/i/likes` 固定で開始し、ダメなら修正
- [ ] Edge派かChrome派か → Playwrightはchromium同梱で動かすのでどちらでもOK扱い

## 10. 次ステップ

- 本書承認後、実装プラン（tasks）作成→ `main.py` / `run.bat` 実装へ
- 実装順： 1) スケルトン+config 2) ログイン保存 3) 抽出 4) DL 5) メタ・重複 6) bat・dry-run
