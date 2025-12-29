from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import threading
import re
import time
import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# --- 定数・設定データ ---
STATION_LIST = [
    "大阪難波", "大阪上本町", "鶴橋", "生駒", "学園前", "大和西大寺", "近鉄奈良", "京都", "近鉄丹波橋", 
    "大和八木", "名張", "伊賀神戸", "榊原温泉口", "伊勢中川",
    "近鉄名古屋", "桑名", "近鉄四日市", "白子", "津",
    "松阪", "伊勢市", "宇治山田", "五十鈴川", "鳥羽", "志摩磯部", "鵜方", "賢島",
    "大阪阿部野橋", "尺土", "高田市", "橿原神宮前", "飛鳥", "壺阪山", "吉野口", "福神", "下市口", "六田", "大和上市", "吉野神宮", "吉野"
]

NO_MAP_GRADES = ["和風個室", "洋風個室"]
GROUP_SEAT_GRADES = ["サロン", "ツイン"]

TRAIN_DEFINITIONS = {
    "指定なし": {
        "grades": ["指定なし", "レギュラー", "デラックス", "プレミアム", "サロン", "階上", "階下", "ツイン", "和風個室", "洋風個室"],
        "cars": 10, "cols": 4, "skip_cars": []
    },
    "しまかぜ": {
        "grades": ["プレミアム", "サロン", "和風個室", "洋風個室"],
        "cars": 6, "cols": 3, 
        "skip_cars": [3], 
        "special_car": 4 
    },
    "ひのとり": {
        "grades": ["プレミアム", "レギュラー"],
        "cars": 6, 
        "cols": 4, 
        "skip_cars": []
    },
    "伊勢志摩ライナー": {
        "grades": ["レギュラー", "デラックス", "サロン", "ツイン"],
        "cars": 6, "cols": 4, "skip_cars": []
    },
    "アーバンライナー": {
        "grades": ["レギュラー", "デラックス"],
        "cars": 8, "cols": 4, "skip_cars": []
    },
    "ビスタカー": {
        "grades": ["レギュラー", "階上", "階下"],
        "cars": 4, "cols": 4, "skip_cars": []
    },
    "さくらライナー": {
        "grades": ["レギュラー", "デラックス"],
        "cars": 4, "cols": 4, "skip_cars": []
    },
    "青の交響曲": {
        "grades": ["デラックス", "サロン", "ツイン"], 
        "cars": 3, "cols": 3, "skip_cars": [2]
    },
    "あをによし": {
        "grades": ["ツイン", "サロン"],
        "cars": 4, "cols": 2, "skip_cars": [2]
    }
}

TRAIN_NAMES = list(TRAIN_DEFINITIONS.keys())

# ひのとり4号車 特殊座席マップ (IDはご提供のコードに基づく)
HINOTORI_CAR4_MAP = {
    "31D": "314", "31B": "312",
    "35D": "354", "35C": "353", "35A": "351"
}

def convert_seat_to_id(seat_str, train_name="", car_no=""):
    """
    座席ID変換ロジック
    """
    # ひのとり4号車の特殊対応
    if train_name == "ひのとり" and str(car_no) == "4":
        if seat_str in HINOTORI_CAR4_MAP:
            return HINOTORI_CAR4_MAP[seat_str]

    if seat_str.isdigit(): return f"{int(seat_str):02d}1"
    
    match = re.match(r"(\d+)([A-D]?)", seat_str)
    if not match: return None
    row_num = int(match.group(1))
    col_char = match.group(2)
    
    col_map = {'A': '1', 'B': '2', 'C': '3', 'D': '4'}
    col_num = col_map.get(col_char, '1')
    return f"{row_num:02d}{col_num}"

def run_automation(cond):
    print(f"自動化開始: {cond}")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page(viewport={"width": 1280, "height": 900})

            # 1. 検索実行
            page.goto("https://www.ticket.kintetsu.co.jp/M/MZZ/MZZ20.do?op=pDisplayServiceMenu")
            page.get_by_text("会員登録せずに特急券を購入する").click()
            page.get_by_text("購入開始").click()

            try:
                page.locator("select[name='ci200']").select_option(value=cond["date"])
                page.locator("select[name='ci203']").select_option(value=cond["hour"])
                page.locator("select[name='ci204']").select_option(value=cond["minute"])
            except: pass

            page.locator("input[name='ci207']").fill(cond["dep"])
            page.locator("input[name='ci210']").fill(cond["arr"])
            page.get_by_role("link", name="検索", exact=True).click()
            page.wait_for_load_state("domcontentloaded")

            # 2. 列車選択
            train_headers = page.locator(".vs-selecttrain-head").all()
            selected_index = 0
            
            if not train_headers:
                print("列車なし")
                browser.close()
                return
            
            if cond["train"] != "指定なし":
                for i, header in enumerate(train_headers):
                    txt = header.inner_text().replace("\n", " ")
                    try: name = header.locator(".vs-selecttain-trainiconarea-name").inner_text()
                    except: name = ""
                    if cond["train"] in name or cond["train"] in txt:
                        selected_index = i
                        print(f"列車発見: {name}")
                        break
            
            page.evaluate(f"vsTrainSearchRstSubmit({selected_index})")
            
            # 3. 購入条件画面
            try:
                page.wait_for_selector("h1:has-text('特急券購入条件')", timeout=5000)
                
                adults = int(cond["adults"])
                children = int(cond["children"])
                total_people = adults + children

                # 高速入力
                page.evaluate(f"""() => {{
                    document.querySelector("select[name='ci213']").value = "{adults}";
                    document.querySelector("select[name='ci214']").value = "{children}";
                    if(typeof seatPositionSelect === 'function') seatPositionSelect();
                }}""")
                time.sleep(0.5)

                current_grade = cond["grade"]
                is_private = current_grade in NO_MAP_GRADES

                if current_grade != "指定なし":
                    try: page.locator("label").filter(has_text=current_grade).first.click()
                    except: pass

                if is_private:
                    pass 
                elif cond["enable_seat_assign"]:
                    try: page.locator("label").filter(has_text="シートマップから選択").locator("visible=true").first.click()
                    except:
                        try: page.locator("input[value='-2']").locator("visible=true").first.click()
                        except: pass
                else:
                    if total_people >= 2:
                        try: page.locator("label").filter(has_text="どの席でも良い").locator("visible=true").first.click()
                        except: pass
                    else:
                        try: page.locator("label").filter(has_text="選択しない").locator("visible=true").first.click()
                        except: pass

                page.get_by_text("次へ").click()

                # 4. シートマップ操作 (個室以外)
                if not is_private and cond["enable_seat_assign"]:
                    page.wait_for_selector("#vs-seatmap-page", timeout=8000)
                    
                    target_car = cond["car_no"]
                    if cond["train"] == "しまかぜ" and current_grade == "サロン":
                        target_car = "4"

                    if target_car != "指定なし":
                        target_id = f"vs-train{target_car}"
                        try:
                            car_el = page.locator(f"#{target_id} a").first
                            if car_el.is_visible():
                                car_el.click()
                                page.wait_for_timeout(1000)
                        except: pass
                    
                    # 複数座席の処理
                    if cond["seat_no"]:
                        seat_list = cond["seat_no"].split(",") # カンマ区切りでリスト化
                        
                        # JSで一括処理用スクリプトを構築
                        js_code = "() => {"
                        
                        for seat_str in seat_list:
                            if not seat_str: continue
                            seat_id = convert_seat_to_id(seat_str, cond["train"], target_car)
                            if seat_id:
                                js_code += f"""
                                    var s = document.getElementById('{seat_id}');
                                    if(s && !s.classList.contains('vs-seat-full')) s.click();
                                """
                        
                        # 共通処理（同意チェック & 次へ）
                        js_code += """
                            var chk = document.getElementById('car-info-checkbox');
                            if(chk && chk.offsetParent !== null) chk.checked = true;
                            
                            var btn = document.getElementById('vs-seatconfirm-btn');
                            if(btn) btn.click();
                        }
                        """
                        
                        # 実行
                        print(f"座席選択実行: {seat_list}")
                        page.evaluate(js_code)

                # 5. 割引選択
                try:
                    page.wait_for_selector("h1:has-text('割引選択')", timeout=5000)
                    page.evaluate("vsDiscountSubmit('0')")
                except: pass

                # 6. お客様情報入力
                try:
                    page.wait_for_selector("h1:has-text('お客様情報入力')", timeout=5000)
                    
                    lname = os.getenv("USER_LAST_NAME_KANA", "")
                    fname = os.getenv("USER_FIRST_NAME_KANA", "")
                    email = os.getenv("USER_EMAIL", "")
                    card_no = os.getenv("CREDIT_CARD_NO", "")
                    card_exp_m = os.getenv("CREDIT_EXP_MONTH", "")
                    card_exp_y = os.getenv("CREDIT_EXP_YEAR", "")
                    card_holder = os.getenv("CREDIT_HOLDER", "")
                    sec_code = os.getenv("CREDIT_SECURITY_CODE", "")

                    page.evaluate(f"""() => {{
                        document.getElementById('ci103').value = '{lname}';
                        document.getElementById('ci104').value = '{fname}';
                        document.getElementById('ci105').value = '{email}';
                        document.getElementById('ci106').value = '{email}';
                        
                        const creditRadio = document.getElementById('ci232_1');
                        if(creditRadio) {{
                            creditRadio.click();
                            reDisplayCard('ci232','payPayInfo,creditInfo');
                        }}
                    }}""")
                    
                    page.wait_for_timeout(200)
                    
                    page.evaluate(f"""() => {{
                        if(document.getElementById('ci127')) document.getElementById('ci127').value = '{card_no}';
                        if(document.getElementById('ci129')) document.getElementById('ci129').value = '{card_exp_m}';
                        if(document.getElementById('ci130')) document.getElementById('ci130').value = '{card_exp_y}';
                        if(document.getElementById('ci503')) document.getElementById('ci503').value = '{sec_code}';
                        if(document.getElementById('ci142')) document.getElementById('ci142').value = '{card_holder}';
                        
                        const agree = document.getElementById('agree-check');
                        if(agree) agree.click();
                    }}""")

                    print("入力完了。手動で確認して購入ボタンを押してください。")

                except Exception as e:
                    print(f"入力エラー: {e}")

            except Exception as e:
                print(f"フローエラー: {e}")

            while True:
                try:
                    if page.is_closed(): break
                    time.sleep(1)
                except: break
            
            browser.close()

    except Exception as e:
        print(f"プロセスエラー: {e}")

@app.route('/')
def index():
    date_options = []
    today = datetime.now()
    for i in range(35):
        d = today + timedelta(days=i)
        val = d.strftime("%m%d")
        label = d.strftime("%m/%d (%a)")
        date_options.append({"value": val, "label": label})
    
    return render_template('index.html', 
                           stations=STATION_LIST, 
                           dates=date_options,
                           train_defs=TRAIN_DEFINITIONS,
                           train_names=TRAIN_NAMES,
                           no_map_grades=NO_MAP_GRADES)

@app.route('/run', methods=['POST'])
def run():
    data = request.json
    thread = threading.Thread(target=run_automation, args=(data,))
    thread.start()
    return jsonify({"status": "started", "message": "ブラウザを起動しました。自動操作の後、手動で購入確定してください。"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)