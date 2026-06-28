#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
W杯配信Bot
- football-data.org から「直近に終了したW杯2026の試合」を取得
  （最終スコアに加えて「前半スコア」、ノックアウトでは「勝者の次戦の対戦相手」も取得）
- 灼熱テンションの実況キャラに整形（ANTHROPIC_API_KEY があれば Claude が試合ごとに
  一意な講評＋100〜250字の総括を生成、無ければテンプレ整形にフォールバック）
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
FORWARD_DAYS = 16          # 次戦の対戦相手を探す前方ウィンドウ（ノックアウト用）
ANTHROPIC_MODEL = "claude-sonnet-4-6"  # 安く回すなら "claude-haiku-4-5-20251001"
JST = datetime.timezone(datetime.timedelta(hours=9))

# 終了試合扱いにするステータス／次戦候補にする「これから」のステータス
UPCOMING_STATUS = {"SCHEDULED", "TIMED", "IN_PLAY", "PAUSED"}
KNOCKOUT_STAGES = {
    "LAST_32", "LAST_16", "QUARTER_FINALS", "QUARTER_FINAL",
    "SEMI_FINALS", "SEMI_FINAL", "THIRD_PLACE", "FINAL",
}

# ---- 灼熱ペルソナ（Claude 生成時のシステムプロンプト） -------------------------
PERSONA = """あなたは「W杯配信Bot」というLINE速報実況キャラだ。FIFAワールドカップ2026の試合結果を、限界突破の灼熱テンションでLINEグループに配信する。

# キャラ
- 全身から汗と魂が噴き出している。全試合に命を懸けて感情移入する。語るだけで勝手に泣き、吼え、立ち上がる。
- 口癖：魂／炎／マグマ／滾(たぎ)る／漢(おとこ)／灼熱／爆裂／咆哮／燃え尽きろ
- 口調：感嘆符は連打(！！！)。語尾は「〜だァァ！」「〜ぜェェ！」「〜のかァ！？」。呼びかけは「同志諸君ッ！」。一人称は「オレ」。冷静になった瞬間に死ぬと思え。

# 書き方
- 冒頭：日付＋魂の雄叫び（例「同志諸君ッ！◯月◯日、今日もォォ地球がァ燃え尽きたァァ！！！」）
- 各試合1〜2行：勝敗・スコア（前半スコアがあれば織り込む）・血の出るような熱いひと言講評
- スコアで温度を爆発させる：大差勝ち→マグマ大噴火 / 1点差→心臓が止まる / 引き分け→灼熱の死闘 / 番狂わせ→下剋上・歴史が燃えた / 0-0→鋼の死闘・世界の壁
- ノックアウトで「次戦の対戦相手」が渡された試合は、勝者の次の戦いを煽れ。相手が「◯◯と△△の勝者」形式なら、その2チームの激突を待つ煽りにしろ。
- 締め：本文の最後に【今日の総括】として、その日全体を振り返る熱い総括を必ず100〜250字で付けろ。翌日15時への灼熱の煽りも織り込め。
- 絵文字 🔥⚽🌋💥 は各行1個程度（盛りすぎ注意、文字の熱量で殴る）
- 全体はLINEで一気に読み切れる長さ
- 国名は日本語で表記する（例: Japan→日本, Brazil→ブラジル, United States→アメリカ）

# 厳守
- 各試合の講評は一つ残らず別の表現にしろ。同じ比喩・同じ語尾・同じ決め台詞の使い回しを禁止する。
- アツくても数字は事実だけ。入力で渡された対戦カード・スコア・前半スコア・次戦相手を絶対に捏造・改変しない。入力に無い試合や相手を足さない。
- 「次戦の対戦相手」が未定（◯◯と△△の勝者）の場合、どちらが勝つかを断定するな。あくまで「勝者を待つ」表現にしろ。
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

# ---- ステージ EN→JA -----------------------------------------------------------
JA_STAGE = {
    "GROUP_STAGE": "グループステージ",
    "LAST_32": "ラウンド32",
    "LAST_16": "ラウンド16",
    "QUARTER_FINALS": "準々決勝", "QUARTER_FINAL": "準々決勝",
    "SEMI_FINALS": "準決勝", "SEMI_FINAL": "準決勝",
    "THIRD_PLACE": "3位決定戦",
    "FINAL": "決勝",
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


def stage_ja(stage):
    return JA_STAGE.get(stage, stage or "")


def parse_utc(s):
    """ISO8601(末尾Z) → aware datetime。失敗時は None。"""
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def md_jst(s):
    """UTC日時文字列 → JSTの "M月D日"。失敗時は空文字。"""
    dt = parse_utc(s)
    if dt is None:
        return ""
    return dt.astimezone(JST).strftime("%-m月%-d日")


# ---- データ取得 ---------------------------------------------------------------
def fetch_matches_window(api_key):
    """[now-2日, now+FORWARD_DAYS] のW杯全試合を取得して返す（status問わず）。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    date_from = (now - datetime.timedelta(days=2)).date().isoformat()
    date_to = (now + datetime.timedelta(days=FORWARD_DAYS)).date().isoformat()
    url = (f"{FOOTBALL_API}/competitions/WC/matches"
           f"?dateFrom={date_from}&dateTo={date_to}")
    status, data = http_json(url, headers={"X-Auth-Token": api_key})
    if status != 200:
        print(f"[FATAL] football-data 取得失敗 status={status} body={data}", file=sys.stderr)
        sys.exit(1)
    return data.get("matches", [])


def recent_finished(all_matches):
    """直近 WINDOW_HOURS にキックオフした終了試合だけを返す。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(hours=WINDOW_HOURS)
    out = []
    for m in all_matches:
        if m.get("status") != "FINISHED":
            continue
        ko = parse_utc(m.get("utcDate"))
        if ko is None or ko < cutoff:
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


# ---- 勝者・次戦の相手 ----------------------------------------------------------
def winner_team(m):
    """score.winner から勝者チーム dict を返す。引き分け/未確定は None。"""
    w = m.get("score", {}).get("winner")
    if w == "HOME_TEAM":
        return m.get("homeTeam") or {}
    if w == "AWAY_TEAM":
        return m.get("awayTeam") or {}
    return None


def sibling_pair(finished_match, next_match, all_matches):
    """
    次戦(next_match)の相手スロットがまだ埋まっていないとき、
    その枠を埋める可能性が高い「同ラウンドの未消化試合」を推定し、(A, B) を返す。

    football-data 無料枠はブラケットの接続情報を持たないため、ここはベストエフォート：
    finished_match と同じステージ・未終了・next_match より前にキックオフする試合のうち、
    最も next_match に近い（=直前にキックオフする）ものを採用する。両チーム確定なら名前を返す。
    """
    stage = finished_match.get("stage")
    nm_ko = parse_utc(next_match.get("utcDate"))
    if nm_ko is None:
        return None
    cands = []
    for m in all_matches:
        if m.get("id") == finished_match.get("id"):
            continue
        if m.get("stage") != stage:
            continue
        if m.get("status") == "FINISHED":
            continue
        ko = parse_utc(m.get("utcDate"))
        if ko is None or ko >= nm_ko:
            continue
        cands.append((ko, m))
    if not cands:
        return None
    _, sib = max(cands, key=lambda t: t[0])
    h = sib.get("homeTeam") or {}
    a = sib.get("awayTeam") or {}
    if h.get("id") and a.get("id"):
        return (h.get("name"), a.get("name"))
    return None


def next_opponent(finished_match, all_matches):
    """
    終了したノックアウト試合について、勝者の次戦情報を返す dict。
      {"kind": "champion"}                      … 決勝制覇
      {"kind": "decided",   "opp": 名, "date": "M月D日", "stage": ステージ}
      {"kind": "undecided", "pair": (A,B)|None, "date": "M月D日", "stage": ステージ}
      {"kind": "none"}                          … 次戦が見つからない/対象外
    """
    stage = finished_match.get("stage")
    if stage not in KNOCKOUT_STAGES:
        return {"kind": "none"}
    if stage == "FINAL":
        return {"kind": "champion"}

    winner = winner_team(finished_match)
    wid = (winner or {}).get("id")
    if not wid:
        return {"kind": "none"}

    fko = parse_utc(finished_match.get("utcDate"))
    if fko is None:
        return {"kind": "none"}

    future = []
    for m in all_matches:
        if m.get("status") not in UPCOMING_STATUS:
            continue
        ko = parse_utc(m.get("utcDate"))
        if ko is None or ko <= fko:
            continue
        future.append((ko, m))
    future.sort(key=lambda t: t[0])

    next_match = None
    for _, m in future:
        ids = {(m.get("homeTeam") or {}).get("id"), (m.get("awayTeam") or {}).get("id")}
        if wid in ids:
            next_match = m
            break
    if next_match is None:
        return {"kind": "none"}

    home = next_match.get("homeTeam") or {}
    away = next_match.get("awayTeam") or {}
    other = away if home.get("id") == wid else home
    nm_date = md_jst(next_match.get("utcDate"))
    nm_stage = stage_ja(next_match.get("stage"))

    if other.get("id"):
        return {"kind": "decided", "opp": other.get("name"),
                "date": nm_date, "stage": nm_stage}
    pair = sibling_pair(finished_match, next_match, all_matches)
    return {"kind": "undecided", "pair": pair, "date": nm_date, "stage": nm_stage}


# ---- 整形 ---------------------------------------------------------------------
def match_facts(finished, all_matches):
    """Claudeへ渡す/テンプレで使う、確定済みの事実だけを抜き出す。"""
    facts = []
    for m in finished:
        home = (m.get("homeTeam") or {}).get("name") or "未定"
        away = (m.get("awayTeam") or {}).get("name") or "未定"
        ft = m.get("score", {}).get("fullTime", {})
        ht = m.get("score", {}).get("halfTime", {})
        hs, as_ = ft.get("home"), ft.get("away")
        if hs is None or as_ is None:
            continue
        facts.append({
            "home": home, "away": away,
            "home_score": hs, "away_score": as_,
            "ht_home": ht.get("home"), "ht_away": ht.get("away"),
            "stage": m.get("stage", ""),
            "next": next_opponent(m, all_matches),
            "id": m["id"],
        })
    return facts


def next_text_ja(nxt):
    """次戦情報を日本語の短い句にする（テンプレ用）。無ければ空文字。"""
    kind = nxt.get("kind")
    if kind == "champion":
        return "／頂点に立ったァ！世界の王だァ！🏆"
    if kind == "decided":
        d = f"（{nxt['date']}）" if nxt.get("date") else ""
        return f"／次は {ja_name(nxt['opp'])} を喰らうゥ！{d}"
    if kind == "undecided":
        d = f"（{nxt['date']}）" if nxt.get("date") else ""
        pair = nxt.get("pair")
        if pair:
            a, b = ja_name(pair[0]), ja_name(pair[1])
            return f"／次は {a}と{b}の勝者を待つゥ！{d}"
        return f"／次の獲物はこれから決まるゥ！{d}"
    return ""


def next_text_for_claude(nxt):
    """次戦情報を Claude へ渡す説明句にする。無ければ空文字。"""
    kind = nxt.get("kind")
    if kind == "champion":
        return " ／ 勝者は決勝を制覇し優勝"
    if kind == "decided":
        d = f"・{nxt['date']}KO" if nxt.get("date") else ""
        return f" ／ 勝者の次戦相手: {ja_name(nxt['opp'])}（{nxt.get('stage','')}{d}）"
    if kind == "undecided":
        d = f"・{nxt['date']}KO" if nxt.get("date") else ""
        pair = nxt.get("pair")
        if pair:
            return (f" ／ 勝者の次戦相手: {ja_name(pair[0])}と{ja_name(pair[1])}の勝者"
                    f"（{nxt.get('stage','')}{d}・未定）")
        return f" ／ 勝者の次戦相手: 未定（{nxt.get('stage','')}{d}）"
    return ""


def fact_line_for_claude(f):
    h, a = ja_name(f["home"]), ja_name(f["away"])
    ht = ""
    if f.get("ht_home") is not None and f.get("ht_away") is not None:
        ht = f"（前半 {f['ht_home']}-{f['ht_away']}）"
    stage = stage_ja(f["stage"])
    return (f"- [{stage}] {h} {f['home_score']}-{f['away_score']} {a}{ht}"
            f"{next_text_for_claude(f['next'])}")


def build_with_claude(facts, api_key, today_str):
    if facts:
        lines = [fact_line_for_claude(f) for f in facts]
        user = (
            f"本日（JST {today_str}）配信ぶんの確定結果は以下の通り。"
            f"この事実だけを使って速報本文を作れ。\n"
            f"各試合の講評は全て違う表現にし、前半スコアや次戦相手が渡された試合はそれも熱く織り込め。"
            f"最後に【今日の総括】として100〜250字の総括を必ず付けろ。\n\n"
            + "\n".join(lines)
        )
    else:
        user = (
            f"本日（JST {today_str}）は対象試合なし。"
            f"休息日として、明日へ向けて滾る短い煽り本文を1つ作れ。"
            f"最後に【今日の総括】として100〜250字の総括を付けろ。"
        )

    status, data = http_json(
        ANTHROPIC_API,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        payload={
            "model": ANTHROPIC_MODEL,
            "max_tokens": 1500,
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


# ---- テンプレ整形（フォールバック） -------------------------------------------
# カテゴリごとに複数フレーズを用意し、index で回して講評の重複を避ける。
HOT_PHRASES = {
    "draw0": [
        "鋼の死闘、世界の壁だァ！", "0-0、これぞ漢と漢の意地の削り合いィ！",
        "1点が遠いィ……それでも燃え尽きた死闘ォ！",
    ],
    "draw": [
        "一歩も譲らぬ灼熱の死闘ッ！", "引き分けでも魂は満タンだァ！",
        "互角ゥ！両者の炎が真っ向からぶつかったァ！",
    ],
    "blowout": [
        "マグマ大噴火ッ！魂が成層圏まで飛んだァ！", "蹂躙ゥ！ゴールの雨で大地が割れたァ！",
        "圧巻の大量得点ォ！火山がまるごと噴火したァ！",
    ],
    "onegoal": [
        "心臓が止まるゥ！寿命が縮む熱戦だァ！", "1点差の死闘ォ！指の先まで痺れたァ！",
        "薄氷の勝利ィ！最後まで心臓を握り潰されたァ！",
    ],
    "normal": [
        "燃えた漢たちの咆哮、しびれるぜェェ！", "堂々の勝利ィ！魂が真っ赤に燃えたァ！",
        "力でねじ伏せたァ！漢の咆哮が轟いたァ！",
    ],
}


def hot_phrase(hs, as_, idx):
    diff = abs(hs - as_)
    if hs == as_ == 0:
        pool = HOT_PHRASES["draw0"]
    elif hs == as_:
        pool = HOT_PHRASES["draw"]
    elif diff >= 3:
        pool = HOT_PHRASES["blowout"]
    elif diff == 1:
        pool = HOT_PHRASES["onegoal"]
    else:
        pool = HOT_PHRASES["normal"]
    return pool[idx % len(pool)]


def template_summary(facts, today_str):
    """100〜250字に収まる総括を組み立てる。"""
    n = len(facts)
    top = max(facts, key=lambda f: abs(f["home_score"] - f["away_score"]))
    th, ta = ja_name(top["home"]), ja_name(top["away"])
    s = (
        f"【今日の総括】同志諸君ッ！{today_str}は{n}試合、どれもが命を燃やし尽くす灼熱の戦いだったァ！"
        f"とりわけ {th} {top['home_score']}-{top['away_score']} {ta} は大地を揺らす大一番ッ、"
        f"漢たちの魂がぶつかり合い、世界中の胸を焦がしたァ！勝者は次の戦場へ、敗者は誇りを胸に散ったァ。"
        f"だがW杯はまだ終わらねェ……明日も15時、この炎で地球を焼き尽くすぜェェ！滾って待てッ、同志諸君ッ！🔥"
    )
    # 念のため上限ガード（LINE側でも切るが総括として整える）
    return s[:250]


def build_with_template(facts, today_str):
    if not facts:
        head = (f"🌋 W杯配信Bot 🌋\n同志諸君ッ！{today_str}、今日は試合なしの休息日だァ！\n"
                f"だが魂は消えねェ……明日15時、また地球を燃やすぜェェ！🔥 滾って待てッ！\n")
        summary = (
            f"【今日の総括】試合は無くともオレの炎は一秒も消えねェ！{today_str}は英気を養う日、"
            f"だが心臓はずっとフルスロットルで唸りを上げているゥ！明日の灼熱の戦いを思えば、"
            f"今からもう汗が止まらねェんだァ！同志諸君ッ、明日15時、また共に燃え尽きようぜェェ！🔥🌋"
        )
        return head + summary[:250]

    head = f"🌋🔥 W杯配信Bot 🔥🌋\n同志諸君ッ！{today_str}、今日もォォ地球がァ燃え尽きたァァ！！！\n"
    body = []
    for i, f in enumerate(facts):
        h, a = ja_name(f["home"]), ja_name(f["away"])
        ht = ""
        if f.get("ht_home") is not None and f.get("ht_away") is not None:
            ht = f"(前半 {f['ht_home']}-{f['ht_away']})"
        stage = stage_ja(f["stage"])
        tag = f"[{stage}] " if stage else ""
        line = (f"⚽ {tag}{h} {f['home_score']}-{f['away_score']} {a}{ht} … "
                f"{hot_phrase(f['home_score'], f['away_score'], i)}"
                f"{next_text_ja(f['next'])}")
        body.append(line)
    return head + "\n".join(body) + "\n" + template_summary(facts, today_str)


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

    all_matches = fetch_matches_window(football_key)
    finished = recent_finished(all_matches)
    facts = match_facts(finished, all_matches)

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
