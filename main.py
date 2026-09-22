# -*- coding: utf-8 -*-
"""Xいいね画像・動画ダウンローダー (gallery-dl + cookies, ブラウザ不要)

通常運用はブラウザを使わない。必要なのは auth/ のcookieだけ。
初回ログインだけ --login (ブラウザ) か --from-cookies-txt (拡張機能出力) を使う。
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
AUTH_DIR = os.path.join(BASE_DIR, "auth")
STORAGE_STATE = os.path.join(AUTH_DIR, "storage_state.json")
COOKIES_TXT = os.path.join(AUTH_DIR, "cookies.txt")
ARCHIVE_PATH = os.path.join(AUTH_DIR, "dl_archive.sqlite3")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def setup_logging(out_dir):
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
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


def ensure_auth(silent=False):
    """cookie認証の有効確認。auth_tokenが無ければ導入案内。"""
    if not os.path.exists(STORAGE_STATE):
        if not silent:
            print("storage_stateがありません。次のどちらかでログインしてください：")
            print("  python main.py --login")
            print("  python main.py --from-cookies-txt <cookies.txtのパス>")
        return False
    try:
        with open(STORAGE_STATE, encoding="utf-8") as f:
            state = json.load(f)
        names = {c.get("name") for c in state.get("cookies", [])}
    except Exception:
        names = set()
    if "auth_token" not in names:
        if not silent:
            print("!! auth_tokenがありません。次のどちらかでログインし直してください：")
            print("  python main.py --login")
            print("  python main.py --from-cookies-txt <cookies.txtのパス>")
        return False
    return True


def looks_like_auth_error(output):
    """gallery-dl出力が認証切れっぽいか。"""
    o = (output or "").lower()
    keys = ["401", "403", "unauthorized", "forbidden", "authenticate",
            "login required", "could not retrieve", "bad credentials"]
    return any(k in o for k in keys)


def do_login(headless=False):
    """初回ログイン用。ブラウザを開くので手動でXにログインしてEnter。
    注意: 「Googleでログイン」ボタンは自動ブラウザでは白画面になるため使わない。
    XのID（ユーザー名/メール/電話番号）＋パスワードで直接ログインすること。"""
    from playwright.sync_api import sync_playwright

    os.makedirs(AUTH_DIR, exist_ok=True)
    profile_dir = os.path.join(AUTH_DIR, "chrome_profile")
    os.makedirs(profile_dir, exist_ok=True)
    print("=== first login ===")
    print("開いたブラウザでXに手動ログインし、このコンソールでEnterを押してください。")
    print("!!「Googleでログイン」は白画面になるため使わないでください。")
    print("  Xのユーザー名/メール/電話番号＋パスワードで直接ログインしてください。")
    print("If x.com/login shows error, use the address bar to go to x.com/ and log in there.")
    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
        # 内蔵Chromiumを使う。Real Chrome(channel="chrome")はXに接続拒否されるため使わない。
        ctx = p.chromium.launch_persistent_context(
            profile_dir, headless=False,
            viewport={"width": 1280, "height": 900}, args=args,
        )
        print("ブラウザを起動しました。")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://x.com/", wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"!! x.comを開けませんでした: {e}")
            print("   ネットワーク／プロキシを確認して再実行してください。")
            ctx.close()
            return
        page.bring_to_front()
        # 読み込み待ち（XはJSレンダリングのため数秒かかる）
        try:
            page.wait_for_url("**/x.com/**", timeout=30000)
        except Exception:
            pass
        time.sleep(5)
        print(f"表示中: {page.url} / {page.title()}")
        try:
            input("ログイン完了後にEnter > ")
        except KeyboardInterrupt:
            pass
        # 既存の正規ログインをゲスト上書きで壊さないようバックアップ
        if os.path.exists(STORAGE_STATE):
            try:
                bak = STORAGE_STATE + ".bak"
                with open(STORAGE_STATE, encoding="utf-8") as f:
                    old = json.load(f)
                old_names = {c.get("name") for c in old.get("cookies", [])}
                if "auth_token" in old_names:
                    shutil.copyfile(STORAGE_STATE, bak)
                    print(f"既存の有効ログインをバックアップ: {bak}")
            except Exception:
                pass
        ctx.storage_state(path=STORAGE_STATE)
        # ログイン検証
        try:
            with open(STORAGE_STATE, encoding="utf-8") as f:
                state = json.load(f)
            names = {c.get("name") for c in state.get("cookies", [])}
        except Exception:
            names = set()
        ctx.close()
    if "auth_token" in names:
        print(f"保存しました: {STORAGE_STATE} (auth_token確認OK)")
        export_cookies_txt()
    else:
        print(f"!! {STORAGE_STATE} にauth_tokenがありません。ログインが完了していません。")
        print("   ブラウザ上で https://x.com/home が開けることを確認してからEnterを押してください。")
        print("   Googleボタンは使えません。ID＋パスワードでログインしてください。")


def export_cookies_txt():
    """storage_state.json -> gallery-dl用 Netscape cookies.txt"""
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


def import_cookies_txt(path):
    """拡張機能が出力したNetscape cookies.txt -> storage_state.jsonに変換する。"""
    if not os.path.exists(path):
        print(f"!! ファイルが見つかりません: {path}")
        return False
    pw_cookies = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                parts = line.split()
            if len(parts) < 7:
                continue
            domain, flag, cpath, secure, exp, name, val = parts[:7]
            try:
                exp = int(exp)
            except ValueError:
                exp = 2147483647
            pw_cookies.append({
                "name": name,
                "value": val,
                "domain": domain,
                "path": cpath or "/",
                "expires": exp if exp > 0 else 2147483647,
                "httpOnly": False,
                "secure": secure.upper() == "TRUE",
                "sameSite": "Lax",
            })
    # x.com系だけに絞る（他サイト混入防止）
    pw_cookies = [c for c in pw_cookies
                  if "x.com" in c["domain"] or "twitter.com" in c["domain"]]
    if not pw_cookies:
        print("!! x.com系クッキーが見つかりません。x.comを開いた状態で出力してください。")
        return False
    os.makedirs(AUTH_DIR, exist_ok=True)
    with open(STORAGE_STATE, "w", encoding="utf-8") as f:
        json.dump({"cookies": pw_cookies, "origins": []}, f, ensure_ascii=False, indent=2)
    print(f"保存: {STORAGE_STATE} ({len(pw_cookies)} cookies)")
    names = {c["name"] for c in pw_cookies}
    if "auth_token" not in names:
        print("!! auth_tokenが見つかりません。Xにログインした状態でcookies.txtを出力してください。")
        return False
    print("auth_token確認OK")
    export_cookies_txt()
    return True


def run_gallery_dl(likes_url, target, staging_dir, dry_run=False):
    """gallery-dlでいいね欄からメディア取得。ブラウザ不要、cookieのみ。"""
    os.makedirs(staging_dir, exist_ok=True)
    base = [
        sys.executable, "-m", "gallery_dl",
        "--cookies", COOKIES_TXT,
        "--range", f"1-{target}",
        "--retries", "3",
        "--sleep", "1",
    ]
    if dry_run:
        cmd = base + ["--get-urls", likes_url]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", timeout=600)
        urls = [l for l in (r.stdout or "").splitlines() if l.strip()]
        print(f"メディアURL {len(urls)}件（DLなし）:")
        for u in urls:
            print(u)
        return True
    cmd = base + ["--write-metadata", "--download-archive", ARCHIVE_PATH,
                  "-d", staging_dir, likes_url]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", timeout=3600)
    out = (r.stderr or "") + "\n" + (r.stdout or "")
    if r.returncode != 0:
        tail = out[-1500:]
        print(f"gallery-dl終了コード={r.returncode}: {tail[:500]}")
        print("stagingに残った分は取り込みます。")
        return False, out
    return True, out


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


def rename_to_spec(tmp_path, out_dir, username, datestr, tweet_id, idx):
    username = re.sub(r"[^0-9A-Za-z_]+", "_", str(username or "unknown"))[:30]
    ext = os.path.splitext(tmp_path)[1].lower().lstrip(".") or "bin"
    new_name = f"{datestr}_{tweet_id}_{idx}_{username}.{ext}"
    new_path = os.path.join(out_dir, new_name)
    n = 1
    base, extension = os.path.splitext(new_path)
    while os.path.exists(new_path):
        new_path = f"{base}_{n}{extension}"
        n += 1
    os.rename(tmp_path, new_path)
    return new_path, new_name


def process_staging(out_dir, staging_dir, target):
    """stagingのDL済みファイルを仕様名にリネームして取り込む。"""
    seen_set, seen_path = load_seen(out_dir)
    ok, skip = 0, 0
    paths = sorted(glob.glob(os.path.join(staging_dir, "**", "*"), recursive=True))
    # gallery-dl emits one file per media item (e.g. 123_1.jpg, 123_2.jpg).
    # Group by tweet before consulting/updating seen.json so later photos in
    # the same post are not mistaken for already processed posts.
    media_by_tweet = {}
    for path in paths:
        if os.path.isdir(path) or path.endswith(".json"):
            continue
        m = re.match(r"^(\d+)_\d+\.([A-Za-z0-9]+)$", os.path.basename(path))
        if not m:
            continue
        tid = m.group(1)
        media_by_tweet.setdefault(tid, []).append(path)

    for tid, media_paths in media_by_tweet.items():
        media_paths.sort(key=lambda p: int(re.match(r"^\d+_(\d+)\.", os.path.basename(p)).group(1)))
        metas = []
        for path in media_paths:
            meta_path = path + ".json"
            meta = {}
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    pass
                try:
                    os.remove(meta_path)
                except Exception:
                    pass
            metas.append(meta)
        meta = next((item for item in metas if item), {})
        if tid in seen_set:
            for path in media_paths:
                try:
                    os.remove(path)
                except Exception:
                    pass
            skip += 1
            continue
        author = (meta.get("author") or {}).get("name", "") if isinstance(meta.get("author"), dict) else ""
        date_s = meta.get("date", "") or ""
        try:
            datestr = datetime.datetime.strptime(date_s, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d")
        except Exception:
            datestr = datetime.datetime.now().strftime("%Y%m%d")
        text = (meta.get("content") or "")[:200]
        mtype = meta.get("type", "")
        media_type = "video" if mtype == "video" else "image"
        saved = []
        for idx, path in enumerate(media_paths):
            _, new_name = rename_to_spec(path, out_dir, author, datestr, tid, idx)
            saved.append(new_name)
        append_meta(out_dir, {
            "tweet_url": f"https://x.com/i/status/{tid}",
            "tweet_id": tid,
            "username": author,
            "created_at": date_s,
            "text": text,
            "media_type": "video" if any(
                item.get("type") == "video" for item in metas
            ) or media_type == "video" else "image",
            "saved_files": saved,
            "fetched_at": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        seen_set.add(tid)
        ok += 1
        print(f"  保存: {', '.join(saved)}")
    save_seen(seen_set, seen_path)
    # 空になった下位ディレクトリ掃除
    for path in sorted(paths, reverse=True):
        if os.path.isdir(path):
            try:
                os.rmdir(path)
            except Exception:
                pass
    print(f"走査{target}件: 保存{ok} / seenスキップ{skip} / メディアなし等{max(0, target - ok - skip)}")
    return ok


def main():
    ap = argparse.ArgumentParser(description="Xいいね画像・動画ダウンローダー")
    ap.add_argument("--login", action="store_true", help="first login (manual browser, one-time only)")
    ap.add_argument("--from-cookies-txt", metavar="PATH", default=None, help="import Netscape cookies.txt exported by browser extension")
    ap.add_argument("--dry-run", action="store_true", help="URL extract only")
    ap.add_argument("--count", type=int, default=None, help="取得件数上書き")
    args = ap.parse_args()

    cfg = load_config()
    target = args.count or cfg.get("target_count", 100)
    username = cfg.get("username", "")
    out_dir = os.path.join(BASE_DIR, cfg.get("out_dir", "downloads"))
    staging_dir = os.path.join(out_dir, "_staging")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(AUTH_DIR, exist_ok=True)
    log_path = setup_logging(out_dir)
    print(f"log: {log_path}")

    if args.from_cookies_txt:
        import_cookies_txt(args.from_cookies_txt)
        return

    if args.login:
        do_login()
        return

    if not username and not args.dry_run:
        print('config.jsonに "username" を設定してください（例: "neoenox"）。')
        return
    likes_url = f"https://x.com/{username}/likes" if username else "https://x.com/i/likes"

    if not ensure_auth():
        print("自動でログイン画面を開きます。ログイン後に処理を続行します。")
        do_login()
        if not ensure_auth():
            return
    export_cookies_txt()

    if args.dry_run:
        print("=== DRY-RUN ===")
        run_gallery_dl(likes_url, target, staging_dir, dry_run=True)
        return

    print(f"いいね取得開始: target={target} ({likes_url})")
    ok, out = run_gallery_dl(likes_url, target, staging_dir)
    if not ok and looks_like_auth_error(out):
        print("セッション切れの可能性。自動でログイン画面を開きます。")
        do_login()
        if not ensure_auth():
            return
        export_cookies_txt()
        print("再試行します。")
        run_gallery_dl(likes_url, target, staging_dir)
    process_staging(out_dir, staging_dir, target)


if __name__ == "__main__":
    main()
