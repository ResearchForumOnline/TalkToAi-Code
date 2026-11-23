"""Place the official offline Python CLI in a Windows desktop payload."""
import argparse
import hashlib
import io
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile


VERSION = '3.14.10'
URL = f'https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-embed-amd64.zip'
# Published SHA-256 at https://www.python.org/downloads/release/python-31410/
SHA256 = '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'
SIZE = 12_619_664
PIP_WHEEL = 'pip-26.3-py3-none-any.whl'
PIP_URL = ('https://files.pythonhosted.org/packages/07/25/'
           'ee8c38ae72d66b844ce8eb9711ba905dba21ca1a8a0c960057c41cd23337/' + PIP_WHEEL)
PIP_SHA256 = 'f3b60b20ffaadd4415ed7957212a38ac63fa4c5fa375bd0f4a797777940fad13'
PIP_SIZE = 1_950_951


def verified_bytes(url, size, digest, local_path=None):
    source = open(local_path, 'rb') if local_path else urllib.request.urlopen(url, timeout=60)
    with source:
        data = source.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f'Bytes do not match the pinned release at {url}')
    return data


def package(payload, archive=None, pip_archive=None):
    payload = Path(payload).resolve(strict=True)
    target = payload / 'project-python'
    if target.exists():
        raise FileExistsError(f'Project Python already exists in {payload}')
    data = verified_bytes(URL, SIZE, SHA256, archive)
    wheel = verified_bytes(PIP_URL, PIP_SIZE, PIP_SHA256, pip_archive)
    with tempfile.TemporaryDirectory(dir=payload) as temp, zipfile.ZipFile(io.BytesIO(data)) as bundle:
        stage = Path(temp) / 'project-python'
        stage.mkdir()
        for member in bundle.infolist():
            if member.filename != Path(member.filename).name:
                raise ValueError(f'Unexpected member in the embedded Python archive: {member.filename}')
            # This CLI runs ordinary project scripts. CPython's default embedded
            # path configuration excludes the script's directory and sibling modules.
            if member.filename.endswith('._pth'):
                continue
            bundle.extract(member, stage)
        shutil.copyfile(stage / 'python.exe', stage / 'python3.exe')
        for name in ('python314.dll', 'python314.zip', 'LICENSE.txt'):
            if not (stage / name).is_file():
                raise ValueError(f'Project Python archive is missing {name}')
        packages = stage / 'Lib' / 'site-packages'
        packages.mkdir(parents=True)
        (packages / PIP_WHEEL).write_bytes(wheel)
        (packages / 'pip-wheel.pth').write_text(PIP_WHEEL + '\n', encoding='ascii')
        with zipfile.ZipFile(io.BytesIO(wheel)) as wheel_zip:
            license_bytes = wheel_zip.read('pip-26.3.dist-info/licenses/LICENSE.txt')
            (stage / 'pip-LICENSE.txt').write_bytes(license_bytes)
        stage.rename(target)
    print(f'Bundled CPython {VERSION} and pip 26.3 for Windows project scripts with upstream licenses.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('payload')
    parser.add_argument('--archive', help='Use an existing copy of the pinned python.org archive.')
    parser.add_argument('--pip-wheel', help='Use an existing copy of the pinned PyPI wheel.')
    args = parser.parse_args()
    package(args.payload, args.archive, args.pip_wheel)
