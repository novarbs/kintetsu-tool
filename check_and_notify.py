"""
青の交響曲 空席チェック & Discord通知 (GitHub Actions用スタンドアロン版)

GitHub Actionsのcronで5分ごとに実行される。
空席があればDiscord Webhookで通知する。

環境変数:
  DISCORD_WEBHOOK_URL: Discord Webhook URL
  MONITOR_DATE: 監視日 (例: "0404") ※未設定の場合は翌日以降の最も近い運行日
  MONITOR_SCHEDULE: 便番号 0-3 (デフォルト: 全便チェック)
  MONITOR_GRADES: 監視グレード (カンマ区切り, デフォルト: "デラックス,サロン,ツイン")
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# --- 定数 ---
SEARCH_PAGE_URL = "https://www.ticket.kintetsu.co.jp/M/MET/MET60.do?op=pDisplayExpVacGid&ps001=g"
SEARCH_ACTION_URL = "https://www.ticket.kintetsu.co.jp/M/MET/MET60.do"

BS_SCHEDULES = [
    {"label": "第1便 (10:10発 阿部野橋→吉野)", "dep": "大阪阿部野橋", "arr": "吉野", "hour": "10", "minute": "00"},
    {"label": "第2便 (12:34発 吉野→阿部野橋)", "dep": "吉野", "arr": "大阪阿部野橋", "hour": "12", "minute": "30"},
    {"label": "第3便 (14:10発 阿部野橋→吉野)", "dep": "大阪阿部野橋", "arr": "吉野", "hour": "14", "minute": "00"},
    {"label": "第4便 (16:34発 吉野→阿部野橋)", "dep": "吉野", "arr": "大阪阿部野橋", "hour": "16", "minute": "30"},
]

MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
}


def check_vacancy(date, schedule, target_grades):
    """近鉄サイトで空席を確認する"""
    result = {
        "train_found": False,
        "train_info": "",
        "grades": {g: False for g in target_grades},
        "raw_icons": {},
        "error": None
    }

    session = requests.Session()
    session.headers.update(MOBILE_HEADERS)

    # 1. 検索ページGET → hiddenフィールド取得
    resp = session.get(SEARCH_PAGE_URL, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    form = soup.find("form", {"id": "MET60Model"})
    if not form:
        result["error"] = "検索フォームが見つかりません"
        return result

    form_data = {}
    for inp in form.find_all("input", {"type": "hidden"}):
        name = inp.get("name", "")
        value = inp.get("value", "")
        if name:
            form_data[name] = value

    # 2. 検索条件を設定
    form_data["op"] = "pExecuteExpVacGid"
    form_data["ci200"] = date
    form_data["ci203"] = schedule["hour"]
    form_data["ci204"] = schedule["minute"]
    form_data["ci206"] = schedule["dep"]
    form_data["ci209"] = schedule["arr"]

    # 3. 検索実行 (POST)
    resp = session.post(SEARCH_ACTION_URL, data=form_data, timeout=15)
    resp.raise_for_status()
    resp.encoding = "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")

    # 4. 列車ブロックから青の交響曲を探す
    train_blocks = soup.find_all("div", class_="sp-selecttrain-info")
    if not train_blocks:
        h1 = soup.find("h1")
        page_title = h1.get_text(strip=True) if h1 else "不明"
        result["error"] = f"列車が見つかりません (ページ: {page_title})"
        return result

    symphony_block = None
    for block in train_blocks:
        block_text = block.get_text()
        imgs = block.find_all("img")
        has_symphony_img = any("blue-symphony" in (img.get("src", "")) for img in imgs)
        if "青の交響曲" in block_text or "ｼﾝﾌｫﾆｰ" in block_text or has_symphony_img:
            symphony_block = block
            result["train_found"] = True
            train_num_el = block.find("div", class_="sp-selecttrain-train-number")
            result["train_info"] = f"青の交響曲 {train_num_el.get_text(strip=True)}" if train_num_el else "青の交響曲"
            break

    if not symphony_block:
        result["error"] = "青の交響曲が検索結果に見つかりません"
        return result

    # 5. 各グレードの空席状況をアイコン画像で判定
    grade_rows = symphony_block.find_all("div", class_="sp-selecttrain-condition2")
    for row in grade_rows:
        seat_type_el = row.find("span", class_="sp-seat-type")
        if not seat_type_el:
            continue
        grade_name = seat_type_el.get_text(strip=True)

        icon_span = row.find("span")
        icon_img = icon_span.find("img") if icon_span else None
        icon_src = icon_img.get("src", "") if icon_img else ""
        icon_filename = icon_src.split("/")[-1] if icon_src else ""
        result["raw_icons"][grade_name] = icon_filename

        for tg in target_grades:
            if tg in grade_name:
                result["grades"][tg] = icon_filename and "icon-x" not in icon_filename
                break

    return result


def send_discord_notification(webhook_url, date_label, schedule, available_grades, train_info):
    """Discord Webhook通知"""
    grade_text = "・".join(available_grades)
    embed = {
        "title": "🚄 空席を検知しました！",
        "color": 0x1a237e,
        "fields": [
            {"name": "📅 日付", "value": date_label, "inline": True},
            {"name": "🚂 便", "value": schedule["label"], "inline": False},
            {"name": "✅ 空きグレード", "value": grade_text, "inline": False},
            {"name": "🔗 予約", "value": "[近鉄チケットサイト](https://www.ticket.kintetsu.co.jp/)", "inline": False},
        ],
        "footer": {"text": "青の交響曲 空席監視 (GitHub Actions)"},
        "timestamp": datetime.utcnow().isoformat()
    }
    payload = {
        "content": "🎵 **青の交響曲に空席が見つかりました！**",
        "embeds": [embed]
    }
    resp = requests.post(webhook_url, json=payload, timeout=10)
    return resp.status_code in (200, 204)


def get_monitor_date():
    """監視対象の日付を決定"""
    env_date = os.getenv("MONITOR_DATE", "").strip()
    if env_date:
        return env_date

    # 未設定の場合: 今日〜30日後の全日付を返す(後でフィルタ)
    # デフォルトは明日
    tomorrow = datetime.now() + timedelta(days=1)
    return tomorrow.strftime("%m%d")


def main():
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print("❌ DISCORD_WEBHOOK_URL が未設定です")
        sys.exit(1)

    date = get_monitor_date()
    target_grades = os.getenv("MONITOR_GRADES", "デラックス,サロン,ツイン").split(",")
    schedule_env = os.getenv("MONITOR_SCHEDULE", "").strip()

    # 便の決定: 指定があればその便のみ、なければ全便チェック
    if schedule_env:
        schedules_to_check = [int(s) for s in schedule_env.split(",")]
    else:
        schedules_to_check = list(range(len(BS_SCHEDULES)))

    print(f"🔍 空席チェック開始")
    print(f"   日付: {date}")
    print(f"   グレード: {', '.join(target_grades)}")
    print(f"   便: {[BS_SCHEDULES[i]['label'] for i in schedules_to_check]}")
    print()

    any_vacancy = False

    for idx in schedules_to_check:
        schedule = BS_SCHEDULES[idx]
        print(f"--- {schedule['label']} ---")

        try:
            result = check_vacancy(date, schedule, target_grades)

            if result["error"]:
                print(f"  ⚠️ {result['error']}")
                continue

            if not result["train_found"]:
                print("  ℹ️ 青の交響曲が見つかりません")
                continue

            print(f"  🚂 {result['train_info']}")

            # グレード判定
            available = []
            for grade, is_available in result["grades"].items():
                icon = result["raw_icons"].get(grade, "?")
                status = "○ 空席あり" if is_available else "× 満席"
                print(f"  {grade}: {status} ({icon})")
                if is_available:
                    available.append(grade)

            if available:
                any_vacancy = True
                # 日付ラベル作成
                year = datetime.now().year
                date_label = f"{year}/{date[:2]}/{date[2:]}"
                print(f"\n  🎉 空席発見！ Discord通知を送信...")
                success = send_discord_notification(webhook_url, date_label, schedule, available, result["train_info"])
                print(f"  {'✅ 送信成功' if success else '❌ 送信失敗'}")
            else:
                print("  空席なし")

        except Exception as e:
            print(f"  ❌ エラー: {e}")

        print()

    if not any_vacancy:
        print("📊 結果: 空席なし")
    else:
        print("📊 結果: 空席を検知し、Discord通知を送信しました")


if __name__ == "__main__":
    main()
