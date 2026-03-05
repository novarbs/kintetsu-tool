"""
青の交響曲 空席チェック & Discord通知 (GitHub Actions用)

1回の実行で全便を自動スキャンし、空席があればDiscordに通知する。
便ごとの個別指定は不要。

環境変数:
  DISCORD_WEBHOOK_URL: Discord Webhook URL (必須)
  MONITOR_DATE: 監視日 "MMDD" (例: "0404") ※未設定なら翌日
  MONITOR_GRADES: 通知するグレード (カンマ区切り, デフォルト: "デラックス,サロン,ツイン")
"""

import os
import sys
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# --- 定数 ---
SEARCH_PAGE_URL = "https://www.ticket.kintetsu.co.jp/M/MET/MET60.do?op=pDisplayExpVacGid&ps001=g"
SEARCH_ACTION_URL = "https://www.ticket.kintetsu.co.jp/M/MET/MET60.do"

# 青の交響曲の全便をカバーする検索パターン
# 1度の検索で4件表示されるので、各方面1回ずつで全便取得可能
SEARCH_PATTERNS = [
    {"dep": "大阪阿部野橋", "arr": "吉野",       "hour": "09", "minute": "00", "direction": "阿部野橋→吉野"},
    {"dep": "吉野",       "arr": "大阪阿部野橋", "hour": "12", "minute": "00", "direction": "吉野→阿部野橋"},
]

MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
}

# アイコン判定マッピング
ICON_MAP = {
    "icon-circle-l.png": ("○", True),   # 空席有り
    "icon-try-l.png":    ("△", True),   # 空席残り僅か
    "icon-x-l.png":      ("×", False),  # 空席なし
}


def get_session_and_hidden_fields():
    """検索ページにアクセスし、セッションとhiddenフィールドを取得"""
    session = requests.Session()
    session.headers.update(MOBILE_HEADERS)

    resp = session.get(SEARCH_PAGE_URL, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form", {"id": "MET60Model"})
    if not form:
        raise Exception("検索フォームが見つかりません")

    hidden_fields = {}
    for inp in form.find_all("input", {"type": "hidden"}):
        name = inp.get("name", "")
        value = inp.get("value", "")
        if name:
            hidden_fields[name] = value

    return session, hidden_fields


def search_trains(session, hidden_fields, date, pattern):
    """検索を実行し、結果ページのHTMLを返す"""
    form_data = dict(hidden_fields)
    form_data["op"] = "pExecuteExpVacGid"
    form_data["ci200"] = date
    form_data["ci203"] = pattern["hour"]
    form_data["ci204"] = pattern["minute"]
    form_data["ci206"] = pattern["dep"]
    form_data["ci209"] = pattern["arr"]

    resp = session.post(SEARCH_ACTION_URL, data=form_data, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_symphony_trains(html):
    """
    検索結果HTMLから青の交響曲の情報を抽出する。

    Returns: list of dict
        [
            {
                "dep_time": "10：10",
                "arr_time": "11：28",
                "train_number": "9009列車",
                "grades": [
                    {"name": "デラックス", "icon": "icon-x-l.png", "symbol": "×", "available": False},
                    {"name": "サロン",     "icon": "icon-try-l.png", "symbol": "△", "available": True},
                    ...
                ]
            },
            ...
        ]
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # 各列車は accordion パターンで表示される
    # ヘッダー: sp-selecttrain-head (id=sp-accordion1, sp-accordion2, ...)
    # 詳細:    sp-selecttrain-infoarea2 (id=sp-accordion-infoarea1, ...)
    heads = soup.find_all("div", class_="sp-selecttrain-head")

    for head in heads:
        # Blue Symphony かチェック
        name_el = head.find("span", class_="sp-selecttain-trainiconarea-name")
        imgs = head.find_all("img")
        is_symphony = False

        if name_el and ("青の交響曲" in name_el.get_text() or "ｼﾝﾌｫﾆｰ" in name_el.get_text()):
            is_symphony = True
        elif any("blue-symphony" in (img.get("src", "")) for img in imgs):
            is_symphony = True

        if not is_symphony:
            continue

        # 発着時刻を取得
        time_area = head.find("span", class_="sp-time-area")
        if not time_area:
            continue

        time_text = time_area.get_text(strip=True)
        # "10：10発→11：28着" のようなテキストから抽出
        dep_time = ""
        arr_time = ""
        date_texts = time_area.find_all("span", class_="sp-selecttrain-info-datetext")
        time_parts = time_area.get_text().split("→")
        if len(time_parts) >= 1:
            dep_time = time_parts[0].replace("発", "").strip()
        if len(time_parts) >= 2:
            arr_time = time_parts[1].replace("着", "").strip()

        # 対応する詳細エリアを探す
        head_id = head.get("id", "")  # sp-accordion2
        accordion_num = head_id.replace("sp-accordion", "")
        info_area = soup.find("div", id=f"sp-accordion-infoarea{accordion_num}")
        if not info_area:
            continue

        # 列車情報を取得
        train_info_block = info_area.find("div", class_="sp-selecttrain-info")
        if not train_info_block:
            continue

        train_num_el = train_info_block.find("div", class_="sp-selecttrain-train-number")
        train_number = train_num_el.get_text(strip=True) if train_num_el else ""

        # グレード別空席状況
        grades = []
        grade_rows = train_info_block.find_all("div", class_="sp-selecttrain-condition2")
        for row in grade_rows:
            seat_type_el = row.find("span", class_="sp-seat-type")
            if not seat_type_el:
                continue
            grade_name = seat_type_el.get_text(strip=True)

            # アイコン画像を取得
            first_span = row.find("span")
            icon_img = first_span.find("img") if first_span else None
            icon_src = icon_img.get("src", "") if icon_img else ""
            icon_filename = icon_src.split("/")[-1] if icon_src else ""

            # アイコン判定
            symbol, available = ICON_MAP.get(icon_filename, ("?", False))

            grades.append({
                "name": grade_name,
                "icon": icon_filename,
                "symbol": symbol,
                "available": available,
            })

        results.append({
            "dep_time": dep_time,
            "arr_time": arr_time,
            "train_number": train_number,
            "grades": grades,
        })

    return results


def send_discord_notification(webhook_url, date_label, train, available_grades):
    """Discord Webhook通知"""
    grade_lines = []
    for g in train["grades"]:
        emoji = "✅" if g["available"] else "❌"
        grade_lines.append(f"{emoji} {g['symbol']} {g['name']}")

    grade_text = "\n".join(grade_lines)
    available_text = "・".join([g["name"] for g in available_grades])

    embed = {
        "title": "🚄 青の交響曲に空席があります！",
        "color": 0x1a237e,
        "fields": [
            {"name": "📅 日付", "value": date_label, "inline": True},
            {"name": "🕐 時刻", "value": f"{train['dep_time']}発 → {train['arr_time']}着", "inline": True},
            {"name": "🚂 列車", "value": train["train_number"], "inline": True},
            {"name": "💺 空きグレード", "value": available_text, "inline": False},
            {"name": "📊 全グレード状況", "value": grade_text, "inline": False},
            {"name": "🔗 予約", "value": "[近鉄チケットサイト](https://www.ticket.kintetsu.co.jp/)", "inline": False},
        ],
        "footer": {"text": "青の交響曲 空席監視 (GitHub Actions)"},
        "timestamp": datetime.utcnow().isoformat()
    }
    payload = {
        "content": f"🎵 **{train['dep_time']}発の青の交響曲に空席！** ({available_text})",
        "embeds": [embed]
    }
    resp = requests.post(webhook_url, json=payload, timeout=10)
    return resp.status_code in (200, 204)


def main():
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print("❌ DISCORD_WEBHOOK_URL が未設定です")
        sys.exit(1)

    # 日付
    date = os.getenv("MONITOR_DATE", "").strip()
    if not date:
        tomorrow = datetime.now() + timedelta(days=1)
        date = tomorrow.strftime("%m%d")

    year = datetime.now().year
    date_label = f"{year}/{date[:2]}/{date[2:]}"

    # 監視グレード
    grades_filter = os.getenv("MONITOR_GRADES", "デラックス,サロン,ツイン").split(",")
    grades_filter = [g.strip() for g in grades_filter if g.strip()]

    print(f"🔍 青の交響曲 空席チェック")
    print(f"   日付: {date_label}")
    print(f"   監視グレード: {', '.join(grades_filter)}")
    print()

    # セッション取得 (1回で共有)
    try:
        session, hidden_fields = get_session_and_hidden_fields()
        print("✅ セッション取得完了")
    except Exception as e:
        print(f"❌ セッション取得失敗: {e}")
        sys.exit(1)

    all_trains = []

    # 各方面を検索
    for pattern in SEARCH_PATTERNS:
        print(f"\n🔎 検索中: {pattern['direction']} ({pattern['hour']}:{pattern['minute']}以降)")
        try:
            html = search_trains(session, hidden_fields, date, pattern)
            trains = parse_symphony_trains(html)

            if trains:
                print(f"   → 青の交響曲 {len(trains)}件 発見")
                all_trains.extend(trains)
            else:
                print(f"   → 青の交響曲なし")

            # 2回目の検索のためセッション更新 (hiddenフィールド再取得)
            session2, hidden_fields = get_session_and_hidden_fields()
            session = session2

        except Exception as e:
            print(f"   ❌ 検索エラー: {e}")

    print(f"\n{'='*50}")
    print(f"📊 検出結果: 青の交響曲 {len(all_trains)}便")
    print(f"{'='*50}\n")

    any_vacancy = False

    for train in all_trains:
        print(f"🚂 {train['dep_time']}発 → {train['arr_time']}着 ({train['train_number']})")

        available_grades_in_filter = []
        for g in train["grades"]:
            status = f"  {g['symbol']} {g['name']}"
            if g["available"]:
                status += " ← 空席あり！"
            print(status)

            # フィルタに含まれるグレードで空席ありなら通知対象
            if g["available"] and any(fg in g["name"] for fg in grades_filter):
                available_grades_in_filter.append(g)

        if available_grades_in_filter:
            any_vacancy = True
            print(f"\n  🎉 Discord通知を送信中...")
            success = send_discord_notification(webhook_url, date_label, train, available_grades_in_filter)
            print(f"  {'✅ 送信成功' if success else '❌ 送信失敗'}")

        print()

    if not any_vacancy:
        print("結果: 対象グレードに空席なし")
    else:
        print("結果: 空席を検知し、Discord通知を送信しました！")


if __name__ == "__main__":
    main()
