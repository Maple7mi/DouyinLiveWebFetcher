#!/usr/bin/env python
# coding: utf-8
"""一键登录抖音并读取浏览器 Cookie。

不抓账号密码，只打开一个独立的 Edge 配置目录，让用户自己扫码/登录；
登录完成后通过 Edge 的 CDP 接口读取 douyin.com 域下的 Cookie。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable

import websocket


class BrowserLoginError(RuntimeError):
    pass


class BrowserLoginCancelled(BrowserLoginError):
    pass


def find_edge() -> str | None:
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_json(url: str, timeout: float = 1.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _wait_for_browser(port: int, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            return _get_json(f"http://127.0.0.1:{port}/json/version")
        except Exception as exc:
            last_error = exc
        time.sleep(0.3)
    raise BrowserLoginError(f"登录窗口没有启动成功：{last_error or '无法连接 Edge 调试端口'}")


def _wait_for_target(port: int, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            targets = _get_json(f"http://127.0.0.1:{port}/json/list")
            pages = [t for t in targets if t.get("type") == "page"]
            if pages:
                douyin = [t for t in pages if "douyin.com" in (t.get("url") or "")]
                return (douyin or pages)[0]
        except Exception as exc:
            last_error = exc
        time.sleep(0.4)
    raise BrowserLoginError(f"登录窗口没有启动成功：{last_error or '无法连接 Edge 调试端口'}")


def _browser_ws_url(port: int) -> str:
    info = _wait_for_browser(port)
    ws = info.get("webSocketDebuggerUrl") or ""
    if not ws:
        raise BrowserLoginError("Edge 没有提供浏览器级调试地址。")
    return ws


class _Cdp:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(
            ws_url,
            timeout=3,
            origin="http://localhost",
            suppress_origin=True,
        )
        self.next_id = 0

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

    def call(self, method: str, params: dict | None = None, timeout: float = 5.0) -> dict:
        self.next_id += 1
        msg_id = self.next_id
        self.ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                continue
            data = json.loads(raw)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise BrowserLoginError(str(data["error"]))
                return data.get("result") or {}
        raise BrowserLoginError(f"Edge 调试接口超时：{method}")


def _cookie_header(cookies: list[dict]) -> str:
    wanted = {}
    for cookie in cookies:
        domain = (cookie.get("domain") or "").lstrip(".").lower()
        if not domain.endswith("douyin.com"):
            continue
        name = cookie.get("name")
        value = cookie.get("value")
        if not name or value is None:
            continue
        # 后面的同名 Cookie 覆盖前面的；通常 domain 更具体的排在后面。
        wanted[name] = value
    if not wanted.get("sessionid") and not wanted.get("sessionid_ss"):
        return ""
    return "; ".join(f"{name}={value}" for name, value in wanted.items())


def _read_cookies(cdp: "_Cdp") -> list[dict]:
    errors: list[str] = []
    for method in ("Storage.getCookies", "Network.getAllCookies"):
        try:
            result = cdp.call(method, timeout=8)
            return result.get("cookies") or []
        except BrowserLoginError as exc:
            errors.append(f"{method}: {exc}")
    raise BrowserLoginError("读取登录 Cookie 失败：" + "；".join(errors))


def login_and_get_cookie(
    profile_dir: str | Path,
    *,
    status_cb: Callable[[str], None] | None = None,
    timeout: float = 300.0,
    cancel_event=None,
) -> str:
    """打开 Edge 让用户登录；返回可用的 Cookie 请求头。

    status_cb 会在后台线程里被调用，调用方负责转到 UI 线程。
    """
    edge = find_edge()
    if not edge:
        raise BrowserLoginError("没有找到 Microsoft Edge，请先安装 Edge。")

    profile = Path(profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    args = [
        edge,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=msEdgeSidebarV2",
        "--app=https://www.douyin.com/",
    ]
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    cdp = None
    try:
        if status_cb:
            status_cb("登录窗口已打开，请扫码或登录抖音…登录成功后先不要关闭 Edge，等程序提示成功")
        # 先确保页面已经起来，再连浏览器级 CDP；页面跳转不会影响浏览器级连接。
        _wait_for_target(port)
        try:
            cdp = _Cdp(_browser_ws_url(port))
        except Exception:
            target = _wait_for_target(port)
            cdp = _Cdp(target["webSocketDebuggerUrl"])
        deadline = time.time() + timeout
        last_report = 0.0
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise BrowserLoginCancelled("已取消登录")
            cookies = _read_cookies(cdp)
            header = _cookie_header(cookies)
            if header:
                if status_cb:
                    status_cb("已检测到登录，正在读取 Cookie…")
                return header
            if proc.poll() is not None:
                # Edge 可能把窗口交给后台进程；再读一次，仍失败才报错。
                cookies = _read_cookies(cdp)
                header = _cookie_header(cookies)
                if header:
                    return header
                raise BrowserLoginError(
                    "登录窗口已关闭，未读到登录 Cookie。请重新点击登录，"
                    "登录成功后先不要关闭 Edge，等程序提示成功。"
                )
            now = time.time()
            if status_cb and now - last_report > 5:
                last_report = now
                status_cb("等待登录中…请在 Edge 窗口里扫码，登录成功后不要关闭窗口")
            time.sleep(0.6)
        raise BrowserLoginError("登录超时，请重新点击登录。")
    finally:
        if cdp is not None:
            cdp.close()
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def clear_login_profile(profile_dir: str | Path) -> None:
    shutil.rmtree(Path(profile_dir), ignore_errors=True)
