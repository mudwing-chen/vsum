# -*- coding: utf-8 -*-
"""浏览器自动化：Chrome 系优先（独立 profile），Safari 只在显式指定时用。

为什么不默认 Safari：
- Safari 不支持无头，起一次弹一次窗，且共用日常登录态；
- Chrome/Edge 的 --user-data-dir 能把「vsum 的登录态」和日常浏览器隔开，
  删掉 profile 目录就等于退出登录。

Safari 的一次性准备（显式 --browser safari 才需要）：
    sudo safaridriver --enable
    Safari → 设置 → 高级 → 勾「显示“开发”菜单」→ 开发 → 允许远程自动化
"""
import os
import platform
import shutil
import time

def default_profile(env=None, system=None):
    """平台默认 profile：Windows 用 %LOCALAPPDATA%\\vsum\\chrome，其它用 ~/.cache/vsum/chrome。"""
    env = os.environ if env is None else env
    system = platform.system() if system is None else system
    if system == "Windows":
        root = env.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(root, "vsum", "chrome")
    return os.path.join(os.path.expanduser("~"), ".cache", "vsum", "chrome")


class BrowserError(RuntimeError):
    """浏览器起不来（没装、driver 缺失、扫码超时）。CLI 映射成退出码 3。"""


def is_macos():
    return platform.system() == "Darwin"


def _selenium():
    try:
        from selenium import webdriver  # noqa: F401
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise BrowserError(
            "没有 selenium：pip install 'vsum[browser]'（或 pip install selenium）"
        ) from exc
    from selenium import webdriver
    return webdriver


def safari_hint():
    return ("Safari 自动化没打开。一次性准备：" + chr(10) +
            "  1) sudo safaridriver --enable" + chr(10) +
            "  2) Safari → 设置 → 高级 → 勾「显示“开发”菜单」" + chr(10) +
            "  3) 菜单「开发」→ 允许远程自动化" + chr(10) +
            "Safari 不支持无头，运行时会弹窗口（正常）。")


def profile_dir(override=None):
    """独立 profile 目录：--profile > VSUM_BROWSER_PROFILE > 平台默认（见 default_profile）。"""
    raw = override or os.environ.get("VSUM_BROWSER_PROFILE") or default_profile()
    return os.path.abspath(os.path.expanduser(raw))


def windows_candidates(env=None):
    """Windows 三个安装根下的 Chrome / Edge 路径。纯函数，供测试传假 env。"""
    env = os.environ if env is None else env
    roots = (env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), env.get("LOCALAPPDATA"))
    out = []
    for root in roots:
        if not root:
            continue
        out.append(("chrome", os.path.join(root, "Google", "Chrome", "Application", "chrome.exe")))
        out.append(("edge", os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe")))
    return out


def candidates(system=None, env=None):
    """按平台列出 (kind, path)，Chrome 在前、Edge 在后。纯函数。"""
    system = system or platform.system()
    if system == "Darwin":
        return [
            ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ("chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    if system == "Windows":
        return windows_candidates(env)
    return [
        ("chrome", "/usr/bin/google-chrome"),
        ("chrome", "/usr/bin/chromium"),
        ("chrome", "/usr/bin/chromium-browser"),
        ("edge", "/usr/bin/microsoft-edge"),
    ]


def find_binary(kind, system=None, env=None):
    """找某个浏览器家族的可执行文件；找不到返回 None（交给 selenium-manager）。"""
    for k, path in candidates(system, env):
        if k == kind and path and os.path.exists(path):
            return path
    names = (("chrome", "google-chrome", "chromium") if kind == "chrome"
             else ("msedge", "microsoft-edge"))
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _options(target, headless, profile):
    if target == "edge":
        from selenium.webdriver.edge.options import Options
    else:
        from selenium.webdriver.chrome.options import Options
    opts = Options()
    opts.add_argument("--user-data-dir=" + profile_dir(profile))
    opts.add_argument("--profile-directory=Default")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--mute-audio")
    opts.add_argument("--no-first-run")
    opts.add_argument("--no-default-browser-check")
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
    return opts


def open_browser(prefer=None, headless=None, profile=None):
    """返回 (driver, kind)。kind 是 'safari' / 'chrome' / 'edge'。

    prefer: None/'auto' 先 Chrome 再 Edge（都不装才交给 selenium-manager）；
            'safari' 必须显式指定；'chrome'/'edge' 强制该家族。
    headless: 只对 Chrome 系有效；Safari 忽略（它没有无头）。
    profile: 独立 profile 目录，默认 ~/.cache/vsum/chrome。
    """
    kind = (prefer or "auto").lower()
    if kind not in ("auto", "safari", "chrome", "edge"):
        raise BrowserError("不认识的浏览器：%s（可选 auto/safari/chrome/edge）" % prefer)

    webdriver = _selenium()
    if kind == "safari":
        if not is_macos():
            raise BrowserError("Safari 只在 macOS 上可用；这台机器请用 chrome/edge")
        try:
            return webdriver.Safari(), "safari"
        except Exception as exc:
            raise BrowserError(safari_hint() + " 原始错误：" + str(exc)) from exc

    if kind == "auto":
        target = "chrome" if find_binary("chrome") else ("edge" if find_binary("edge") else "chrome")
    else:
        target = kind
    binary = find_binary(target)
    try:
        opts = _options(target, headless, profile)
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise BrowserError("selenium 版本太旧，没有 %s 的 Options" % target) from exc
    if binary:
        opts.binary_location = binary
    cls = webdriver.Edge if target == "edge" else webdriver.Chrome
    try:
        return cls(options=opts), target
    except Exception as exc:
        raise BrowserError(
            "%s 起不来（找不到浏览器时 selenium-manager 会尝试下载 driver）：%s" % (target, exc)
        ) from exc


def login_douyin(profile=None, timeout=300, prefer="chrome"):
    """起可见浏览器，等用户扫码。成功后返回 (driver, kind)，**不退出**，调用方关闭。"""
    driver, kind = open_browser(prefer=prefer, headless=False, profile=profile)
    try:
        driver.set_page_load_timeout(40)
        driver.get("https://www.douyin.com/")
        deadline = time.time() + timeout
        while time.time() < deadline:
            names = {c.get("name") for c in driver.get_cookies()}
            if "sessionid" in names or "sessionid_ss" in names:
                return driver, kind
            time.sleep(3)
        raise BrowserError("扫码超时（%d 秒）。窗口里扫码登录后重跑：vsum login douyin" % timeout)
    except Exception:
        close(driver)
        raise


def close(driver):
    try:
        driver.quit()
    except Exception:
        pass
