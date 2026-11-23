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

The Windows package includes the CPython 3.14.10 x64 embeddable distribution for project commands. Its original license and component notices are included in `project-python/LICENSE.txt`. The build verifies the published SHA-256, omits the embedding-only path override so project modules can be imported, and includes a second executable name for `python3`. [CPython release and checksums](https://www.python.org/downloads/release/python-31410/) · [Corresponding source](https://github.com/python/cpython/tree/v3.14.10)

The bundled project interpreter includes PyPA pip 26.3. The build verifies the original wheel against PyPI's SHA-256. Its MIT license is in `project-python/pip-LICENSE.txt`, and all bundled component notices are preserved inside the unchanged wheel. [PyPI release](https://pypi.org/project/pip/26.3/) · [Corresponding source](https://github.com/pypa/pip/tree/26.3)

Windows builds from 9.0.2 include the Qt Multimedia and Qt TextToSpeech libraries from PySide6 Addons 6.8.3. The multimedia backend uses dynamically linked FFmpeg 7.1 under LGPL 2.1 or later. This software is based in part on the work of the Independent JPEG Group. The package preserves Qt’s FFmpeg and component credits and license texts in `LICENSES/FFmpeg`. [FFmpeg 7.1 source](https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n7.1.tar.gz) · [Qt 6.8.3 attributions](https://github.com/qt/qtmultimedia/tree/v6.8.3/src/3rdparty/ffmpeg) · [Qt build scripts](https://github.com/qt/qt5/tree/v6.8.3/coin/provisioning)

The 8.6.0 Store candidate uses the same dependency-notice process described below. Its exact bundled versions and included notices are recorded in its `LICENSES/DEPENDENCIES.json`.

The 8.5.0 Windows installer and portable archive bundle Python and the required dynamic dependencies. Their `LICENSES` directory preserves the applicable installed distribution notices, and the original app license is included beside this document. Qt/PySide6 libraries remain separate replaceable DLLs in `_internal/PySide6`; no model weights or third-party browser installation are included. The source and corresponding version information for dependencies are linked above. PySide6/Shiboken 6.8.3 sources are available from the official `v6.8.3` source tag at https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.8.3 and Qt 6.8.3 source at https://download.qt.io/archive/qt/6.8/6.8.3/single/ . Installed package metadata and included notices determine each component's applicable terms.

## Kenney game assets

The Windows app includes 25 game asset packs created by Kenney under CC0 1.0. The bundled collection contains the original archives, individual license notices and a catalog linking each pack to its creator page. Project imports include the creator notice and source receipt. [Kenney assets](https://kenney.nl/assets) · [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/)
