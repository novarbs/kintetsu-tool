"""
青の交響曲 空席監視モジュール (軽量版)
- requests + BeautifulSoup で近鉄チケットサイトの空席を確認
- Discord Webhookで通知
- APSchedulerで5分間隔の定期実行
- Playwrightは不要 → 軽量PaaS(Render等)で24h稼働可能
"""

import os
import time
import json
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler

# --- 定数 ---
SEARCH_PAGE_URL = "https://www.ticket.kintetsu.co.jp/M/MET/MET60.do?op=pDisplayExpVacGid&ps001=g"
SEARCH_ACTION_URL = "https://www.ticket.kintetsu.co.jp/M/MET/MET60.do"

BS_SCHEDULES = [
    {"label": "第1便 (10:10発 阿部野橋→吉野)", "dep": "大阪阿部野橋", "arr": "吉野", "hour": "10", "minute": "00"},
    {"label": "第2便 (12:34発 吉野→阿部野橋)", "dep": "吉野", "arr": "大阪阿部野橋", "hour": "12", "minute": "30"},
    {"label": "第3便 (14:10発 阿部野橋→吉野)", "dep": "大阪阿部野橋", "arr": "吉野", "hour": "14", "minute": "00"},
    {"label": "第4便 (16:34発 吉野→阿部野橋)", "dep": "吉野", "arr": "大阪阿部野橋", "hour": "16", "minute": "30"},
]

BS_GRADES = ["デラックス", "サロン", "ツイン"]

MOBILE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}


class VacancyMonitor:
    """空席監視マネージャー"""

    def __init__(self):
        self.scheduler = BackgroundScheduler()
        self.is_running = False
        self.config = None          # 監視条件
        self.check_history = []     # チェック履歴 (最新50件)
        self.last_notified = None   # 最後に通知した時刻 (同一空席の連続通知防止)

    def start(self, config):
        """監視開始"""
        self.config = config
        self.is_running = True
        self.check_history = []
        self.last_notified = None

        # 即座に1回チェック
        self._run_check()

        # 定期実行をスケジュール
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self.scheduler = BackgroundScheduler()

        interval = int(config.get("interval", 5))
        self.scheduler.add_job(self._run_check, 'interval', minutes=interval, id='vacancy_check')
        self.scheduler.start()

        print(f"[Monitor] 監視開始: {config}")

    def stop(self):
        """監視停止"""
        self.is_running = False
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            self.scheduler = BackgroundScheduler()
        print("[Monitor] 監視停止")

    def get_status(self):
        """現在の状態を返す"""
        return {
            "is_running": self.is_running,
            "config": self.config,
            "history": self.check_history[-20:],  # 最新20件
            "last_notified": self.last_notified
        }

    def _run_check(self):
        """空席チェック実行"""
        if not self.config:
            return

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[Monitor] チェック実行: {timestamp}")

        try:
            result = check_vacancy(self.config)
            entry = {
                "time": timestamp,
                "status": "success",
                "result": result
            }

            # 空席あり？
            available_grades = [g for g, avail in result.get("grades", {}).items() if avail]

            if available_grades:
                entry["vacancy_found"] = True
                entry["available_grades"] = available_grades

                # 連続通知防止 (同じ結果の場合は30分に1回まで)
                should_notify = True
                if self.last_notified:
                    elapsed = (datetime.now() - datetime.strptime(self.last_notified, "%Y-%m-%d %H:%M:%S")).total_seconds()
                    if elapsed < 1800:  # 30分
                        should_notify = False
                        entry["notification"] = "スキップ (30分以内に通知済み)"

                if should_notify:
                    self._send_notification(available_grades, result)
                    self.last_notified = timestamp
                    entry["notification"] = "送信済み"
            else:
                entry["vacancy_found"] = False

            self.check_history.append(entry)

        except Exception as e:
            entry = {
                "time": timestamp,
                "status": "error",
                "error": str(e)
            }
            self.check_history.append(entry)
            print(f"[Monitor] エラー: {e}")

        # 履歴は最新50件まで
        if len(self.check_history) > 50:
            self.check_history = self.check_history[-50:]

    def _send_notification(self, available_grades, result):
        """Discord Webhook通知"""
        webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
        if not webhook_url:
            print("[Monitor] DISCORD_WEBHOOK_URL が未設定のため通知スキップ")
            return

        config = self.config
        schedule_idx = int(config.get("schedule", 0))
        schedule = BS_SCHEDULES[schedule_idx]

        grade_text = "・".join(available_grades)
        train_info = result.get("train_info", "青の交響曲")

        embed = {
            "title": "🚄 空席を検知しました！",
            "color": 0x1a237e,  # navy blue
            "fields": [
                {"name": "📅 日付", "value": config.get("date_label", config.get("date", "")), "inline": True},
                {"name": "🚂 便", "value": schedule["label"], "inline": False},
                {"name": "✅ 空きグレード", "value": grade_text, "inline": False},
                {"name": "🔗 予約", "value": "[近鉄チケットサイト](https://www.ticket.kintetsu.co.jp/)", "inline": False},
            ],
            "footer": {"text": "青の交響曲 空席監視"},
            "timestamp": datetime.utcnow().isoformat()
        }

        payload = {
            "content": "🎵 **青の交響曲に空席が見つかりました！**",
            "embeds": [embed]
        }

        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            if resp.status_code in (200, 204):
                print(f"[Monitor] Discord通知送信成功")
            else:
                print(f"[Monitor] Discord通知エラー: {resp.status_code} {resp.text}")
        except Exception as e:
            print(f"[Monitor] Discord送信例外: {e}")


def check_vacancy(config):
    """
    近鉄サイトで空席を確認する (requests + BeautifulSoup版)

    フロー:
      1. 検索ページ(GET)でセッションとhiddenフィールドを取得
      2. 検索条件をPOSTして結果ページを取得
      3. 結果ページのHTMLをパースして青の交響曲の空席アイコンを判定

    判定:
      - icon-x   → ×（満席）
      - icon-o   → ○（空席あり）
      - icon-triangle → △（残りわずか）

    Returns:
        {
            "train_found": bool,
            "train_info": str,
            "grades": {"デラックス": True/False, "サロン": True/False, "ツイン": True/False},
            "raw_icons": {"デラックス": "icon-xxx.png", ...},
            "error": str or None
        }
    """
    schedule_idx = int(config.get("schedule", 0))
    schedule = BS_SCHEDULES[schedule_idx]
    date = config.get("date", "")
    target_grades = config.get("grades", BS_GRADES)

    result = {
        "train_found": False,
        "train_info": "",
        "grades": {g: False for g in target_grades},
        "raw_icons": {},
        "error": None
    }

    try:
        session = requests.Session()
        session.headers.update(MOBILE_HEADERS)

        # 1. 検索ページをGETしてセッションCookie + hiddenフィールドを取得
        print("[Monitor] 検索ページを取得中...")
        resp = session.get(SEARCH_PAGE_URL, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")
        form = soup.find("form", {"id": "MET60Model"})

        if not form:
            result["error"] = "検索フォームが見つかりません"
            return result

        # hidden フィールドを収集
        form_data = {}
        for inp in form.find_all("input", {"type": "hidden"}):
            name = inp.get("name", "")
            value = inp.get("value", "")
            if name:
                form_data[name] = value

        # 2. 検索条件を設定
        form_data["op"] = "pExecuteExpVacGid"
        form_data["ci200"] = date         # 日付
        form_data["ci203"] = schedule["hour"]    # 時
        form_data["ci204"] = schedule["minute"]  # 分
        form_data["ci206"] = schedule["dep"]     # 乗車駅
        form_data["ci209"] = schedule["arr"]     # 降車駅

        print(f"[Monitor] 検索実行: 日付={date} 時刻={schedule['hour']}:{schedule['minute']} {schedule['dep']}→{schedule['arr']}")

        # 3. 検索実行 (POST)
        resp = session.post(SEARCH_ACTION_URL, data=form_data, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")

        # 4. 検索結果ページのタイトル確認
        h1 = soup.find("h1")
        page_title = h1.get_text(strip=True) if h1 else ""
        print(f"[Monitor] ページタイトル: {page_title}")

        if "空席案内" not in page_title and "列車" not in page_title:
            # エラーページかもしれない
            error_msg = soup.find("li", class_="sp-sub-disc-message")
            if error_msg:
                result["error"] = f"検索エラー: {error_msg.get_text(strip=True)[:100]}"
            else:
                result["error"] = f"予期しないページ: {page_title}"
            return result

        # 5. 列車ブロックから青の交響曲を探す
        train_blocks = soup.find_all("div", class_="sp-selecttrain-info")

        if not train_blocks:
            result["error"] = f"列車が見つかりません (ページ: {page_title})"
            return result

        print(f"[Monitor] {len(train_blocks)} 件の列車ブロックを検出")

        # 青の交響曲を探す
        symphony_block = None
        for block in train_blocks:
            block_text = block.get_text()
            # 画像のsrcでも判定
            imgs = block.find_all("img")
            has_symphony_img = any("blue-symphony" in (img.get("src", "")) for img in imgs)

            if "青の交響曲" in block_text or "ｼﾝﾌｫﾆｰ" in block_text or has_symphony_img:
                symphony_block = block
                result["train_found"] = True
                # 列車番号を取得
                train_num_el = block.find("div", class_="sp-selecttrain-train-number")
                train_number = train_num_el.get_text(strip=True) if train_num_el else ""
                result["train_info"] = f"青の交響曲 {train_number}"
                break

        if not symphony_block:
            # 見つかった列車名をデバッグ出力
            for block in train_blocks[:3]:
                info_area = block.find("div", class_="sp-selecttrain-traininfoarea")
                if info_area:
                    print(f"[Monitor] 見つかった列車: {info_area.get_text(strip=True)[:60]}")
            result["error"] = "青の交響曲が検索結果に見つかりません"
            return result

        # 6. 各グレードの空席状況をアイコン画像で判定
        grade_rows = symphony_block.find_all("div", class_="sp-selecttrain-condition2")
        print(f"[Monitor] {len(grade_rows)} 件のグレード行を検出")

        for row in grade_rows:
            # グレード名を取得
            seat_type_el = row.find("span", class_="sp-seat-type")
            if not seat_type_el:
                continue
            grade_name = seat_type_el.get_text(strip=True)

            # アイコン画像のsrcを取得
            # 最初のimg (空席状況アイコン)
            icon_span = row.find("span")
            icon_img = icon_span.find("img") if icon_span else None
            icon_src = icon_img.get("src", "") if icon_img else ""

            # アイコンファイル名を抽出
            icon_filename = icon_src.split("/")[-1] if icon_src else ""
            result["raw_icons"][grade_name] = icon_filename

            print(f"[Monitor] グレード: {grade_name} → アイコン: {icon_filename}")

            # 対象グレードかチェック
            matched_grade = None
            for tg in target_grades:
                if tg in grade_name:
                    matched_grade = tg
                    break

            if matched_grade:
                # icon-x が含まれていなければ空席あり
                is_available = icon_filename and "icon-x" not in icon_filename
                result["grades"][matched_grade] = is_available

    except requests.exceptions.RequestException as e:
        result["error"] = f"通信エラー: {str(e)}"
        print(f"[Monitor] 通信エラー: {e}")
    except Exception as e:
        result["error"] = f"チェック処理エラー: {str(e)}"
        import traceback
        traceback.print_exc()

    return result


# シングルトンインスタンス
monitor = VacancyMonitor()
