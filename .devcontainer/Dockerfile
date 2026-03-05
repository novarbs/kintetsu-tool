# ベースイメージ（Python 3.12）
FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm

# Yarnリポジトリの GPG 鍵が期限切れのため、不要なリポジトリ設定を削除
RUN rm -f /etc/apt/sources.list.d/yarn.list

# パッケージリストの更新
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
    # 必要に応じて追加のOSパッケージをここに記述
    && apt-get clean -y && rm -rf /var/lib/apt/lists/*