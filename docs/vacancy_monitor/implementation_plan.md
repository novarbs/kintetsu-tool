# 青の交響曲 空席監視＆通知システム

## 概要

指定した日付・便・グレードで近鉄特急「青の交響曲」の空席を5分間隔で自動チェックし、空席を検知したら即座に通知するDocker常駐システム。

## 通知方法

**LINE Messaging API** を推奨。理由：
- ユーザーがLINE Botの経験あり（過去の会話から）
- スマホ通知が即座に届く
- Docker環境からも問題なく送信可能

> [!NOTE]
> LINE Messaging APIのチャネルアクセストークンとユーザーIDが必要。
> セットアップ済みでない場合は、[LINE Developers](https://developers.line.biz/) でチャネル作成が必要。

## アーキテクチャ

```
┌─────────────────────────────────────────────────┐
│  Docker Container                               │
│                                                 │
│  ┌──────────────┐    ┌───────────────────────┐  │
│  │  Flask App   │    │  Monitor (5分間隔)     │  │
│  │  :5000       │    │                       │  │
│  │              │◄──►│ Playwright (headless)  │  │
│  │  - 設定UI    │    │ → 近鉄サイト空席確認   │  │
│  │  - 状態表示  │    │ → LINE通知送信         │  │
│  └──────────────┘    └───────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## 監視フロー

1. 近鉄チケットサイトへアクセス
2. 「会員登録せずに特急券を購入する」→「購入開始」
3. 日付・時刻・発着駅を入力して検索
4. 列車一覧から「青の交響曲」を探す
5. 列車をクリック → 購入条件画面
6. **各グレード（デラックス/サロン/ツイン）のラジオボタンが有効か確認**
   - 無効（disabled）= 満席
   - 有効 = 空席あり
7. 空席ありなら通知送信

## 提案する変更

---

### モニターモジュール

#### [NEW] monitor.py
空席チェックのメインロジック。

- `check_vacancy(config)` — Playwright headlessで近鉄サイトをスクレイピングし、各グレードの空席状況を返す
- `MonitorScheduler` クラス — APSchedulerで5分間隔の定期実行を管理
- `send_line_notification(message)` — LINE Messaging APIで通知送信

---

### Flask アプリ修正

#### [MODIFY] [app.py](file:///Users/nova/Developer/Kintetu/app.py)

以下のエンドポイントを追加：
- `GET /monitor` — 監視ダッシュボード画面
- `POST /api/monitor/start` — 監視開始（条件を受け取る）
- `POST /api/monitor/stop` — 監視停止
- `GET /api/monitor/status` — 現在の監視状態・履歴をJSON返却

---

### 監視ダッシュボード

#### [NEW] templates/monitor.html

監視の設定・状態確認UI：
- 日付・便・グレード選択フォーム（青の交響曲専用）
- 「監視開始」「監視停止」ボタン
- リアルタイムステータス表示（10秒ごとに自動更新）
- チェック履歴ログ表示
- 空席検知時はUIでもハイライト表示

---

### Docker設定

#### [MODIFY] [Dockerfile](file:///Users/nova/Developer/Kintetu/Dockerfile)
- VNC関連パッケージを削除（headlessで動くため不要）
- シンプルなFlaskアプリのみ起動

#### [MODIFY] [docker-compose.yml](file:///Users/nova/Developer/Kintetu/docker-compose.yml)
- ポート6080/6081（noVNC）を削除
- Flask（5001:5000）のみ公開

#### [MODIFY] [requirements.txt](file:///Users/nova/Developer/Kintetu/requirements.txt)
- `apscheduler` 追加（定期実行）
- `requests` 追加（LINE API通信）

---

### 環境変数

#### [MODIFY] [.env](file:///Users/nova/Developer/Kintetu/.env)

以下を追加：
```
LINE_CHANNEL_ACCESS_TOKEN=（LINEチャネルアクセストークン）
LINE_USER_ID=（通知先のLINEユーザーID）
```

## 検証計画

### 自動テスト
- `monitor.py` の空席チェック関数を単独実行して、近鉄サイトから正しくグレード別空席状況を取得できるか確認

```bash
docker compose exec kintetsu python -c "from monitor import check_vacancy; print(check_vacancy({...}))"
```

### ブラウザテスト
- Flask UIの `/monitor` ページにアクセスし、以下を確認：
  1. 監視条件を設定して「開始」を押す → ステータスが「監視中」に変わる
  2. 10秒後にステータスが自動更新される
  3. 「停止」を押す → ステータスが「停止中」に変わる

### 手動検証
- 空席がある便を指定して監視開始 → LINE通知が届くことを確認
- ユーザーにLINE通知の受信確認をお願いする
