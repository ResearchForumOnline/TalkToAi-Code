# Third-party dependencies

This source distribution does not bundle third-party runtime binaries or model weights. Setup downloads pinned packages and their dependency/license metadata from PyPI. The MIT license applies only to the original TalkToAi Code source.

- PySide6 / Shiboken / Qt: LGPLv3/GPLv3 or applicable commercial terms. https://doc.qt.io/qtforpython-6/licenses.html . Source: https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://code.qt.io/ . Keep applicable notices/source rights when redistributing binaries; replaceable dynamic libraries remain replaceable.
- Playwright: Apache-2.0. https://github.com/microsoft/playwright-python . Browser use requires an existing Microsoft Edge installation under Microsoft's terms.
- pywinauto: BSD-3-Clause. https://github.com/pywinauto/pywinauto
- Pillow: HPND and included component licenses. https://github.com/python-pillow/Pillow
- pypdf: BSD-3-Clause. https://github.com/py-pdf/pypdf
- comtypes: MIT. https://github.com/enthought/comtypes
- pywin32: Python Software Foundation license and included notices. https://github.com/mhammond/pywin32
- PyInstaller (build dependency): GPL with its bootloader exception. https://pyinstaller.org/en/stable/license.html
- Python: PSF license and included notices. https://docs.python.org/3/license.html

The Aider automatic-check workflow and DevSpace Personal evidence-first ideas informed original integration work; their code is not included here. This project is not affiliated with OpenAI, Cline, Aider, Qt or model publishers. Model weights, runtimes, Godot, Unity and Blender are installed separately and retain their own licenses.

## Windows binary distributions

The 8.5.0 Windows installer and portable archive bundle Python and the required dynamic dependencies. Their `LICENSES` directory preserves the applicable installed distribution notices, and the original app license is included beside this document. Qt/PySide6 libraries remain separate replaceable DLLs in `_internal/PySide6`; no model weights or third-party browser installation are included. The source and corresponding version information for dependencies are linked above. PySide6/Shiboken 6.8.3 sources are available from the official `v6.8.3` source tag at https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.8.3 and Qt 6.8.3 source at https://download.qt.io/archive/qt/6.8/6.8.3/single/ . Installed package metadata and included notices determine each component's applicable terms.
