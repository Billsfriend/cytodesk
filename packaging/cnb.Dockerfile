FROM python:3.12-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        binutils \
        ca-certificates \
        curl \
        git \
        libdbus-1-3 \
        libegl1 \
        libfontconfig1 \
        libgl1 \
        libglib2.0-0 \
        libice6 \
        libopengl0 \
        libsm6 \
        libx11-6 \
        libx11-xcb1 \
        libxcb-cursor0 \
        libxcb-icccm4 \
        libxcb-image0 \
        libxcb-keysyms1 \
        libxcb-randr0 \
        libxcb-render-util0 \
        libxcb-shape0 \
        libxcb-sync1 \
        libxcb-util1 \
        libxcb-xfixes0 \
        libxcb-xinerama0 \
        libxkbcommon-x11-0 \
        libxrender1 \
        patchelf \
        tar \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir "uv==0.11.18"

WORKDIR /workspace
