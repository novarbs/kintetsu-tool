# ベースイメージ（Python 3.12）
FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm

# パッケージリストの更新
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends \
    # 必要に応じて追加のOSパッケージをここに記述
    && apt-get clean -y && rm -rf /var/lib/apt/lists/*