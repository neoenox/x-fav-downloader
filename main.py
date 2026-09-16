# -*- coding: utf-8 -*-
"""Xいいね画像・動画ダウンローダー (Playwright + yt-dlp)"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
AUTH_DIR = os.path.join(BASE_DIR, "auth")
STORAGE_STATE = os.path.join(AUTH_DIR, "storage_state.json")
COOKIES_TXT = os.path.join(AUTH_DIR, "cookies.txt")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def setup_logging(out_dir):
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    log_path = os.path.join(log_dir, f"run_{ts}.log")

    class Tee:
        def __init__(self, *files):
            self.files = files
        def write(self, s):
            for f in self.files:
                f.write(s)
                f.flush()
        def flush(self):
            for f in self.files:
                f.flush()

    logf = open(log_path, "w", encoding="utf-8")
    sys.stdout = Tee(sys.stdout, logf)
    sys.stderr = Tee(sys.stderr, logf)
    return log_path


def launch_browser(p, headless=False):
    """Real Chromeを優先（検出回避＋ログイン流用）。無ければ同梱Chromium。"""
    args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
    try:
        return p.chromium.launch(headless=headless, channel="chrome", args=args)
    except Exception as e:
        print(f"Real Chrome起動失敗、同梱Chromiumで継続: {e}")
        return p.chromium.launch(headless=headless, args=args)


def do_login(headless=False):
    """初回ログイン用。ブラウザを開くので手動でXにログインしてEnter。"""
    from playwright.sync_api import sync_playwright

    os.makedirs(AUTH_DIR, exist_ok=True)
    print("=== first login ===")
    print("Browser opens. Log in to X manually, then press Enter here.")
    print("If x.com/login shows error, use the address bar to go to x.com/ and log in there.")
    with sync_playwright() as p:
        browser = launch_browser(p, headless=False)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.goto("https://x.com/", wait_until="domcontentloaded")
        try:
            input("After login, press Enter > ")
        except KeyboardInterrupt:
            pass
        ctx.storage_state(path=STORAGE_STATE)
        browser.close()
    print(f"保存しました: {STORAGE_STATE}")
    export_cookies_txt()


def export_cookies_txt():
    """storage_state.json -> yt-dlp用 Netscape cookies.txt"""
    if not os.path.exists(STORAGE_STATE):
        return
    with open(STORAGE_STATE, encoding="utf-8") as f:
        state = json.load(f)
    lines = ["# Netscape HTTP Cookie File"]
    for c in state.get("cookies", []):
        domain = c.get("domain", "")
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure") else "FALSE"
        exp = int(c.get("expires", 0) or 0)
        name = c.get("name", "")
        val = c.get("value", "")
        lines.append("\t".join([domain, flag, path, secure, str(exp), name, val]))
    with open(COOKIES_TXT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"cookies.txt更新: {COOKIES_TXT}")


def extract_tweet_id(url):
    m = re.search(r"/status/(\d+)", url)
    return m.group(1) if m else None


def fetch_likes(target_count, scroll_wait, max_empty, headless):
    from playwright.sync_api import sync_playwright

    print(f"いいね取得開始: target={target_count}")
    urls = []
    seen = set()
    with sync_playwright() as p:
        browser = launch_browser(p, headless=headless)
        ctx = browser.new_context(
            storage_state=STORAGE_STATE if os.path.exists(STORAGE_STATE) else None,
            viewport={"width": 1280, "height": 1200},
        )
        page = ctx.new_page()
        cfg = load_config()
        page.goto(cfg.get("likes_url", "https://x.com/i/likes"), wait_until="domcontentloaded")
        time.sleep(4)

        # 未ログインではログイン画面に飛ばされる
        if "/login" in page.url or "ログイン" in (page.title() or ""):
            print("!! ログインが必要です。先に `python main.py --login` を実行してください。")
            browser.close()
            return []

        empty = 0
        last_count = -1
        # 予備セレクタも試す
        while len(urls) < target_count and empty < max_empty:
            anchors = page.query_selector_all('a[href*="/status/"]')
            for a in anchors:
                try:
                    href = a.get_attribute("href") or ""
                except Exception:
                    continue
                if "/status/" not in href:
                    continue
                # photo/video付きのみに絞らない（本文のみはDL時に0件になるので後でスキップ）
                full = href if href.startswith("http") else f"https://x.com{href}"
                # /photo/1 等のサフィックスを除去
                full = re.sub(r"/(photo|video)/\d+$", "", full)
                if full not in seen:
                    seen.add(full)
                    if extract_tweet_id(full):
                        urls.append(full)
                        if len(urls) >= target_count:
                            break
            if len(urls) == last_count:
                empty += 1
            else:
                empty = 0
            last_count = len(urls)
            print(f"  収集中: {len(urls)}件 ...")
            page.mouse.wheel(0, 4000)
            time.sleep(scroll_wait)

        browser.close()
    print(f"取得完了: {len(urls)}件")
    return urls[:target_count]


def load_seen(out_dir):
    p = os.path.join(out_dir, "seen.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return set(json.load(f)), p
        except Exception:
            return set(), p
    return set(), p


def save_seen(seen_set, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_set), f, ensure_ascii=False, indent=2)


def append_meta(out_dir, record):
    p = os.path.join(out_dir, "metadata.jsonl")
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def ytdlp_download(tweet_url, out_dir, retry=3):
    """yt-dlpで1ツイート分をDL。保存されたファイル一覧を返す。"""
    # 一時出力テンプレ（後で仕様名にリネーム）
    tmp_tmpl = os.path.join(out_dir, "_tmp_%(id)s_%(uploader_id)s_%(autonumber)s.%(ext)s")
    base_cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-warnings",
        "--merge-output-format", "mp4",
        "-f", "bestvideo+bestaudio/best",
        "-o", tmp_tmpl,
        tweet_url,
    ]
    if os.path.exists(COOKIES_TXT):
        base_cmd[1:1] = []  # no-op
        # --cookies は -m の後ではなく末尾寄りでもOK。先頭に挿入
        base_cmd.extend(["--cookies", COOKIES_TXT])

    before = set(glob.glob(os.path.join(out_dir, "_tmp_*")))
    err = ""
    for attempt in range(1, retry + 1):
        try:
            r = subprocess.run(base_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=300)
            if r.returncode == 0:
                break
            err = (r.stderr or r.stdout)[-2000:]
            print(f"  yt-dlp失敗({attempt}/{retry}): {err[:300]}")
            time.sleep(2 * attempt)
        except subprocess.TimeoutExpired:
            err = "timeout"
            print(f"  yt-dlp timeout({attempt}/{retry})")
    after = set(glob.glob(os.path.join(out_dir, "_tmp_*")))
    new_files = sorted(after - before)
    # 画像orig化：yt-dlpがlargeを落としてきた場合の保険（pbs URLsはyt-dlp内部なので基本不要）
    return new_files, err


def ytdlp_info(tweet_url):
    """メタ取得用 dump-json（失敗しても空dict）"""
    cmd = [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-warnings", "--skip-download", tweet_url]
    if os.path.exists(COOKIES_TXT):
        cmd.extend(["--cookies", COOKIES_TXT])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60)
        if r.returncode == 0 and r.stdout.strip():
            # 複数行JSONの場合は先頭行
            line = r.stdout.strip().splitlines()[0]
            return json.loads(line)
    except Exception as e:
        print(f"  info取得失敗: {e}")
    return {}


def rename_to_spec(tmp_path, out_dir, info, tweet_id, idx):
    username = info.get("uploader_id") or info.get("uploader") or "unknown"
    username = re.sub(r"[^0-9A-Za-z_]+", "_", str(username))[:30]
    ts = info.get("timestamp")
    if ts:
        datestr = datetime.datetime.fromtimestamp(ts).strftime("%Y%m%d")
    else:
        datestr = datetime.datetime.now().strftime("%Y%m%d")
    ext = os.path.splitext(tmp_path)[1].lower().lstrip(".") or "bin"
    # jpg/jpeg統一などはしない（元形式維持）
    new_name = f"{datestr}_{tweet_id}_{idx}_{username}.{ext}"
    new_path = os.path.join(out_dir, new_name)
    # 同名回避
    n = 1
    base, extension = os.path.splitext(new_path)
    while os.path.exists(new_path):
        new_path = f"{base}_{n}{extension}"
        n += 1
    os.rename(tmp_path, new_path)
    return new_path, new_name


def main():
    ap = argparse.ArgumentParser(description="Xいいね画像・動画ダウンローダー")
    ap.add_argument("--login", action="store_true", help="初回ログイン保存")
    ap.add_argument("--dry-run", action="store_true", help="URL抽出のみ")
    ap.add_argument("--count", type=int, default=None, help="取得件数上書き")
    ap.add_argument("--headless", action="store_true", help="ヘッドレスで実行")
    args = ap.parse_args()

    cfg = load_config()
    target = args.count or cfg.get("target_count", 100)
    out_dir = os.path.join(BASE_DIR, cfg.get("out_dir", "downloads"))
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(AUTH_DIR, exist_ok=True)
    log_path = setup_logging(out_dir)
    print(f"log: {log_path}")

    if args.login:
        do_login(headless=args.headless or cfg.get("headless", False))
        return

    if not os.path.exists(STORAGE_STATE):
        print("storage_stateがありません。まず `python main.py --login` を実行してください。")
        print("ブラウザでXにログイン→Enterで保存されます。")
        return

    export_cookies_txt()

    urls = fetch_likes(
        target_count=target,
        scroll_wait=cfg.get("scroll_wait_sec", 2),
        max_empty=cfg.get("max_empty_scrolls", 5),
        headless=args.headless or cfg.get("headless", False),
    )
    if not urls:
        print("URLが0件でした。DOM変更またはログイン切れの可能性があります。logsを確認してください。")
        return

    if args.dry_run:
        print("=== DRY-RUN ===")
        for u in urls[:target]:
            print(u)
        print(f"計{len(urls)}件（DLなし）")
        return

    seen_set, seen_path = load_seen(out_dir)
    ok, skip, fail = 0, 0, 0
    for i, url in enumerate(urls, 1):
        tid = extract_tweet_id(url) or url
        if tid in seen_set:
            skip += 1
            continue
        print(f"[{i}/{len(urls)}] {url}")
        try:
            info = ytdlp_info(url)
            files, err = ytdlp_download(url, out_dir, retry=cfg.get("retry", 3))
            if not files:
                # 本文のみツイート等はここに来る
                print("  メディアなしor取得不可→スキップ")
                # 本文のみを毎回再試行しないようseenに入れる
                seen_set.add(tid)
                save_seen(seen_set, seen_path)
                skip += 1
                continue
            saved = []
            for idx, tmp in enumerate(files):
                new_path, new_name = rename_to_spec(tmp, out_dir, info, tid, idx)
                saved.append(new_name)
            rec = {
                "tweet_url": url,
                "tweet_id": tid,
                "username": info.get("uploader_id") or info.get("uploader") or "",
                "created_at": info.get("upload_date") or "",
                "text": (info.get("description") or info.get("title") or "")[:200],
                "media_type": "video" if any(x.endswith(".mp4") for x in saved) else "image",
                "saved_files": saved,
                "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            append_meta(out_dir, rec)
            seen_set.add(tid)
            save_seen(seen_set, seen_path)
            ok += 1
            print(f"  保存: {saved}")
        except Exception as e:
            fail += 1
            print(f"  例外スキップ: {e}")
        time.sleep(1)

    print(f"完了: 成功{ok} / スキップ{skip} / 失敗{fail} / 全体{len(urls)}")


if __name__ == "__main__":
    main()
