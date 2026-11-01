# 把晾衣绳截图打包成单个 exe（先 pip install pyinstaller）
import importlib.machinery
import os
import shutil

import PyInstaller.__main__
from PySide6.QtWidgets import QApplication

d = os.path.dirname(os.path.abspath(__file__))
app = QApplication([])
cl = importlib.machinery.SourceFileLoader("cl", os.path.join(d, "clothesline.pyw")).load_module()
ico = os.path.join(d, "clothesline.ico")
cl.make_icon().pixmap(256, 256).save(ico, "ICO")
shutil.rmtree(os.path.join(d, "build"), ignore_errors=True)
PyInstaller.__main__.run([
    os.path.join(d, "clothesline.pyw"), "--onefile", "--windowed", "--noconfirm", "--clean",
    "--name", "晾衣绳截图", "--icon", ico,
    "--specpath", d, "--distpath", os.path.join(d, "dist"), "--workpath", os.path.join(d, "build"),
    "--exclude-module", "numpy", "--exclude-module", "PySide6.QtWebEngineCore",
    "--exclude-module", "PySide6.QtWebEngineWidgets", "--exclude-module", "PySide6.QtQml",
    "--exclude-module", "PySide6.QtQuick", "--exclude-module", "PySide6.QtMultimedia",
    "--exclude-module", "PySide6.QtPdf", "--exclude-module", "PySide6.Qt3DCore",
])
print("完成：", os.path.join(d, "dist", "晾衣绳截图.exe"))