# -*- coding: utf-8 -*-
"""抖音网页版自动发消息的核心逻辑（无界面）。

被两个入口调用：
    spark_web.py  源码/命令行（本机计划任务在用）
    app.py        打包后的 exe（GUI + --run/--login）

浏览器用系统自带的 Edge（channel="msedge"），登录态存在数据目录的 profile 里，
所以目标机器不需要安装 Python、Playwright 浏览器或任何运行时。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import paths

HOME = "https://www.douyin.com/"

DATA = paths.data_dir()
PROFILE = str(DATA / "profile")
STATE = str(DATA / "state.json")
SHOTS = str(DATA / "shots")
LOGS = str(DATA / "logs")
LAST_RUN = str(DATA / "last_run.json")
TASK_PREFIX = "DouyinAutoMessage"
MUTE_STATE = str(DATA / "mute_before.json")

# 打包成无控制台的 exe 时 sys.stdout 是 None，print 会抛异常
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 找「文字完全匹配且可见」的最小元素，返回中心坐标；点不到时返回 None
FIND_TEXT_JS = """
([text]) => {
  const norm = (s) => (s || '').replace(/\\s+/g, '');
  const cands = [];
  for (const e of document.querySelectorAll('div,span,li,a,button')) {
    if (norm(e.textContent) !== text) continue;
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (r.bottom < 0 || r.top > innerHeight) continue;
    cands.push({ x: r.x + r.width / 2, y: r.y + r.height / 2, area: r.width * r.height });
  }
  cands.sort((a, b) => a.area - b.area);
  return cands[0] || null;
}
"""

LAUNCH_ARGS = [
    "--no-first-run",
    "--no-default-browser-check",
    # 锁屏/窗口被遮挡时 Chromium 会降频定时器与渲染，这三个开关关掉节流
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
]


def log(msg: str) -> None:
    """打印并追加到当天日志。"""
    os.makedirs(LOGS, exist_ok=True)
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    with open(os.path.join(LOGS, f"{date.today()}.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state() -> dict:
    """读发送记录；文件坏了就当空的。"""
    if not os.path.exists(STATE):
        return {}
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    """写回发送记录。"""
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def click_text(page, text: str, timeout: float = 10.0) -> bool:
    """按可见文字点一下（不依赖坐标常量）。

    抖音的层级里同一个文字常有好几个节点（含隐藏的），这里筛可见、取最小、点中心。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        point = page.evaluate(FIND_TEXT_JS, [text])
        if point:
            page.mouse.click(point["x"], point["y"])
            return True
        page.wait_for_timeout(300)
    return False


def open_message_drawer(page) -> bool:
    """打开右上角的消息抽屉，等到搜索框出现。"""
    for attempt in range(2):
        if not click_text(page, "消息"):
            if attempt == 0:
                click_text(page, "更多")          # 窄窗口时「消息」藏在「更多」里
                page.wait_for_timeout(800)
                continue
            return False
        try:
            page.wait_for_selector("input.semi-input", timeout=8000)
            return True
        except Exception:
            page.wait_for_timeout(1000)
    return False


def blocked(page) -> bool:
    """页面是不是被验证码/登录页拦住了。"""
    title = page.title() or ""
    body = page.locator("body").inner_text()[:200]
    return ("验证码" in title) or ("扫码登录" in body)


def pick_message(target: str, cfg: dict) -> str:
    """按「日期 + 好友」稳定地挑一句祝福语。

    同一天同一个人早上和晚上必须拿到同一句，否则晚间补发会换一条，看着像 bug。
    """
    msgs = cfg.get("messages") or paths.DEFAULT_MESSAGES
    idx = (date.today().toordinal() + sum(ord(c) for c in target)) % len(msgs)
    return msgs[idx]


def send_one(page, target: str, message: str) -> bool:
    """给一个好友发一条，返回是否确认发出。"""
    page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)
    if blocked(page):
        raise RuntimeError("页面被验证码/登录拦住")

    if not open_message_drawer(page):
        raise RuntimeError("消息抽屉没打开")

    search = page.locator("input.semi-input").first
    search.click()
    search.fill(target)
    page.wait_for_timeout(2000)

    # 把搜索命中的那一行记进日志，方便确认「点的是不是对的人」
    try:
        preview = page.evaluate("""() => {
          const hits = [...document.querySelectorAll('div,span,button')]
            .filter(e => (e.textContent || '').trim() === '发消息');
          if (!hits.length) return '';
          let n = hits[0];
          for (let i = 0; i < 4 && n.parentElement; i++) n = n.parentElement;
          return (n.innerText || '').replace(/\\s+/g, ' ').slice(0, 40);
        }""")
        log(f"搜索「{target}」命中：{preview or '(未读到)'}")
    except Exception:
        pass

    # 搜索结果里优先点「发消息」按钮；没有按钮时才点名字
    if not click_text(page, "发消息"):
        if not click_text(page, target):
            raise RuntimeError(f"搜索结果里没有「{target}」")

    page.wait_for_selector("div.editor-kit-container", timeout=20000)
    editor = page.locator("div.editor-kit-container").first
    editor.click()
    page.keyboard.insert_text(message)
    page.wait_for_timeout(500)
    page.locator("svg.messageMsgInputpublishBtn").first.click()

    # 成功判据：输入框清空 + 消息出现在页面里（只看 body 会假阳性，
    # 因为没发出去时输入框里还留着这句话）
    deadline = time.time() + 10
    while time.time() < deadline:
        page.wait_for_timeout(500)
        editor_text = page.locator("div.editor-kit-container").first.inner_text().replace("\u200b", "").strip()
        if message in page.locator("body").inner_text() and message not in editor_text:
            return True
    return False


def _endpoint_volume():
    """取系统主输出设备的音量接口（兼容 pycaw 新旧 API）。"""
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    dev = AudioUtilities.GetSpeakers()
    if hasattr(dev, "EndpointVolume"):
        return dev.EndpointVolume
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def mute_system():
    """把系统静音，返回原本是否静音；取不到音量设备时返回 None。"""
    try:
        vol = _endpoint_volume()
        was = bool(vol.GetMute())
        vol.SetMute(1, None)
        try:
            with open(MUTE_STATE, "w", encoding="utf-8") as f:
                json.dump({"was": was, "time": f"{datetime.now():%Y-%m-%d %H:%M}"}, f)
        except Exception:
            pass
        log(f"系统已静音（原本{'已静音' if was else '未静音'}）")
        return was
    except Exception as exc:  # noqa: BLE001
        log(f"静音失败，继续执行：{exc}")
        return None


def restore_mute(was) -> None:
    """按进入前记下的状态恢复静音。"""
    if was is None:
        return
    try:
        _endpoint_volume().SetMute(1 if was else 0, None)
        try:
            os.remove(MUTE_STATE)
        except Exception:
            pass
        log(f"静音状态已恢复为{'静音' if was else '不静音'}")
    except Exception as exc:  # noqa: BLE001
        log(f"恢复静音状态失败：{exc}")


def edge_available() -> bool:
    """系统里有没有 Edge（打包版只用系统 Edge，不额外带浏览器内核）。"""
    for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ):
        if os.path.exists(p):
            return True
    return False


def read_last_run() -> dict:
    """读最近一次运行摘要（没有就返回空字典）。"""
    try:
        with open(LAST_RUN, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_last_run(mode: str, failed: int, total: int) -> None:
    """记录最近一次运行结果，供界面/通知使用。"""
    try:
        with open(LAST_RUN, "w", encoding="utf-8") as f:
            json.dump(
                {"time": f"{datetime.now():%Y-%m-%d %H:%M}", "mode": mode, "failed": failed, "total": total},
                f, ensure_ascii=False,
            )
    except Exception:
        pass


def notify_user(title: str, text: str) -> None:
    """弹一个系统消息框（给完全不懂电脑的人看）。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, title, 0x00000030)  # MB_ICONWARNING
    except Exception:
        pass


def is_logged_in() -> bool:
    """判断 profile 里有没有抖音的登录 cookie（不是看文件在不在）。"""
    import sqlite3
    from pathlib import Path

    db = Path(PROFILE) / "Default" / "Network" / "Cookies"
    if not db.exists():
        return False
    try:
        con = sqlite3.connect(db.as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            n = con.execute(
                "select count(*) from cookies where host_key like '%douyin%'"
                " and name in ('sessionid','sessionid_ss','sid_tt','passport_csrf_token')"
            ).fetchone()[0]
        finally:
            con.close()
        return int(n) > 0
    except Exception:
        return False


def login(timeout_sec: int = 300, on_state=None) -> bool:
    """打开浏览器让用户扫码登录，登录成功后自动关闭，返回是否成功。"""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    ctx = None
    ok = False
    try:
        ctx = pw.chromium.launch_persistent_context(
            PROFILE, channel="msedge", headless=False,
            viewport={"width": 1280, "height": 860}, args=LAUNCH_ARGS,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            page.wait_for_timeout(2000)
            try:
                if page.get_by_text("登录", exact=True).count() == 0 and not blocked(page):
                    ok = True
                    break
            except Exception:
                pass
            if on_state and not on_state():
                break
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
        try:
            pw.stop()
        except Exception:
            pass
    return ok


def run(mode: str, targets: list[str], message: str, cfg: dict, force: bool = False) -> int:
    """主流程：逐个好友处理，单人失败不影响其他人。"""
    from playwright.sync_api import sync_playwright

    state = load_state()
    today = str(date.today())
    day_state = state.setdefault(today, {})
    failed = 0

    # 打开抖音前先静音，避免视频外放；结束时（含异常）关闭浏览器并恢复原状态
    was_muted = mute_system()
    pw = None
    ctx = None
    try:
        pw = sync_playwright().start()
        ctx = pw.chromium.launch_persistent_context(
            PROFILE, channel="msedge", headless=False,
            viewport={"width": 1280, "height": 860}, args=LAUNCH_ARGS,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for target in targets:
            if target in day_state and not force:
                log(f"{target}: 今天 {day_state[target]['time']} 已发，跳过（{mode}）")
                continue
            try:
                msg = message or pick_message(target, cfg)
                ok = send_one(page, target, msg)
                if ok:
                    day_state[target] = {"time": f"{datetime.now():%H:%M}", "ok": True}
                    log(f"{target}: 已发送「{msg}」")
                else:
                    failed += 1
                    os.makedirs(SHOTS, exist_ok=True)
                    page.screenshot(path=os.path.join(SHOTS, f"fail-{target}-{datetime.now():%H%M%S}.png"))
                    log(f"{target}: 点了发送但页面没回显，请人工确认")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log(f"{target}: 失败 - {exc}")
                os.makedirs(SHOTS, exist_ok=True)
                try:
                    page.screenshot(path=os.path.join(SHOTS, f"err-{target}-{datetime.now():%H%M%S}.png"))
                except Exception:
                    pass
            save_state(state)
    finally:
        if ctx is not None:
            try:
                ctx.close()
                log("已关闭浏览器窗口")
            except Exception as exc:  # noqa: BLE001
                log(f"关闭浏览器失败：{exc}")
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass
        restore_mute(was_muted)

    cutoff = (date.today() - timedelta(days=14)).isoformat()
    state = {k: v for k, v in state.items() if k >= cutoff}
    save_state(state)
    log(f"本轮结束：成功 {len(targets) - failed}/{len(targets)}，模式 {mode}")
    write_last_run(mode, failed, len(targets))
    return 0 if failed == 0 else 1


def _run_ps(command: str) -> str:
    """跑一段 PowerShell，返回输出（失败返回空串）。"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=60,
        )
        return (proc.stdout or "").strip()
    except Exception:
        return ""


def _delete_tasks() -> list[str]:
    """删除本程序注册的所有计划任务（按前缀匹配，改过名也能清掉）。"""
    out = _run_ps(
        "Get-ScheduledTask | Where-Object { $_.TaskName -like '%s*' } | "
        "ForEach-Object { Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false; $_.TaskName }" % TASK_PREFIX
    )
    names = [n.strip() for n in out.splitlines() if n.strip()]
    return names


def _kill_our_browsers() -> None:
    """关掉用本程序 profile 启动的 Edge，否则删 profile 会被占用。"""
    _run_ps(
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
        "Where-Object { $_.CommandLine -like '*%s*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" % paths.APP_NAME
    )


def _restore_mute_if_left() -> str:
    """上次异常退出若把系统留在静音，这里恢复。"""
    if not os.path.exists(MUTE_STATE):
        return ""
    try:
        with open(MUTE_STATE, encoding="utf-8") as f:
            was = bool(json.load(f).get("was", False))
        _endpoint_volume().SetMute(1 if was else 0, None)
        os.remove(MUTE_STATE)
        return f"已恢复静音状态（原本{'静音' if was else '不静音'}）"
    except Exception as exc:  # noqa: BLE001
        return f"恢复静音失败：{exc}"


def _schedule_folder_delete(folder: Path) -> str:
    """等程序退出后删掉程序文件夹：写一个一次性 .bat，用 ShellExecute 启动。

    实测：无控制台进程里直接 subprocess 起 powershell 会静默失败，只有这条路可靠。
    """
    import tempfile

    bat = Path(tempfile.gettempdir()) / "DouyinAutoMessage_uninstall.bat"
    body = (
        "@echo off\r\n"
        "timeout /t 3 /nobreak >nul\r\n"
        f'rmdir /s /q "{folder}"\r\n'
        'del "%~f0"\r\n'
    )
    try:
        try:
            bat.write_text(body, encoding="gbk")
        except UnicodeEncodeError:
            bat.write_text("chcp 65001 >nul\r\n" + body, encoding="utf-8")
        os.startfile(str(bat))
        return f"已安排删除程序文件夹：{folder}（几秒后自动完成）"
    except Exception as exc:  # noqa: BLE001
        return f"无法自动删除程序文件夹，请手动删除 {folder}（原因：{exc}）"


def uninstall(remove_program_folder: bool = False) -> tuple[bool, list[str]]:
    """撤销本程序对这台电脑做过的全部改动。

    只动三样：计划任务、自己的数据目录、（可选）程序文件夹。
    不碰注册表、系统设置、用户日常的 Edge 数据。
    开发模式（源码运行）下绝不删除数据目录——那里就是源码目录。
    """
    import shutil

    lines: list[str] = []
    ok = True

    msg = _restore_mute_if_left()
    if msg:
        lines.append(msg)

    _kill_our_browsers()
    lines.append("已关闭自动发送用的浏览器进程")

    names = _delete_tasks()
    lines.append("已删除计划任务：" + "、".join(names) if names else "本机没有注册过计划任务")

    folder = Path(paths.data_dir())
    if not paths.is_frozen():
        lines.append(f"开发模式：跳过删除数据目录（{folder}），避免误删源码")
    elif (folder / "core.py").exists():
        ok = False
        lines.append(f"数据目录里检测到源码，拒绝删除：{folder}")
    else:
        for _ in range(3):
            shutil.rmtree(folder, ignore_errors=True)
            if not folder.exists():
                break
            time.sleep(1.0)
        if folder.exists():
            ok = False
            lines.append(f"数据目录没删干净，请手动删除：{folder}")
        else:
            lines.append(f"已删除数据目录：{folder}")

    if remove_program_folder and paths.is_frozen():
        exe_dir = Path(sys.executable).resolve().parent
        lines.append(_schedule_folder_delete(exe_dir))
    elif remove_program_folder:
        lines.append(f"开发模式下不自动删除程序目录：{Path(__file__).resolve().parent}")

    lines.append("")
    lines.append("程序没有改注册表、没有改系统设置、没有动你日常用的 Edge 数据。")
    return ok, lines


def cli(argv: list[str]) -> int:
    """命令行入口：--run morning|evening、--preview、--login、--force、--only。"""
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--run", choices=["morning", "evening"], default="")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--message", default="")
    ap.add_argument("--force", action="store_true", help="忽略今天已发的记录，强制再发")
    ap.add_argument("--selftest", action="store_true", help="自检：只开一次浏览器读标题，用来验证打包是否可用")
    ap.add_argument("--notify", action="store_true", help="失败时弹系统提示框（计划任务用）")
    ap.add_argument("--uninstall", action="store_true", help="卸载：删除计划任务和数据目录")
    ap.add_argument("--purge", action="store_true", help="卸载时连程序文件夹一起删")
    a = ap.parse_args(argv)

    if a.uninstall:
        # 注意：这里不能写日志——数据目录刚被删掉，log() 会把它重新创建出来
        ok, lines = uninstall(remove_program_folder=a.purge)
        for line in lines:
            try:
                print(line, flush=True)
            except Exception:
                pass
        return 0 if ok else 1

    cfg = paths.load_config()
    targets = [a.only] if a.only else cfg["targets"]

    if a.preview:
        for t in targets:
            print(f"{t}: {pick_message(t, cfg)}")
        return 0
    if a.selftest:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                PROFILE, channel="msedge", headless=False,
                viewport={"width": 1280, "height": 860}, args=LAUNCH_ARGS,
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
            log("selftest OK, title=" + page.title())
            ctx.close()
        return 0
    if a.login:
        ok = login()
        log("登录成功" if ok else "登录未完成")
        return 0 if ok else 1
    if not a.run:
        return 0

    if not targets:
        log("没有配置发送对象")
        return 2
    try:
        code = run(a.run, targets, a.message, cfg, force=a.force)
    except Exception:
        log("整体异常：\n" + traceback.format_exc())
        code = 2

    if a.notify and code != 0:
        info = read_last_run()
        failed = info.get("failed", "?")
        total = info.get("total", len(targets))
        notify_user(
            "抖音自动发消息：今天没跑完",
            f"有 {failed}/{total} 位好友没有发送成功。\n\n"
            "请打开「抖音自动发消息」程序，点「查看日志」把内容发给维护你的人。",
        )
    return code










