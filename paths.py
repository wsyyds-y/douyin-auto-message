# -*- coding: utf-8 -*-
"""路径与配置：源码运行时用项目目录，打包成 exe 后用 %LOCALAPPDATA%\\DouyinAutoMessage。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "DouyinAutoMessage"
CN_NAME = "抖音自动发消息"

DEFAULT_MESSAGES = [
    "早安，今天也要顺顺利利呀",
    "愿今天事事顺心，笑口常开",
    "新的一天，愿你健康平安，万事如意",
    "祝你今天心情好，吃嘛嘛香",
    "愿你今天遇到的都是好事",
    "早上好，愿你今天元气满满",
    "愿你天天开心，事事顺心",
    "祝你平安喜乐，心想事成",
    "愿你今天被温柔以待",
    "新的一天，愿你一切顺利",
    "愿你今天顺顺利利，开开心心",
    "祝你好运连连，身体健康",
    "愿你今天有个好心情",
    "天天开心，平安顺遂",
    "愿你所求皆如愿，所行皆坦途",
    "早安，愿你今天过得舒服自在",
]


def is_frozen() -> bool:
    """是不是 PyInstaller 打出来的 exe。"""
    return bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    """配置、登录态、日志的存放目录（会自动创建）。"""
    if is_frozen():
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
    else:
        base = Path(__file__).resolve().parent
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return data_dir() / "config.json"


def default_config() -> dict:
    return {
        "targets": [],
        "messages": list(DEFAULT_MESSAGES),
        "times": {"morning": "07:00", "evening": "20:00"},
        "evening_enabled": True,
    }


def _normalize(cfg: dict) -> dict:
    """补全缺字段，防止旧配置缺键导致崩溃。"""
    base = default_config()
    base.update(cfg or {})
    times = base.get("times") or {}
    base["times"] = {
        "morning": str(times.get("morning") or "07:00"),
        "evening": str(times.get("evening") or "20:00"),
    }
    base["targets"] = [str(t) for t in (base.get("targets") or []) if str(t).strip()]
    base["messages"] = [str(m) for m in (base.get("messages") or []) if str(m).strip()] or list(DEFAULT_MESSAGES)
    base["evening_enabled"] = bool(base.get("evening_enabled", True))
    return base


def load_config() -> dict:
    """读配置；不存在就写一份默认的。"""
    path = config_path()
    if not path.exists():
        cfg = default_config()
        save_config(cfg)
        return cfg
    try:
        with open(path, encoding="utf-8") as f:
            return _normalize(json.load(f))
    except Exception:
        cfg = default_config()
        save_config(cfg)
        return cfg


def save_config(cfg: dict) -> None:
    """保存配置（先写临时文件再替换，避免写一半断电）。"""
    path = config_path()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_normalize(cfg), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

