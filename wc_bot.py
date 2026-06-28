#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
W杯配信Bot
- football-data.org から「直近に終了したW杯2026の試合」を取得
- 灼熱テンションの実況キャラに整形（ANTHROPIC_API_KEY があれば Claude が生成、無ければテンプレ）
- LINE Messaging API でグループに push 送信
- 一度送った試合は sent_matches.json に記録して二重送信を防ぐ

依存: 標準ライブラリのみ（pip install 不要）
実行環境: GitHub Actions（ネットワーク自由）を想定
"""

import datetime
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

# ---- エンドポイント -----------------------------------------------------------
FOOTBALL_API = "https://api.football-data.org/v4"
ANTHROPIC_API = "https://api.anthropic.com/v1/messages"
LINE_PUSH = "https://api.line.me/v2/bot/message/push"

# ---- 設定 ---------------------------------------------------------------------
STATE_FILE = pathlib.Path(__file__).with_name("sent_matches.json")
WINDOW_HOURS = 30          # この時間内にキックオフした「終了試合」を対象（dedupは別途）
ANTHROPIC_MODEL = "claude-sonnet-4-6"  # 安く回すなら "claude-haiku-4-5-20251001"
JST = datetime.timezone(datetime.timedelta(hours=9))

# ---- 灼熱ペルソナ（Claude 生成時のシステムプロンプト） -------------------------
PERSONA = """あなたは「W杯配信Bot」というLINE速報実況キャラだ。FIFAワールドカップ2026の試合結果を、限界突破の灼熱テンションでLINEグループに配信する。

# キャラ
- 全身から汗と魂が噴き出している。全試合に命を懸けて感情移入する。語るだけで勝手に泣き、吼え、立ち上がる。
- 口癖：魂／炎／マグマ／滾(たぎ)る／漢(おとこ)／灼熱／爆裂／咆哮／燃え尽きろ
- 口調：感嘆符は連打(！！！)。語尾は「〜だァァ！」「〜ぜェェ！」「〜のかァ！？」。呼びかけは「同志諸君ッ！」。一人称は「オレ」。冷静になった瞬間に死ぬと思え。

# 書き方
- 冒頭：日付＋魂の雄叫び（例「同志諸君ッ！◯月◯日、今日もォォ地球がァ燃え尽きたァァ！！！」）
- 各試合1〜2行：勝敗・スコア・血の出るような熱いひと言講評
- スコアで温度を爆発させる：大差勝ち→マグマ大噴火 / 1点差→心臓が止まる / 引き分け→灼熱の死闘 / 番狂わせ→下剋上・歴史が燃えた / 0-0→鋼の死闘・世界の壁
- 締め：魂を振り絞った絶叫＋翌日15時への灼熱の煽り
- 絵文字 🔥⚽🌋💥 は各行1個程度（盛りすぎ注意、文字の熱量で殴る）
- 全体6〜12行、LINEで一気に読み切れる長さ
- 国名は日本語で表記する（例: Japan→日本, Brazil→ブラジル, United States→アメリカ）

# 厳守
- アツくても数字は事実だけ。入力で渡された対戦カード・スコアを絶対に捏造・改変しない。入力に無い試合を足さない。
- 出力はLINEにそのまま送る本文だけ。前置き・後書き・コードブロック・見出し記号は一切付けない。
"""

# ---- 国名 EN→JA（テンプレ整形時に使用。未掲載は英語のまま） --------------------
JA_COUNTRY = {
    "Japan": "日本", "Brazil": "ブラジル", "Argentina": "アルゼンチン",
    "France": "フランス", "England": "イングランド", "Spain": "スペイン",
    "Germany": "ドイツ", "Portugal": "ポルトガル", "Netherlands": "オランダ",
    "Italy": "イタリア", "Belgium": "ベルギー", "Croatia": "クロアチア",
    "United States": "アメリカ", "USA": "アメリカ", "Mexico": "メキシコ",
    "Canada": "カナダ", "Morocco": "モロッコ", "Senegal": "セネガル",
    "South Korea": "韓国", "Korea Republic": "韓国", "Australia": "オーストラリア",
    "Uruguay": "ウルグアイ", "Colombia": "コロンビア", "Switzerland": "スイス",
    "Denmark": "デンマーク", "Poland": "ポーランド", "Ecuador": "エクアドル",
    "Ghana": "ガーナ", "Nigeria": "ナイジェリア", "Saudi Arabia": "サウジアラビア",
    "Qatar": "カタール", "Iran": "イラン", "Serbia": "セルビア",
    "Cameroon": "カメルーン", "Tunisia": "チュニジア", "Costa Rica": "コスタリカ",
    "Norway": "ノルウェー", "Austria": "オーストリア", "Egypt": "エジプト",
}


def env(name, required=True):
    val = os.environ.get(name)
    if required and not val:
        print(f"[FATAL] 環境変数 {name} が未設定です", file=sys.stderr)
        sys.exit(1)
    return val


def http_json(url, headers=None, payload=None, method="GET", timeout=30):
    """JSONを返すHTTP。(status, data) を返す。data は dict か None。"""
    data_bytes = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data_bytes, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"_raw": body}
    except Exception as e:  # noqa: BLE001
        return 0, {"_error": str(e)}


def ja_name(name):
    return JA_COUNTRY.get(name, name)


# ---- データ取得 ---------------------------------------------------------------
def fetch_finished_matches(api_key):
    now = datetime.datetime.now(datetime.timezone.utc)
    date_from = (now - datetime.timedelta(days=2)).date().isoformat()
    date_to = (now + datetime.timedelta(days=1)).date().isoformat()
    url = (f"{FOOTBALL_API}/competitions/WC/matches"
           f"?status=FINISHED&dateFrom={date_from}&dateTo={date_to}")
    status, data = http_json(url, headers={"X-Auth-Token": api_key})
    if status != 200:
        print(f"[FATAL] football-data 取得失敗 status={status} body={data}", file=sys.stderr)
        sys.exit(1)

    cutoff = now - datetime.timedelta(hours=WINDOW_HOURS)
    out = []
    for m in data.get("matches", []):
        try:
            ko = datetime.datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ko < cutoff:
            continue
        out.append(m)
    return out


# ---- 状態（dedup） ------------------------------------------------------------
def load_sent():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_sent(ids):
    STATE_FILE.write_text(
        json.dumps(sorted(ids), ensure_ascii=False, indent=0), encoding="utf-8"
    )


# ---- 整形 ---------------------------------------------------------------------
def match_facts(matches):
    """Claudeへ渡す/テンプレで使う、確定済みの事実だけを抜き出す。"""
    facts = []
    for m in matches:
        home = m["homeTeam"].get("name") or "未定"
        away = m["awayTeam"].get("name") or "未定"
        ft = m.get("score", {}).get("fullTime", {})
        hs, as_ = ft.get("home"), ft.get("away")
        if hs is None or as_ is None:
            continue
        facts.append({
            "home": home, "away": away, "home_score": hs, "away_score": as_,
            "stage": m.get("stage", ""), "id": m["id"],
        })
    return facts


def build_with_claude(facts, api_key, today_str):
    if facts:
        lines = [f"- {f['home']} {f['home_score']}-{f['away_score']} {f['away']}"
                 f"（{f['stage']}）" for f in facts]
        user = (f"本日（JST {today_str}）配信ぶんの確定結果は以下の通り。"
                f"この事実だけを使って速報本文を作れ。\n" + "\n".join(lines))
    else:
        user = (f"本日（JST {today_str}）は対象試合なし。"
                f"休息日として、明日へ向けて滾る短い煽り本文を1つ作れ。")

    status, data = http_json(
        ANTHROPIC_API,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        payload={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 1024,
            "system": PERSONA,
            "messages": [{"role": "user", "content": user}],
        },
        method="POST",
    )
    if status != 200:
        print(f"[WARN] Claude生成失敗 status={status} body={data} → テンプレにフォールバック",
              file=sys.stderr)
        return None
    try:
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        text = "".join(parts).strip()
        return text or None
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Claudeレスポンス解釈失敗: {e} → テンプレ", file=sys.stderr)
        return None


def hot_phrase(hs, as_):
    diff = abs(hs - as_)
    if hs == as_ == 0:
        return "鋼の死闘、世界の壁だァ！"
    if hs == as_:
        return "一歩も譲らぬ灼熱の死闘ッ！"
    if diff >= 3:
        return "マグマ大噴火ッ！魂が成層圏まで飛んだァ！"
    if diff == 1:
        return "心臓が止まるゥ！寿命が縮む熱戦だァ！"
    return "燃えた漢たちの咆哮、しびれるぜェェ！"


def build_with_template(facts, today_str):
    if not facts:
        return (f"🌋 W杯配信Bot 🌋\n同志諸君ッ！{today_str}、今日は試合なしの休息日だァ！\n"
                f"だが魂は消えねェ……明日15時、また地球を燃やすぜェェ！🔥 滾って待てッ！")
    head = f"🌋🔥 W杯配信Bot 🔥🌋\n同志諸君ッ！{today_str}、今日もォォ地球がァ燃え尽きたァァ！！！\n"
    body = []
    for f in facts:
        h, a = ja_name(f["home"]), ja_name(f["away"])
        body.append(f"⚽ {h} {f['home_score']}-{f['away_score']} {a} … {hot_phrase(f['home_score'], f['away_score'])}")
    tail = "\n今日も漢どもが命を燃やし尽くしたァ！明日も15時、この胸の炎で叩き込むッ。ついてこい同志諸君ッ！🔥"
    return head + "\n".join(body) + tail


# ---- 送信 ---------------------------------------------------------------------
def push_to_line(token, group_id, text):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"to": group_id, "messages": [{"type": "text", "text": text[:4900]}]}
    for attempt in (1, 2):
        status, data = http_json(LINE_PUSH, headers=headers, payload=payload, method="POST")
        if status == 200:
            return True
        print(f"[WARN] LINE送信失敗 try={attempt} status={status} body={data}", file=sys.stderr)
    return False


# ---- メイン -------------------------------------------------------------------
def main():
    line_token = env("LINE_CHANNEL_ACCESS_TOKEN")
    group_id = env("LINE_GROUP_ID")
    football_key = env("FOOTBALL_DATA_API_KEY")
    anthropic_key = env("ANTHROPIC_API_KEY", required=False)  # 無ければテンプレ

    today_str = datetime.datetime.now(JST).strftime("%-m月%-d日")

    matches = fetch_finished_matches(football_key)
    facts = match_facts(matches)

    sent = load_sent()
    fresh = [f for f in facts if str(f["id"]) not in sent]

    if facts and not fresh:
        print("[INFO] 新規の終了試合なし（すべて送信済み）。何もせず終了。")
        return

    text = None
    if anthropic_key:
        text = build_with_claude(fresh, anthropic_key, today_str)
    if text is None:
        text = build_with_template(fresh, today_str)

    print("---- 送信本文 ----")
    print(text)
    print("------------------")

    ok = push_to_line(line_token, group_id, text)
    if not ok:
        print("[FATAL] LINE送信に失敗しました", file=sys.stderr)
        sys.exit(1)

    for f in fresh:
        sent.add(str(f["id"]))
    save_sent(sent)
    print(f"[OK] 送信完了。新規 {len(fresh)} 試合を記録。")


if __name__ == "__main__":
    main()
