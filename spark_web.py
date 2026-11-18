# -*- coding: utf-8 -*-
"""命令行兼容层：保留原来的 --mode 参数（本机既有的计划任务 run_*.cmd 在用）。

真正的逻辑都在 core.py；图形界面在 app.py。
"""
from __future__ import annotations

import argparse
import traceback

import core
import paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["morning", "evening"], default="morning")
    ap.add_argument("--only", default="", help="只发给某一个人，用于测试")
    ap.add_argument("--message", default="")
    ap.add_argument("--force", action="store_true", help="忽略今天已发的记录")
    a = ap.parse_args()

    cfg = paths.load_config()
    targets = [a.only] if a.only else cfg["targets"]
    if not targets:
        core.log("没有配置发送对象")
        return 2
    try:
        return core.run(a.mode, targets, a.message, cfg, force=a.force)
    except Exception:
        core.log("整体异常：\n" + traceback.format_exc())
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
