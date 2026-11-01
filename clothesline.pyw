# -*- coding: utf-8 -*-
"""晾衣绳截图：后台常驻，快捷键截图，截图挂在屏幕顶部的绳子上，要用时直接拖下来。

依赖：pip install PySide6
启动：双击本文件（用 pythonw 运行，不弹黑框）
"""
import ctypes
import json
import math
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

from PySide6.QtCore import (QAbstractNativeEventFilter, QElapsedTimer, QMimeData, QObject, QPoint,
                            QPointF, QRect, QRectF, Qt, QTimer, QUrl)
from PySide6.QtGui import (QAction, QColor, QCursor, QDesktopServices, QDrag, QFont, QGuiApplication,
                           QIcon, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QLabel, QLineEdit, QMenu, QMessageBox, QSpinBox,
                               QSystemTrayIcon, QToolTip, QVBoxLayout, QWidget)

FROZEN = getattr(sys, "frozen", False)
BASE = Path(__file__).resolve().parent
if FROZEN:
    # 打包成 exe 后程序在临时目录里运行：截图放进「图片\晾衣绳截图」，配置放进 AppData
    _home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    SHOTS = _home / "Pictures" / "晾衣绳截图"
    _data = Path(os.environ.get("APPDATA", str(_home))) / "Clothesline"
    _data.mkdir(parents=True, exist_ok=True)
    CONFIG = _data / "config.json"
    LOG = _data / "clothesline.log"
else:
    SHOTS = BASE / "shots"
    CONFIG = BASE / "config.json"
    LOG = BASE / "clothesline.log"
ARCHIVE = SHOTS / "已归档"
APP_NAME = "晾衣绳截图"


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def _excepthook(t, v, tb):
    log("".join(traceback.format_exception(t, v, tb)))


sys.excepthook = _excepthook


def ival(x):
    """PySide6 的枚举和 int 混用时统一转成 int。"""
    return int(getattr(x, "value", x))


# ---------------------------------------------------------------- Windows 接口
IS_WIN = os.name == "nt"
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 0xB0B0

if IS_WIN:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]


def register_hotkey(hk):
    if not IS_WIN:
        return True
    user32.UnregisterHotKey(None, HOTKEY_ID)
    return bool(user32.RegisterHotKey(None, HOTKEY_ID, hk["mods"] | MOD_NOREPEAT, hk["vk"]))


def foreground_is_fullscreen(own_ids):
    """前台窗口是否全屏占满主显示器（最大化的窗口不算，它不盖住任务栏）。"""
    if not IS_WIN:
        return False
    hwnd = user32.GetForegroundWindow()
    if not hwnd or int(hwnd) in own_ids:
        return False
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    if buf.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
        return False
    mon = user32.MonitorFromWindow(hwnd, 2)
    if mon != user32.MonitorFromPoint(wintypes.POINT(0, 0), 1):
        return False
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    m = mi.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


def recycle(path):
    """删到回收站，误删还能找回来。"""
    if IS_WIN:
        op = SHFILEOPSTRUCTW(None, 3, str(path) + "\0", None, 0x40 | 0x10 | 0x4 | 0x400, False, None, None)
        if shell32.SHFileOperationW(ctypes.byref(op)) == 0:
            return
    try:
        os.remove(path)
    except OSError as e:
        log(f"删除失败 {path}: {e}")


def set_autostart(on):
    if not IS_WIN:
        return
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                         0, winreg.KEY_SET_VALUE)
    try:
        if on:
            if FROZEN:
                cmd = f'"{sys.executable}"'
            else:
                exe = Path(sys.executable)
                pyw = exe.with_name("pythonw.exe")
                exe = pyw if pyw.exists() else exe
                cmd = f'"{exe}" "{Path(__file__).resolve()}"'
            winreg.SetValueEx(key, "Clothesline", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "Clothesline")
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if IS_WIN:
            et = event_type.data() if hasattr(event_type, "data") else bytes(event_type)
            if et == b"windows_generic_MSG":
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    QTimer.singleShot(0, self.callback)
        return False, 0


# ---------------------------------------------------------------- 配置
DEFAULT_CFG = {
    "hotkey": {"mods": MOD_CONTROL | MOD_ALT, "vk": 0x58, "text": "Ctrl+Alt+X"},
    "copy_after_capture": True,
    "autohide": False,
    "hide_fullscreen": True,
    "autostart": False,
    "max_items": 30,
}


def load_cfg():
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    try:
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save_cfg(cfg):
    try:
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        log(f"保存配置失败: {e}")


# ---------------------------------------------------------------- 绳子
ROPE_H = 40          # 绳子窗口高度
ANCHOR = 10          # 两端钉子离屏幕边缘的距离
SAG = 12             # 绳子中间下垂多少
ROPE_COLOR = QColor("#9c7a4c")
ROPE_LIGHT = QColor("#dcc08c")


class RopeWindow(QWidget):
    """只负责画绳子，鼠标完全穿透，不挡任何操作。"""

    def __init__(self, ctrl):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.ctrl = ctrl
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.place()

    def place(self):
        g = self.ctrl.screen.geometry()
        self.setGeometry(g.x(), g.y(), g.width(), ROPE_H)

    def y_at(self, x):
        w = max(1, self.width() - 2 * ANCHOR)
        t = min(1.0, max(0.0, (x - ANCHOR) / w))
        return 8 + SAG * 4 * t * (1 - t)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        if self.ctrl.collapsed:
            p.setPen(QPen(QColor(156, 122, 76, 200), 3))
            p.drawLine(QPointF(0, 1.5), QPointF(w, 1.5))
            return
        path = QPainterPath(QPointF(ANCHOR, self.y_at(ANCHOR)))
        x = ANCHOR
        while x < w - ANCHOR:
            x = min(w - ANCHOR, x + 16)
            path.lineTo(QPointF(x, self.y_at(x)))
        p.setPen(QPen(QColor(0, 0, 0, 45), 3.2))
        p.drawPath(path.translated(0, 1.5))
        p.setPen(QPen(ROPE_COLOR, 2.6, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path)
        twist = QPen(ROPE_LIGHT, 1.1)
        twist.setDashPattern([2, 3])
        p.setPen(twist)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#6b6f75"))
        for ax in (ANCHOR, w - ANCHOR):
            p.drawEllipse(QPointF(ax, self.y_at(ax)), 3.2, 3.2)
        # 图片太多时提示左右还有
        p.setPen(QColor(156, 122, 76, 230))
        f = QFont(self.font())
        f.setPixelSize(18)
        f.setBold(True)
        p.setFont(f)
        if self.ctrl.offset > 0:
            p.drawText(QRectF(ANCHOR + 6, 14, 20, 24), Qt.AlignCenter, "‹")
        if self.ctrl.more_right:
            p.drawText(QRectF(w - ANCHOR - 26, 14, 20, 24), Qt.AlignCenter, "›")


# ---------------------------------------------------------------- 挂在绳上的截图
THUMB_W, THUMB_H = 104, 78
BORDER = 4           # 相纸白边
CLIP_H = 16          # 夹子高度
SIDE = 14            # 左右留给摆动的空间
BOTTOM = 16          # 下方留给阴影和下落动画的空间
GAP = 2
PIVOT_Y = 2          # 摆动的支点（夹子顶端）


def ease_out_back(p):
    c1 = 1.70158
    return 1 + (c1 + 1) * (p - 1) ** 3 + c1 * (p - 1) ** 2


class ShotItem(QWidget):
    def __init__(self, ctrl, path):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.ctrl, self.path = ctrl, Path(path)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_AlwaysShowToolTips)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("单击复制 · 拖出去用 · 双击打开 · 右键更多 · 滚轮翻看")
        self.thumb = self._make_thumb()
        dpr = self.thumb.devicePixelRatio()
        tw, th = self.thumb.width() / dpr, self.thumb.height() / dpr
        self.photo = QRectF(SIDE, CLIP_H - 5, tw + 2 * BORDER, th + 2 * BORDER)
        self.resize(int(math.ceil(self.photo.width() + 2 * SIDE)), int(math.ceil(self.photo.bottom() + BOTTOM)))
        self.slot_visible = True
        self.taken = False
        self.angle = 0.0
        self.amp = 0.0
        self.dy = 0.0
        self.alpha = 1.0
        self.swing_at = self.appear_at = self.die_at = None
        self.press = None
        self.clock = QElapsedTimer()
        self.clock.start()
        self.timer = QTimer(self, interval=16, timeout=self._tick)

    def _make_thumb(self):
        dpr = self.ctrl.dpr
        img = QImage(str(self.path))
        if img.isNull():
            img = QImage(THUMB_W, THUMB_H, QImage.Format_ARGB32)
            img.fill(QColor("#cccccc"))
        s = min(THUMB_W * dpr / img.width(), THUMB_H * dpr / img.height(), 1.0)
        w, h = max(int(28 * dpr), int(img.width() * s)), max(int(20 * dpr), int(img.height() * s))
        pm = QPixmap.fromImage(img.scaled(w, h, Qt.IgnoreAspectRatio if s == 1.0 else Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation))
        pm.setDevicePixelRatio(dpr)
        return pm

    # ---- 动画
    def now(self):
        return self.clock.elapsed() / 1000

    def swing(self, amp):
        self.amp, self.swing_at = amp, self.now()
        self.timer.start()

    def appear(self, amp=8):
        self.appear_at = self.now()
        self.swing(amp)

    def die(self):
        self.die_at = self.now()
        self.timer.start()

    def _tick(self):
        t = self.now()
        busy = False
        if self.swing_at is not None:
            s = t - self.swing_at
            self.angle = self.amp * math.exp(-s / 0.55) * math.sin(2 * math.pi * 1.7 * s)
            if s > 3:
                self.angle, self.swing_at = 0.0, None
            else:
                busy = True
        if self.appear_at is not None:
            p = min(1.0, (t - self.appear_at) / 0.32)
            self.dy = -10 * (1 - ease_out_back(p))
            self.alpha = min(1.0, p * 2)
            if p >= 1:
                self.appear_at, self.dy, self.alpha = None, 0.0, 1.0
            else:
                busy = True
        if self.die_at is not None:
            p = min(1.0, (t - self.die_at) / 0.32)
            self.dy, self.alpha = 22 * p * p, 1 - p
            if p >= 1:
                self.timer.stop()
                self.ctrl.remove_item(self)
                return
            busy = True
        if not busy:
            self.timer.stop()
        self.update()

    # ---- 绘制
    def paint_photo(self, p):
        r = self.photo
        p.setPen(Qt.NoPen)
        for grow, a in ((3, 16), (2, 26), (1, 38)):
            p.setBrush(QColor(0, 0, 0, a))
            p.drawRoundedRect(r.adjusted(-grow + 1, -grow + 2, grow + 1, grow + 2), 3, 3)
        p.setBrush(QColor("#fbfaf7"))
        p.drawRoundedRect(r, 2, 2)
        p.drawPixmap(QPointF(r.x() + BORDER, r.y() + BORDER), self.thumb)

    def paint_clip(self, p, cx):
        p.setPen(QPen(QColor("#7a5a32"), 1))
        p.setBrush(QColor("#caa36c"))
        spread = 12 if self.taken else 0
        for side in (-1, 1):
            p.save()
            p.translate(cx, PIVOT_Y + 1)
            p.rotate(side * spread)
            p.drawRoundedRect(QRectF(-3.6 if side < 0 else 0, 0, 3.6, CLIP_H), 1.2, 1.2)
            p.restore()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#8d9096"))
        p.drawRect(QRectF(cx - 4.6, PIVOT_Y + 6, 9.2, 2.2))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cx = self.width() / 2
        p.translate(cx, PIVOT_Y)
        p.rotate(self.angle)
        p.translate(-cx, -PIVOT_Y)
        if not self.taken:
            p.save()
            p.setOpacity(self.alpha)
            p.translate(0, self.dy)
            self.paint_photo(p)
            p.restore()
        self.paint_clip(p, cx)

    def photo_pixmap(self):
        dpr = self.ctrl.dpr
        r = self.photo
        pm = QPixmap(int((r.width() + 8) * dpr), int((r.height() + 8) * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(4 - r.x(), 4 - r.y())
        self.paint_photo(p)
        p.end()
        return pm

    # ---- 鼠标
    def enterEvent(self, _):
        if not self.timer.isActive() and not self.taken:
            self.swing(3)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.press = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self.press is not None and (e.position().toPoint() - self.press).manhattanLength() >= QApplication.startDragDistance():
            self.press = None
            self.start_drag()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.press is not None:
            self.press = None
            self.ctrl.copy_image(self)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path)))

    def wheelEvent(self, e):
        d = e.angleDelta()
        self.ctrl.scroll(-(d.y() or d.x()))

    def start_drag(self):
        if not self.path.exists():
            self.ctrl.notify("这张截图的文件已经不在了，可以右键删除它。")
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.path))])
        mime.setImageData(QImage(str(self.path)))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pm = self.photo_pixmap()
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(int(self.photo.width() / 2) + 4, 12))
        # "取下"：照片离开夹子跟着鼠标走，夹子张开空着
        self.taken = True
        self.update()
        self.ctrl.busy += 1
        try:
            drag.exec(Qt.CopyAction)
        finally:
            self.ctrl.busy -= 1
            # 实际上图片还在：重新挂回绳子，晃两下
            self.taken = False
            self.appear(8)

    def contextMenuEvent(self, e):
        m = QMenu()
        a_copy = m.addAction("复制图片")
        a_open = m.addAction("打开原图")
        a_dir = m.addAction("打开所在文件夹")
        a_save = m.addAction("另存为…")
        m.addSeparator()
        a_del = m.addAction("删除")
        self.ctrl.busy += 1
        try:
            act = m.exec(e.globalPos())
        finally:
            self.ctrl.busy -= 1
        if act == a_copy:
            self.ctrl.copy_image(self)
        elif act == a_open:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path)))
        elif act == a_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path.parent)))
        elif act == a_save:
            self.ctrl.busy += 1
            try:
                dst, _ = QFileDialog.getSaveFileName(None, "另存为", str(Path.home() / "Desktop" / self.path.name),
                                                     "PNG 图片 (*.png)")
            finally:
                self.ctrl.busy -= 1
            if dst:
                try:
                    shutil.copyfile(self.path, dst)
                except OSError as err:
                    self.ctrl.notify(f"保存失败：{err}")
        elif act == a_del:
            self.die()


# ---------------------------------------------------------------- 截图选区
class CaptureOverlay(QWidget):
    def __init__(self, session, screen, pm):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.session, self.pm = session, pm
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.origin = None
        self.sel = QRect()
        self.mouse = QPoint(-100, -100)
        self.geo = screen.geometry()
        self.setGeometry(self.geo)

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(QPoint(0, 0), self.pm)
        p.setRenderHint(QPainter.Antialiasing)
        shade = QPainterPath()
        shade.addRect(QRectF(self.rect()))
        if not self.sel.isNull():
            hole = QPainterPath()
            hole.addRect(QRectF(self.sel))
            shade = shade.subtracted(hole)
        p.fillPath(shade, QColor(0, 0, 0, 110))
        f = QFont(self.font())
        f.setPixelSize(13)
        p.setFont(f)
        fm = p.fontMetrics()
        if self.origin is None and self.sel.isNull():
            p.setPen(QPen(QColor(255, 255, 255, 90), 1))
            p.drawLine(self.mouse.x(), 0, self.mouse.x(), self.height())
            p.drawLine(0, self.mouse.y(), self.width(), self.mouse.y())
            tip = "拖动选择区域 · 单击截整个屏幕 · Esc 或右键取消"
            box = QRectF(0, 0, fm.horizontalAdvance(tip) + 24, fm.height() + 12)
            box.moveCenter(QPointF(self.width() / 2, 40))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(20, 22, 26, 200))
            p.drawRoundedRect(box, 8, 8)
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignCenter, tip)
            return
        p.setPen(QPen(QColor("#3a9bff"), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(self.sel))
        dpr = self.pm.devicePixelRatio()
        label = f"{round(self.sel.width() * dpr)} × {round(self.sel.height() * dpr)}"
        box = QRectF(0, 0, fm.horizontalAdvance(label) + 14, fm.height() + 6)
        y = self.sel.top() - box.height() - 4
        box.moveTopLeft(QPointF(self.sel.left(), y if y > 2 else self.sel.top() + 4))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 22, 26, 210))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QColor("#ffffff"))
        p.drawText(box, Qt.AlignCenter, label)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.session.finish(None)
        elif e.button() == Qt.LeftButton:
            self.origin = e.position().toPoint()
            self.sel = QRect(self.origin, self.origin)
            self.update()

    def mouseMoveEvent(self, e):
        self.mouse = e.position().toPoint()
        if self.origin is not None:
            self.sel = QRect(self.origin, self.mouse).normalized()
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self.origin is None:
            return
        r, self.origin = self.sel, None
        if r.width() < 4 or r.height() < 4:
            out = self.pm.copy()
        else:
            dpr = self.pm.devicePixelRatio()
            phys = QRect(round(r.x() * dpr), round(r.y() * dpr), round(r.width() * dpr), round(r.height() * dpr))
            out = self.pm.copy(phys.intersected(self.pm.rect()))
        out.setDevicePixelRatio(1.0)
        self.session.finish(out)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.session.finish(None)


class CaptureSession:
    """每块显示器一张遮罩，在哪块上框选都行。"""

    def __init__(self, done):
        self.done = done
        self.overlays = []
        for s in QGuiApplication.screens():
            ov = CaptureOverlay(self, s, s.grabWindow(0))
            self.overlays.append(ov)
            ov.show()
            if ov.windowHandle():
                ov.windowHandle().setScreen(s)
                ov.setGeometry(s.geometry())
        here = QGuiApplication.screenAt(QCursor.pos())
        for ov in self.overlays:
            if here is None or ov.geo == here.geometry():
                ov.raise_()
                ov.activateWindow()
                ov.setFocus()
                break

    def own_ids(self):
        return {int(ov.winId()) for ov in self.overlays}

    def finish(self, pm):
        if self.done is None:
            return
        done, self.done = self.done, None
        for ov in self.overlays:
            ov.close()
            ov.deleteLater()
        self.overlays = []
        done(pm)


# ---------------------------------------------------------------- 设置
class HotkeyEdit(QLineEdit):
    def __init__(self, hk):
        super().__init__(hk["text"])
        self.hk = dict(hk)
        self.setReadOnly(True)
        self.setPlaceholderText("点这里，然后按下组合键")

    def keyPressEvent(self, e):
        k = ival(e.key())
        if k in (ival(Qt.Key_Control), ival(Qt.Key_Shift), ival(Qt.Key_Alt), ival(Qt.Key_Meta)):
            return
        m = e.modifiers()
        mods, parts = 0, []
        for flag, mod, name in ((Qt.ControlModifier, MOD_CONTROL, "Ctrl"), (Qt.AltModifier, MOD_ALT, "Alt"),
                                (Qt.ShiftModifier, MOD_SHIFT, "Shift"), (Qt.MetaModifier, MOD_WIN, "Win")):
            if m & flag:
                mods |= mod
                parts.append(name)
        vk = e.nativeVirtualKey()
        is_fkey = 0x70 <= vk <= 0x87 or vk == 0x2C
        if not vk or (not mods and not is_fkey):
            self.setText("需要带上 Ctrl / Alt / Shift / Win 之一")
            return
        if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
            name = chr(vk)
        elif 0x70 <= vk <= 0x87:
            name = f"F{vk - 0x6F}"
        else:
            name = QKeySequence(k).toString() or f"键{vk}"
        self.hk = {"mods": mods, "vk": vk, "text": "+".join(parts + [name])}
        self.setText(self.hk["text"])


class SettingsDialog(QDialog):
    def __init__(self, cfg):
        super().__init__(None, Qt.WindowStaysOnTopHint)
        self.setWindowTitle(f"{APP_NAME} · 设置")
        self.hotkey = HotkeyEdit(cfg["hotkey"])
        self.copy = QCheckBox("截图后自动复制到剪贴板")
        self.copy.setChecked(cfg["copy_after_capture"])
        self.autohide = QCheckBox("自动收起：鼠标移到屏幕最顶端才展开")
        self.autohide.setChecked(cfg["autohide"])
        self.fullscreen = QCheckBox("有程序全屏时（游戏、视频）自动隐藏")
        self.fullscreen.setChecked(cfg["hide_fullscreen"])
        self.autostart = QCheckBox("开机自动启动")
        self.autostart.setChecked(cfg["autostart"])
        self.max_items = QSpinBox()
        self.max_items.setRange(5, 100)
        self.max_items.setValue(cfg["max_items"])
        self.max_items.setSuffix(" 张")
        form = QFormLayout()
        form.addRow("截图快捷键", self.hotkey)
        form.addRow("绳上最多挂", self.max_items)
        note = QLabel("超出的旧截图会移到 shots\\已归档 文件夹，不会删除。")
        note.setStyleSheet("color: gray")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        for w in (self.copy, self.autohide, self.fullscreen, self.autostart):
            lay.addWidget(w)
        lay.addWidget(buttons)

    def values(self):
        return {"hotkey": self.hotkey.hk, "copy_after_capture": self.copy.isChecked(),
                "autohide": self.autohide.isChecked(), "hide_fullscreen": self.fullscreen.isChecked(),
                "autostart": self.autostart.isChecked(), "max_items": self.max_items.value()}


# ---------------------------------------------------------------- 托盘图标
def make_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    rope = QPainterPath(QPointF(4, 12))
    rope.quadTo(QPointF(32, 24), QPointF(60, 12))
    p.setPen(QPen(ROPE_COLOR, 4, Qt.SolidLine, Qt.RoundCap))
    p.drawPath(rope)
    p.translate(32, 17)
    p.rotate(-6)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#fbfaf7"))
    p.drawRoundedRect(QRectF(-17, 0, 34, 30), 3, 3)
    p.setBrush(QColor("#3a9bff"))
    p.drawRect(QRectF(-13, 4, 26, 18))
    p.setBrush(QColor("#caa36c"))
    p.drawRoundedRect(QRectF(-3, -6, 6, 12), 1.5, 1.5)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- 主控
class Clothesline(QObject):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.cfg = load_cfg()
        SHOTS.mkdir(parents=True, exist_ok=True)
        self.screen = app.primaryScreen()
        self.dpr = self.screen.devicePixelRatio()
        self.items = []
        self.offset = 0
        self.more_right = False
        self.busy = 0
        self.user_hidden = self.fs_hidden = self.capturing = False
        self.collapsed = bool(self.cfg["autohide"])
        self.leave_since = None
        self.session = None

        self.rope = RopeWindow(self)
        self._build_tray()

        self.hotkey_filter = HotkeyFilter(self.capture)
        app.installNativeEventFilter(self.hotkey_filter)
        hotkey_ok = register_hotkey(self.cfg["hotkey"])

        shots = sorted(SHOTS.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in shots[self.cfg["max_items"]:]:
            self._archive(f)
        for f in shots[:self.cfg["max_items"]]:
            self.items.append(ShotItem(self, f))
        self.layout()
        self.apply_visibility()

        self.poll_timer = QTimer(self, interval=80, timeout=self._poll_autohide)
        self.poll_timer.start()
        self.fs_timer = QTimer(self, interval=1000, timeout=self._poll_fullscreen)
        self.fs_timer.start()
        self.screen.geometryChanged.connect(self._screen_changed)

        if hotkey_ok:
            self.notify(f"已在后台运行，按 {self.cfg['hotkey']['text']} 截图。截图会挂在屏幕顶部的绳子上。")
        else:
            self.notify(f"快捷键 {self.cfg['hotkey']['text']} 被别的程序占用了，右键托盘图标 → 设置，换一个。")

    # ---- 托盘
    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        self.act_capture = menu.addAction("截图", self.capture)
        self.act_show = QAction("显示晾衣绳", menu, checkable=True, checked=True)
        self.act_show.toggled.connect(self._toggle_user_hidden)
        menu.addAction(self.act_show)
        menu.addAction("打开截图文件夹", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(SHOTS))))
        menu.addAction("把绳上的截图全部收进归档", self.clear_all)
        menu.addSeparator()
        menu.addAction("设置…", self.open_settings)
        menu.addAction("退出", self.quit)
        self.tray_menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self._update_tray_text()
        self.tray.show()

    def _update_tray_text(self):
        hk = self.cfg["hotkey"]["text"]
        self.tray.setToolTip(f"{APP_NAME}（{hk} 截图）")
        self.act_capture.setText(f"截图\t{hk}")

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.capture()

    def notify(self, text):
        log(text)
        if self.tray.isVisible() and QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(APP_NAME, text, make_icon(), 4000)

    def _toggle_user_hidden(self, shown):
        self.user_hidden = not shown
        self.apply_visibility()

    # ---- 布局和显示
    def layout(self):
        g = self.screen.geometry()
        x = ANCHOR + 26 - self.offset
        limit = g.width() - ANCHOR - 26
        self.more_right = False
        for it in self.items:
            w = it.width()
            it.slot_visible = x >= ANCHOR + 20 and x + w <= limit
            if x + w > limit:
                self.more_right = True
            cx = x + w / 2
            it.move(g.x() + int(x), g.y() + int(self.rope.y_at(cx)) - PIVOT_Y - 1)
            x += w + GAP
        self.content_w = x + self.offset
        self.rope.update()

    def apply_visibility(self):
        base = not (self.user_hidden or self.fs_hidden or self.capturing)
        self.rope.setVisible(base)
        self.rope.update()
        show_items = base and not self.collapsed
        for it in self.items:
            want = show_items and it.slot_visible
            if it.isVisible() != want:
                it.setVisible(want)
                if want:
                    it.raise_()

    def scroll(self, delta):
        g = self.screen.geometry()
        max_off = max(0, self.content_w - (g.width() - 2 * (ANCHOR + 26)))
        self.offset = int(min(max_off, max(0, self.offset + delta * 0.8)))
        self.layout()
        self.apply_visibility()

    def _screen_changed(self, *_):
        self.rope.place()
        self.layout()
        self.apply_visibility()

    def _poll_autohide(self):
        if not self.cfg["autohide"] or self.busy or self.capturing:
            return
        pos, g = QCursor.pos(), self.screen.geometry()
        in_x = g.left() <= pos.x() <= g.right()
        if self.collapsed:
            if in_x and pos.y() <= g.top() + 2:
                self.collapsed, self.leave_since = False, None
                self.apply_visibility()
            return
        if in_x and pos.y() <= g.top() + 135:
            self.leave_since = None
        elif self.leave_since is None:
            self.leave_since = time.monotonic()
        elif time.monotonic() - self.leave_since > 0.8:
            self.collapsed = True
            self.apply_visibility()

    def _poll_fullscreen(self):
        if self.capturing or self.busy:
            return
        fs = False
        if self.cfg["hide_fullscreen"]:
            own = {int(self.rope.winId())} | {int(it.winId()) for it in self.items if it.isVisible()}
            fs = foreground_is_fullscreen(own)
        if fs != self.fs_hidden:
            self.fs_hidden = fs
            self.apply_visibility()

    # ---- 截图
    def capture(self):
        if self.capturing:
            return
        self.capturing = True
        self.apply_visibility()
        QTimer.singleShot(160, self._start_capture)   # 等绳子先藏起来，别拍进去

    def _start_capture(self):
        try:
            self.session = CaptureSession(self._capture_done)
        except Exception:
            log(traceback.format_exc())
            self.capturing = False
            self.apply_visibility()

    def _capture_done(self, pm):
        self.session = None
        self.capturing = False
        if pm is not None and not pm.isNull():
            stamp = time.strftime("%Y%m%d_%H%M%S")
            path = SHOTS / f"截图_{stamp}_{int(time.time() * 1000) % 1000:03d}.png"
            if pm.save(str(path), "PNG"):
                if self.cfg["copy_after_capture"]:
                    QGuiApplication.clipboard().setImage(pm.toImage())
                self.add_shot(path)
            else:
                self.notify(f"截图保存失败：{path}")
        self.apply_visibility()

    def add_shot(self, path):
        it = ShotItem(self, path)
        self.items.insert(0, it)
        self.offset = 0
        self._trim()
        if self.cfg["autohide"]:
            # 新截图先展开给你看一眼，过一会儿再收起
            self.collapsed = False
            self.leave_since = time.monotonic() + 2
        self.layout()
        self.apply_visibility()
        it.appear(10)

    def _trim(self):
        while len(self.items) > self.cfg["max_items"]:
            old = self.items.pop()
            self._archive(old.path)
            old.close()
            old.deleteLater()

    def _archive(self, f):
        try:
            ARCHIVE.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(ARCHIVE / Path(f).name))
        except OSError as e:
            log(f"归档失败 {f}: {e}")

    def remove_item(self, it):
        if it in self.items:
            self.items.remove(it)
        recycle(it.path)
        it.close()
        it.deleteLater()
        self.layout()
        self.apply_visibility()

    def copy_image(self, it):
        img = QImage(str(it.path))
        if img.isNull():
            self.notify("这张截图的文件已经不在了。")
            return
        QGuiApplication.clipboard().setImage(img)
        QToolTip.showText(QCursor.pos(), "已复制到剪贴板")
        it.swing(4)

    def clear_all(self):
        if not self.items:
            return
        self.busy += 1
        try:
            ok = QMessageBox.question(None, APP_NAME, f"把绳上的 {len(self.items)} 张截图都收进“已归档”文件夹？\n文件不会删除。")
        finally:
            self.busy -= 1
        if ok != QMessageBox.Yes:
            return
        for it in self.items:
            self._archive(it.path)
            it.close()
            it.deleteLater()
        self.items = []
        self.offset = 0
        self.layout()

    # ---- 设置
    def open_settings(self):
        dlg = SettingsDialog(self.cfg)
        self.busy += 1
        try:
            if not dlg.exec():
                return
        finally:
            self.busy -= 1
        new = dlg.values()
        old_hk = self.cfg["hotkey"]
        if (new["hotkey"]["mods"], new["hotkey"]["vk"]) != (old_hk["mods"], old_hk["vk"]):
            if not register_hotkey(new["hotkey"]):
                register_hotkey(old_hk)
                new["hotkey"] = old_hk
                QMessageBox.warning(None, APP_NAME, "这个快捷键被别的程序占用了，已保留原来的快捷键。")
        if new["autostart"] != self.cfg["autostart"]:
            try:
                set_autostart(new["autostart"])
            except OSError as e:
                new["autostart"] = self.cfg["autostart"]
                QMessageBox.warning(None, APP_NAME, f"开机启动设置失败：{e}")
        self.cfg.update(new)
        save_cfg(self.cfg)
        self.collapsed = bool(self.cfg["autohide"])
        self.leave_since = None
        self._trim()
        self._update_tray_text()
        self.layout()
        self.apply_visibility()

    def quit(self):
        if IS_WIN:
            user32.UnregisterHotKey(None, HOTKEY_ID)
        self.tray.hide()
        self.app.quit()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(make_icon())
    if IS_WIN:
        app._mutex = kernel32.CreateMutexW(None, False, "Local\\ClotheslineScreenshot")
        if ctypes.get_last_error() == 183:
            QMessageBox.information(None, APP_NAME, "晾衣绳已经在运行了，看看屏幕顶部或右下角托盘。")
            return
    app._ctrl = Clothesline(app)
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log(traceback.format_exc())
        raise