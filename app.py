# -*- coding: utf-8 -*-
"""抖音自动发消息 · 图形界面（也是打包 exe 的入口）。

给完全不懂电脑的人用，所以：
  - 所有失败都有中文提示，并带「复制」按钮，方便把内容发给维护者
  - 启用定时任务前必须看一遍风险清单并勾选确认
  - 日志可以直接在程序里打开查看
双击运行 = 界面；带 --run / --login / --preview / --selftest 参数时走命令行。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
import traceback
from datetime import date, datetime
from pathlib import Path
from tkinter import messagebox, ttk
from xml.sax.saxutils import escape

import core
import paths

HERE = Path(__file__).resolve().parent
VERSION = "1.2"
TASK_PREFIX = "DouyinAutoMessage"
FONT = ("Microsoft YaHei UI", 10)

# 风险清单：(标题, 可能发生什么, 概率（经验估计）, 出事后怎么办)
RISKS = [
    ("抖音网页版改版",
     "抖音调整了界面（按钮改名、结构变化），脚本找不到「消息」或发送按钮，当天发不出去。",
     "低 · 每月约 10%~20%",
     "日志里会写「消息抽屉没打开」或「搜索结果里没有」。把日志发给我，通常改几行就能修好。"),
    ("登录状态过期",
     "登录失效，页面要求重新扫码。",
     "中 · 每 1~3 个月一次（每月约 20%~30%）",
     "日志写「页面被验证码/登录拦住」。点「登录抖音」重新扫码即可。"),
    ("弹出滑块验证码",
     "抖音检测到自动化行为，要求拖动滑块。",
     "中低 · 每月约 5%~10%",
     "脚本会停下并截图。手动过一次验证，之后一般恢复正常。"),
    ("电脑睡眠或关机",
     "到点时电脑在睡眠/关机，任务根本不会执行。",
     "取决于使用习惯；插电且不睡眠时接近 0",
     "唤醒后会自动补跑（时间可能偏晚）。建议插电、把电源计划设为「从不睡眠」。"),
    ("好友改名或重名",
     "搜索结果第一行是另一个人，消息发错对象。",
     "低 · 每月约 2%~5%",
     "日志会记录「搜索『xx』命中：…」，可以核对。发现发错请手动跟对方说明。"),
    ("账号被风控",
     "长期固定时间自动发送，被判定为机器行为，私信功能受限。",
     "低 · 每月低于 5%",
     "表现为发不出去或需要验证。建议把发送时间错开几分钟，偶尔手动发一次。"),
    ("杀毒软件或 SmartScreen 拦截",
     "程序没有数字签名，首次运行可能被拦住，导致打不开或任务不执行。",
     "中 · 首次运行约 30%~50%（一次性）",
     "点「更多信息 → 仍要运行」；在杀毒软件里把程序所在文件夹加入信任。"),
    ("网络中断",
     "发送时没网，当天全部失败。",
     "低 · 每月约 2%~5%",
     "日志会连续报失败。恢复网络后，晚上 20:00 那次会补发。"),
    ("Edge 被卸载或损坏",
     "系统里没有 Microsoft Edge，浏览器起不来，什么都发不了。",
     "极低",
     "程序启动时会直接提示。重装 Edge 即可。"),
    ("同一天重复发送",
     "一天内给同一个人发了两条。",
     "低 · 正常使用不会发生",
     "程序按「当天已发」跳过；只有手动删除 state.json 才会重复。"),
]


def run_command_line(argv: list[str]) -> int:
    """命令行模式：不创建窗口，直接干活（计划任务用）。"""
    return core.cli(argv)


# ---------------------------------------------------------------- 计划任务


def _task_command() -> tuple[str, str]:
    """返回 (可执行文件, 参数前缀)——打包后就是 exe 自己。"""
    if paths.is_frozen():
        return sys.executable, ""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pythonw if pythonw.exists() else sys.executable)
    return exe, f'"{HERE / "app.py"}" '


def _task_xml(mode: str, hhmm: str) -> str:
    """生成计划任务 XML（错过时间补跑、单实例、20 分钟上限）。"""
    exe, prefix = _task_command()
    start = f"{datetime.now():%Y-%m-%d}T{hhmm}:00"
    args = f"{prefix}--run {mode} --notify"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>抖音自动发消息自动发送（{mode}）</Description></RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT20M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec>
    <Command>{escape(exe)}</Command>
    <Arguments>{escape(args)}</Arguments>
  </Exec></Actions>
</Task>
"""


def register_tasks(cfg: dict) -> tuple[bool, str]:
    """按配置注册（或重建）早晚两个计划任务。"""
    times = cfg["times"]
    jobs = [("Morning", "morning", times["morning"])]
    if cfg.get("evening_enabled"):
        jobs.append(("Evening", "evening", times["evening"]))

    tmp_dir = paths.data_dir() / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    problems = []
    for suffix, mode, hhmm in jobs:
        xml_path = tmp_dir / f"{suffix}.xml"
        with open(xml_path, "w", encoding="utf-16") as f:
            f.write(_task_xml(mode, hhmm))
        proc = subprocess.run(
            ["schtasks", "/Create", "/TN", f"{TASK_PREFIX}{suffix}", "/XML", str(xml_path), "/F"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
        )
        if proc.returncode != 0:
            problems.append(f"{suffix}: {proc.stderr.strip() or proc.stdout.strip() or 'schtasks 失败'}")

    if not cfg.get("evening_enabled"):
        subprocess.run(["schtasks", "/Delete", "/TN", f"{TASK_PREFIX}Evening", "/F"],
                       capture_output=True, text=True)

    if problems:
        return False, "；".join(problems)
    return True, "定时任务已启用：早上 %s%s" % (
        times["morning"], ("，晚上 " + times["evening"]) if cfg.get("evening_enabled") else "")


# ---------------------------------------------------------------- 界面


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"抖音自动发消息 v{VERSION} · 自动发送")
        self.geometry("780x820")
        self.minsize(720, 720)
        self.cfg = paths.load_config()
        self.status_var = tk.StringVar(value="就绪")
        self._build()
        self._refresh_login_state()
        self.report_callback_exception = self._on_tk_exception

    # -- 布局 --
    def _build(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)

        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text="抖音自动发消息", font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        self.login_var = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.login_var, foreground="#888").pack(side="right")

        # 发送对象
        box1 = ttk.LabelFrame(outer, text=" 发送对象（抖音私信里搜得到的昵称） ", padding=10)
        box1.pack(fill="both", expand=False, pady=(12, 0))
        list_row = ttk.Frame(box1)
        list_row.pack(fill="both", expand=True)
        self.targets = tk.Listbox(list_row, height=5, font=FONT, activestyle="none")
        self.targets.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_row, command=self.targets.yview)
        sb.pack(side="left", fill="y")
        self.targets.configure(yscrollcommand=sb.set)
        for t in self.cfg["targets"]:
            self.targets.insert("end", t)

        add_row = ttk.Frame(box1)
        add_row.pack(fill="x", pady=(8, 0))
        self.new_target = tk.StringVar()
        entry = ttk.Entry(add_row, textvariable=self.new_target, font=FONT)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _e: self._add_target())
        ttk.Button(add_row, text="添加", command=self._add_target).pack(side="left", padx=(6, 0))
        ttk.Button(add_row, text="删除选中", command=self._del_target).pack(side="left", padx=(6, 0))

        # 祝福语
        box2 = ttk.LabelFrame(outer, text=" 祝福语（一行一条，按日期自动轮换） ", padding=10)
        box2.pack(fill="both", expand=True, pady=(12, 0))
        msg_row = ttk.Frame(box2)
        msg_row.pack(fill="both", expand=True)
        self.messages = tk.Text(msg_row, height=9, font=FONT, wrap="word", undo=True)
        self.messages.pack(side="left", fill="both", expand=True)
        msg_sb = ttk.Scrollbar(msg_row, command=self.messages.yview)
        msg_sb.pack(side="left", fill="y")
        self.messages.configure(yscrollcommand=msg_sb.set)
        self.messages.insert("1.0", "\n".join(self.cfg["messages"]))

        # 时间
        box3 = ttk.LabelFrame(outer, text=" 发送时间 ", padding=10)
        box3.pack(fill="x", pady=(12, 0))
        row = ttk.Frame(box3)
        row.pack(fill="x")
        ttk.Label(row, text="早上", font=FONT).pack(side="left")
        self.morning_var = tk.StringVar(value=self.cfg["times"]["morning"])
        ttk.Entry(row, textvariable=self.morning_var, width=8, font=FONT).pack(side="left", padx=(6, 18))
        ttk.Label(row, text="晚上", font=FONT).pack(side="left")
        self.evening_var = tk.StringVar(value=self.cfg["times"]["evening"])
        ttk.Entry(row, textvariable=self.evening_var, width=8, font=FONT).pack(side="left", padx=(6, 18))
        self.evening_on = tk.BooleanVar(value=bool(self.cfg.get("evening_enabled")))
        ttk.Checkbutton(row, text="晚上检查并补发", variable=self.evening_on).pack(side="left")

        # 按钮两行
        btns = ttk.Frame(outer)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="保存设置", command=self._save).pack(side="left")
        self.btn_login = ttk.Button(btns, text="登录抖音", command=self._login)
        self.btn_login.pack(side="left", padx=(8, 0))
        self.btn_run = ttk.Button(btns, text="立即发送一次", command=self._run_now)
        self.btn_run.pack(side="left", padx=(8, 0))
        self.btn_task = ttk.Button(btns, text="保存并启用定时任务", command=self._save_and_register)
        self.btn_task.pack(side="left", padx=(8, 0))

        btns2 = ttk.Frame(outer)
        btns2.pack(fill="x", pady=(8, 0))
        ttk.Button(btns2, text="查看日志", command=self._open_logs).pack(side="left")
        ttk.Button(btns2, text="自我检查", command=self._diagnose).pack(side="left", padx=(8, 0))
        ttk.Button(btns2, text="打开数据目录", command=self._open_data).pack(side="left", padx=(8, 0))
        ttk.Button(btns2, text="卸载并清理", command=self._uninstall).pack(side="left", padx=(8, 0))

        ttk.Label(outer, textvariable=self.status_var, foreground="#555").pack(fill="x", pady=(12, 0))

    # -- 数据 --
    def _collect(self) -> dict:
        """把界面内容收成配置；时间格式不对会抛 ValueError。"""
        targets = [self.targets.get(i) for i in range(self.targets.size())]
        messages = [m.strip() for m in self.messages.get("1.0", "end").splitlines() if m.strip()]
        for name, var in (("早上", self.morning_var), ("晚上", self.evening_var)):
            value = var.get().strip()
            try:
                datetime.strptime(value, "%H:%M")
            except ValueError:
                raise ValueError(f"{name}时间要写成 HH:MM，例如 07:00") from None
        return {
            "targets": targets,
            "messages": messages,
            "times": {"morning": self.morning_var.get().strip(), "evening": self.evening_var.get().strip()},
            "evening_enabled": bool(self.evening_on.get()),
        }

    def _save(self) -> bool:
        try:
            cfg = self._collect()
        except ValueError as exc:
            messagebox.showerror("格式不对", str(exc))
            return False
        if not cfg["targets"]:
            messagebox.showwarning("还没填人", "先在「发送对象」里加至少一个昵称。")
            return False
        if not cfg["messages"]:
            messagebox.showwarning("还没填祝福语", "至少写一条祝福语。")
            return False
        paths.save_config(cfg)
        self.cfg = cfg
        self.status_var.set(f"已保存：{len(cfg['targets'])} 人，{len(cfg['messages'])} 条祝福语")
        return True

    # -- 启用定时任务（先看风险）--
    def _save_and_register(self) -> None:
        if not self._save():
            return
        if not core.edge_available():
            messagebox.showerror("找不到 Edge 浏览器",
                                 "自动发送需要系统自带的 Microsoft Edge。\n请先安装 Edge，再回来启用。")
            return
        if not core.is_logged_in():
            if messagebox.askyesno("还没登录", "还没有登录抖音，现在去登录吗？登录成功后再点「保存并启用」。",
                                   icon="warning"):
                self._login()
            return
        if not self._risk_dialog():
            self.status_var.set("已取消启用")
            return
        ok, text = register_tasks(self.cfg)
        self.status_var.set(text)
        if ok:
            messagebox.showinfo(
                "已启用",
                text + "\n\n到点会自动打开浏览器发送（约 1 分钟），期间系统会静音。\n\n"
                        "建议再点一次「立即发送一次」，确认第一个好友能收到。",
            )
        else:
            self._error_dialog("启用失败", text, "把这段内容发给我，我来帮你处理。")

    def _risk_dialog(self) -> bool:
        """风险清单 + 勾选确认；返回是否继续。"""
        win = tk.Toplevel(self)
        win.title("启用前请先看这些风险")
        win.geometry("820x640")
        win.transient(self)
        win.grab_set()

        ttk.Label(win, text="启用后，电脑会按设定时间自动登录抖音并发送消息。",
                  font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        ttk.Label(win, text="可能发生的事情和概率（经验估计，不是保证）：",
                  foreground="#555").pack(anchor="w", padx=14)

        wrap = ttk.Frame(win)
        wrap.pack(fill="both", expand=True, padx=14, pady=8)
        text = tk.Text(wrap, wrap="word", font=FONT, height=20)
        text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, command=text.yview)
        sb.pack(side="left", fill="y")
        text.configure(yscrollcommand=sb.set)
        for i, (title, what, prob, fix) in enumerate(RISKS, 1):
            text.insert("end", f"{i}. {title}\n", "head")
            text.insert("end", f"   可能：{what}\n")
            text.insert("end", f"   概率：{prob}\n")
            text.insert("end", f"   出事后：{fix}\n\n")
        text.tag_configure("head", font=("Microsoft YaHei UI", 10, "bold"))
        text.configure(state="disabled")

        agree = tk.BooleanVar(value=False)
        ttk.Checkbutton(win, text="我已知晓以上风险，遇到问题会把日志发给维护者",
                        variable=agree, command=lambda: ok_btn.configure(
                            state="normal" if agree.get() else "disabled")).pack(anchor="w", padx=14)

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=14, pady=10)
        ok_btn = ttk.Button(bar, text="我已知晓，启用定时任务", state="disabled",
                            command=lambda: (setattr(win, "result", True), win.destroy()))
        ok_btn.pack(side="right")
        ttk.Button(bar, text="取消", command=win.destroy).pack(side="right", padx=(0, 8))

        win.result = False
        self.wait_window(win)
        return bool(win.result)

    # -- 登录 --
    def _refresh_login_state(self) -> None:
        if not core.edge_available():
            self.login_var.set("状态：没找到 Edge（需要先安装）")
        elif core.is_logged_in():
            self.login_var.set("状态：已登录抖音")
        else:
            self.login_var.set("状态：未登录（点「登录抖音」扫码）")

    def _login(self) -> None:
        if not messagebox.askyesno("登录抖音",
                                   "会打开一个浏览器窗口，请用抖音 App 扫码登录。\n\n登录成功后窗口会自动关闭。继续吗？"):
            return
        self.btn_login.configure(state="disabled")
        self.status_var.set("等待扫码登录…（在浏览器窗口里操作）")
        cmd = [sys.executable, "--login"] if paths.is_frozen() else [sys.executable, str(HERE / "app.py"), "--login"]
        proc = self._spawn(cmd)
        deadline = time.time() + 300

        def poll() -> None:
            if proc.poll() is None and time.time() < deadline:
                self.after(2000, poll)
                return
            self.btn_login.configure(state="normal")
            self._refresh_login_state()
            if core.is_logged_in():
                self.status_var.set("登录成功")
            else:
                self._error_dialog(
                    "登录没有完成",
                    "没有检测到抖音登录状态。\n\n常见原因：二维码没扫、扫完没在手机上确认、或者网络卡住。\n请重新点「登录抖音」再试一次。",
                    "如果反复失败，把这段提示和日志发给我。",
                )

        self.after(2000, poll)

    # -- 立即发送 --
    def _run_now(self) -> None:
        try:
            cfg = self._collect()
        except ValueError as exc:
            messagebox.showerror("格式不对", str(exc))
            return
        if not cfg["targets"]:
            messagebox.showwarning("还没填人", "先加一个发送对象。")
            return
        if not core.is_logged_in():
            messagebox.showwarning("还没登录", "请先点「登录抖音」扫码登录。")
            return
        first = cfg["targets"][0]
        if not messagebox.askyesno(
            "立即发送",
            f"会真的给「{first}」发一条今天的祝福语。\n\n先拿它试一下，确认能收到再启用定时任务。继续吗？",
        ):
            return
        paths.save_config(cfg)
        self.cfg = cfg
        self.btn_run.configure(state="disabled")
        self.status_var.set(f"正在发送给「{first}」…（浏览器会弹出，几十秒）")
        cmd = ([sys.executable, "--run", "morning", "--only", first, "--force"]
               if paths.is_frozen() else
               [sys.executable, str(HERE / "app.py"), "--run", "morning", "--only", first, "--force"])
        proc = self._spawn(cmd)
        deadline = time.time() + 600

        def poll() -> None:
            if proc.poll() is None and time.time() < deadline:
                self.after(3000, poll)
                return
            self.btn_run.configure(state="normal")
            self._after_run()

        self.after(3000, poll)

    def _after_run(self) -> None:
        """手动发送结束后，用大白话报告结果。"""
        info = core.read_last_run()
        tail = self._log_tail(30)
        if info.get("failed", 0):
            self.status_var.set("发送失败")
            self._error_dialog(
                "没有发送成功",
                "这次没有发出去。常见原因是：还没登录、抖音要求验证码、或者网络断了。\n\n"
                "请先点「登录抖音」确认登录状态，再试一次。",
                tail,
            )
        elif info:
            self.status_var.set(f"发送完成（{info.get('time')}）")
            messagebox.showinfo("发送完成", "已经发出去了。\n\n去抖音里确认一下能收到，然后就可以点「保存并启用定时任务」。")
        else:
            self.status_var.set("发送结束")
            self._error_dialog("没有拿到运行结果", "请把日志发给维护者。", tail)

    # -- 自我检查 --
    def _diagnose(self) -> None:
        cfg = self.cfg
        info = core.read_last_run()
        checks: list[tuple[bool, str]] = []
        checks.append((core.edge_available(), "系统里有 Microsoft Edge"))
        checks.append((core.is_logged_in(), "已登录抖音"))
        checks.append((bool(cfg.get("targets")), f"发送对象 {len(cfg.get('targets') or [])} 个"))
        checks.append((bool(cfg.get("messages")), f"祝福语 {len(cfg.get('messages') or [])} 条"))
        try:
            datetime.strptime(cfg["times"]["morning"], "%H:%M")
            datetime.strptime(cfg["times"]["evening"], "%H:%M")
            checks.append((True, f"时间：早上 {cfg['times']['morning']}，晚上 {cfg['times']['evening']}"))
        except Exception:
            checks.append((False, "时间格式不对"))

        task_ok = False
        try:
            out = subprocess.run(["schtasks", "/Query", "/TN", f"{TASK_PREFIX}Morning"],
                                 capture_output=True, text=True, encoding="utf-8", errors="ignore")
            task_ok = out.returncode == 0
        except Exception:
            pass
        checks.append((task_ok, "定时任务已注册"))
        if info:
            checks.append((not info.get("failed"), f"最近一次运行：{info.get('time')}，失败 {info.get('failed')}/{info.get('total')}"))

        lines = [("✓ " if ok else "✗ ") + text for ok, text in checks]
        lines.append("")
        lines.append("最近日志：")
        lines.append(self._log_tail(20))
        body = "\n".join(lines)
        title = "一切正常" if all(ok for ok, _ in checks) else "有项目需要处理"
        self._text_dialog(title, body, copy_hint="点「复制」把内容发给维护者。")

    # -- 日志 --
    def _log_dir(self) -> Path:
        return Path(core.LOGS)

    def _log_tail(self, n: int = 30) -> str:
        folder = self._log_dir()
        files = sorted(folder.glob("*.log")) if folder.exists() else []
        if not files:
            return "(还没有日志)"
        lines = files[-1].read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-n:])

    def _open_logs(self) -> None:
        win = tk.Toplevel(self)
        win.title("运行日志")
        win.geometry("760x520")
        win.transient(self)

        top = ttk.Frame(win, padding=(12, 10, 12, 0))
        top.pack(fill="x")
        ttk.Label(top, text="选择日期：").pack(side="left")
        files = sorted(self._log_dir().glob("*.log"), reverse=True) if self._log_dir().exists() else []
        names = [f.stem for f in files] or ["(没有日志)"]
        pick = ttk.Combobox(top, values=names, state="readonly", width=16)
        pick.pack(side="left")
        if names:
            pick.current(0)

        box = ttk.Frame(win, padding=12)
        box.pack(fill="both", expand=True)
        text = tk.Text(box, wrap="none", font=("Consolas", 9))
        text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, command=text.yview)
        sb.pack(side="left", fill="y")
        text.configure(yscrollcommand=sb.set)

        def load(_e=None) -> None:
            name = pick.get()
            path = self._log_dir() / f"{name}.log"
            content = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else "(空)"
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", content)
            text.see("end")
            text.configure(state="disabled")

        pick.bind("<<ComboboxSelected>>", load)
        if files:
            load()

        bar = ttk.Frame(win, padding=(12, 0, 12, 12))
        bar.pack(fill="x")

        def copy_all() -> None:
            self.clipboard_clear()
            self.clipboard_append(text.get("1.0", "end").strip())
            self.status_var.set("日志已复制，可以直接粘贴发送")

        ttk.Button(bar, text="复制全部（发给维护者）", command=copy_all).pack(side="left")
        ttk.Button(bar, text="打开日志文件夹",
                   command=lambda: subprocess.Popen(["explorer", str(self._log_dir())])).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")

    # -- 通用对话框 --
    def _text_dialog(self, title: str, body: str, copy_hint: str = "") -> None:
        """展示一段文字，带「复制」按钮（方便贴给维护者）。"""
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("720x520")
        win.transient(self)
        if copy_hint:
            ttk.Label(win, text=copy_hint, foreground="#555").pack(anchor="w", padx=12, pady=(10, 0))
        box = ttk.Frame(win, padding=12)
        box.pack(fill="both", expand=True)
        text = tk.Text(box, wrap="word", font=FONT)
        text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, command=text.yview)
        sb.pack(side="left", fill="y")
        text.configure(yscrollcommand=sb.set)
        text.insert("1.0", body)
        text.configure(state="disabled")

        bar = ttk.Frame(win, padding=(12, 0, 12, 12))
        bar.pack(fill="x")

        def copy_body() -> None:
            self.clipboard_clear()
            self.clipboard_append(body)
            self.status_var.set("内容已复制到剪贴板")

        ttk.Button(bar, text="复制", command=copy_body).pack(side="left")
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")

    def _error_dialog(self, title: str, message: str, detail: str = "") -> None:
        body = message + ("\n\n----- 技术信息（复制给维护者）-----\n" + detail if detail else "")
        self._text_dialog(title, body, copy_hint="遇到问题：点下面的「复制」，把内容发给维护者即可。")

    def _on_tk_exception(self, exc_type, exc_value, exc_tb) -> None:
        """界面内部异常的兜底：记录并弹出可复制的错误框。"""
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            core.log("界面异常：\n" + tb)
        except Exception:
            pass
        self._error_dialog("程序出错了", "这是一个未预料到的错误，但程序没有崩溃。", tb)

    # -- 卸载 --
    def _uninstall(self) -> None:
        """撤销本程序对电脑做的所有改动。"""
        folder = Path(sys.executable).resolve().parent if paths.is_frozen() else HERE
        data = paths.data_dir()
        try:
            size_mb = sum(f.stat().st_size for f in data.rglob("*") if f.is_file()) / 1e6 if data.exists() else 0
        except Exception:
            size_mb = 0

        win = tk.Toplevel(self)
        win.title("卸载抖音自动发消息")
        win.geometry("720x500")
        win.transient(self)
        win.grab_set()
        ttk.Label(win, text="卸载后，下面这些改动会被撤销：",
                  font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 6))
        body = tk.Text(win, wrap="word", font=FONT, height=14)
        body.pack(fill="both", expand=True, padx=16)
        body.insert("end", "1. 计划任务：DouyinAutoMessageMorning、DouyinAutoMessageEvening（到点不再自动发送）\n")
        body.insert("end", f"2. 数据目录：{data}\n   约 {size_mb:.0f} MB（含登录状态、日志、截图、发送记录）\n")
        body.insert("end", "3. 如果上次运行异常退出把系统静音了，会恢复原状态\n\n")
        body.insert("end", "不会改动：注册表、系统设置、驱动、你日常用的 Edge 浏览器数据。\n")
        body.configure(state="disabled")

        del_folder = tk.BooleanVar(value=False)
        ttk.Checkbutton(win, text=f"同时删除程序文件夹（{folder}）",
                        variable=del_folder).pack(anchor="w", padx=16, pady=(8, 0))
        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=16, pady=12)

        def do_it() -> None:
            win.destroy()
            ok, lines = core.uninstall(remove_program_folder=bool(del_folder.get()))
            self._uninstall_done(ok, lines)

        ttk.Button(bar, text="确认卸载", command=do_it).pack(side="right")
        ttk.Button(bar, text="取消", command=win.destroy).pack(side="right", padx=(0, 8))

    def _uninstall_done(self, ok: bool, lines: list[str]) -> None:
        win = tk.Toplevel(self)
        win.title("卸载完成" if ok else "卸载没有完全成功")
        win.geometry("720x440")
        ttk.Label(win, text="已完成：" if ok else "有一部分没删掉：",
                  font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", padx=16, pady=(14, 6))
        text = tk.Text(win, wrap="word", font=FONT)
        text.pack(fill="both", expand=True, padx=16)
        text.insert("1.0", "\n".join(lines))
        text.insert("end", "\n\n" + ("关闭这个窗口后，程序会自动退出。" if ok else "请把上面的内容截图发给维护者。"))
        text.configure(state="disabled")

        def close() -> None:
            win.destroy()
            self.destroy()

        ttk.Button(win, text="关闭", command=close).pack(pady=(0, 14))
        win.protocol("WM_DELETE_WINDOW", close)

    # -- 杂项 --
    @staticmethod
    def _spawn(cmd: list[str]) -> subprocess.Popen:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)

    def _open_data(self) -> None:
        subprocess.Popen(["explorer", str(paths.data_dir())])

    def _add_target(self) -> None:
        name = self.new_target.get().strip()
        if not name:
            return
        existing = [self.targets.get(i) for i in range(self.targets.size())]
        if name in existing:
            messagebox.showinfo("已经有了", f"「{name}」已经在列表里。")
            return
        self.targets.insert("end", name)
        self.new_target.set("")

    def _del_target(self) -> None:
        for i in reversed(self.targets.curselection()):
            self.targets.delete(i)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1].startswith("--"):
        return run_command_line(sys.argv[1:])
    try:
        paths.load_config()
        App().mainloop()
    except Exception:
        tb = traceback.format_exc()
        try:
            (paths.data_dir() / "crash.log").write_text(tb, encoding="utf-8")
        except Exception:
            pass
        core.notify_user("抖音自动发消息启动失败",
                         "程序没能启动。\n\n请把程序文件夹里的 crash.log 发给维护者。\n\n" + tb[-500:])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())





