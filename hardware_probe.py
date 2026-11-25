"""Read this computer's memory, processor cores and graphics memory.

Read-only. It asks the operating system (and nvidia-smi when present) a few
fixed questions, changes nothing, and sends nothing anywhere. Every value is
best effort: a figure that cannot be read is reported as 0, never guessed.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys


_GIB = 1024 ** 3
_DISPLAY_CLASS = r'SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'


def _run(arguments, timeout=6):
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired):
        return ''
    return result.stdout.decode('utf-8', errors='replace') if result.returncode == 0 else ''


def _vendor(name):
    lower = name.lower()
    if any(word in lower for word in ('nvidia', 'geforce', 'rtx', 'gtx', 'quadro', 'tesla')):
        return 'nvidia'
    if any(word in lower for word in ('amd', 'radeon', 'ati ')):
        return 'amd'
    if any(word in lower for word in ('intel', 'iris', 'uhd graphics', 'arc ')):
        return 'intel'
    if 'apple' in lower:
        return 'apple'
    return 'other'


def ram_bytes():
    try:
        if sys.platform == 'win32':
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total', ctypes.c_ulonglong),
                            ('available', ctypes.c_ulonglong), ('total_page', ctypes.c_ulonglong),
                            ('available_page', ctypes.c_ulonglong), ('total_virtual', ctypes.c_ulonglong),
                            ('available_virtual', ctypes.c_ulonglong), ('extended', ctypes.c_ulonglong)]
            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total)
            return 0
        if sys.platform == 'darwin':
            return int(_run(['/usr/sbin/sysctl', '-n', 'hw.memsize']).strip() or 0)
        for line in Path('/proc/meminfo').read_text(encoding='ascii', errors='replace').splitlines():
            if line.startswith('MemTotal:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, AttributeError):
        pass
    return 0


def physical_cores():
    """Processor cores without hyper-threads. Falls back to 0 when the system does not say."""
    try:
        if sys.platform == 'win32':
            import ctypes
            needed = ctypes.c_ulong(0)
            ctypes.windll.kernel32.GetLogicalProcessorInformationEx(0, None, ctypes.byref(needed))
            if not 0 < needed.value < 4 * 1024 * 1024:
                return 0
            buffer = ctypes.create_string_buffer(needed.value)
            if not ctypes.windll.kernel32.GetLogicalProcessorInformationEx(0, buffer, ctypes.byref(needed)):
                return 0
            cores, offset = 0, 0
            while offset + 8 <= needed.value:
                relationship, size = struct.unpack_from('<II', buffer, offset)
                if size < 8:
                    break
                cores += relationship == 0  # RelationProcessorCore
                offset += size
            return cores
        if sys.platform == 'darwin':
            return int(_run(['/usr/sbin/sysctl', '-n', 'hw.physicalcpu']).strip() or 0)
        return parse_cpuinfo(Path('/proc/cpuinfo').read_text(encoding='ascii', errors='replace'))
    except (OSError, ValueError, AttributeError):
        return 0


def parse_cpuinfo(text):
    cores = set()
    package = core = None
    count = 0
    for line in text.splitlines() + ['']:
        if not line.strip():
            if core is not None:
                cores.add((package, core))
            package = core = None
            continue
        key, _, value = line.partition(':')
        key = key.strip()
        if key == 'processor':
            count += 1
        elif key == 'physical id':
            package = value.strip()
        elif key == 'core id':
            core = value.strip()
    return len(cores) if cores else 0


def parse_nvidia(text):
    gpus = []
    for line in text.splitlines():
        name, _, memory = line.rpartition(',')
        try:
            megabytes = float(memory.strip())
        except ValueError:
            continue
        if name.strip() and megabytes > 0:
            gpus.append({'name': name.strip(), 'vram_bytes': int(megabytes * 1024 * 1024), 'vendor': 'nvidia'})
    return gpus


def _windows_gpus():
    import winreg
    found = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS) as group:
            index = 0
            while index < 64:
                try:
                    name = winreg.EnumKey(group, index)
                except OSError:
                    break
                index += 1
                if not re.fullmatch(r'\d{4}', name):
                    continue
                try:
                    with winreg.OpenKey(group, name) as adapter:
                        label = str(winreg.QueryValueEx(adapter, 'DriverDesc')[0])
                        size = 0
                        for value in ('HardwareInformation.qwMemorySize', 'HardwareInformation.MemorySize'):
                            try:
                                raw = winreg.QueryValueEx(adapter, value)[0]
                            except OSError:
                                continue
                            size = int.from_bytes(raw, 'little') if isinstance(raw, bytes) else int(raw)
                            if size > 0:
                                break
                        found.append({'name': label, 'vram_bytes': max(0, size), 'vendor': _vendor(label)})
                except (OSError, ValueError, TypeError):
                    continue
    except OSError:
        pass
    return found


def _linux_gpus():
    found = []
    vendors = {'0x10de': 'nvidia', '0x1002': 'amd', '0x8086': 'intel'}
    try:
        cards = sorted(Path('/sys/class/drm').glob('card[0-9]'))
    except OSError:
        return found
    for card in cards:
        try:
            vendor = vendors.get((card / 'device' / 'vendor').read_text().strip().lower(), 'other')
            total = card / 'device' / 'mem_info_vram_total'
            size = int(total.read_text().strip()) if total.is_file() else 0
        except (OSError, ValueError):
            continue
        found.append({'name': vendor.upper() + ' graphics', 'vram_bytes': size, 'vendor': vendor})
    return found


def graphics():
    """Graphics adapters with their dedicated memory, largest first."""
    gpus = []
    smi = shutil.which('nvidia-smi')
    if smi:
        gpus = parse_nvidia(_run([smi, '--query-gpu=name,memory.total', '--format=csv,noheader,nounits']))
    try:
        others = _windows_gpus() if sys.platform == 'win32' else _linux_gpus() if sys.platform.startswith('linux') else []
    except ImportError:
        others = []
    known = {gpu['name'].lower() for gpu in gpus}
    for gpu in others:
        # nvidia-smi already reported its cards with exact figures.
        if gpu['vendor'] == 'nvidia' and gpus:
            continue
        if gpu['name'].lower() not in known:
            gpus.append(gpu)
    return sorted(gpus, key=lambda gpu: -gpu['vram_bytes'])


def probe():
    """One snapshot of this computer. Takes a second or two when nvidia-smi is present."""
    total = ram_bytes()
    logical = os.cpu_count() or 0
    cores = physical_cores()
    apple = sys.platform == 'darwin' and platform.machine().lower() in ('arm64', 'aarch64')
    gpus = [] if apple else graphics()
    dedicated = max((gpu['vram_bytes'] for gpu in gpus if gpu['vendor'] != 'intel'), default=0)
    if apple:
        # Apple silicon shares one memory pool; Metal lets a process use roughly two thirds of it.
        gpus = [{'name': 'Apple silicon', 'vram_bytes': int(total * 0.66), 'vendor': 'apple'}]
        dedicated = gpus[0]['vram_bytes']
    return {'platform': sys.platform, 'machine': platform.machine(), 'cpu_logical': logical,
            'cpu_physical': cores if 0 < cores <= max(logical, cores) else 0, 'ram_bytes': total, 'gpus': gpus[:4],
            'vram_bytes': dedicated, 'unified_memory': apple,
            'probed_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}


def describe(hardware):
    """One readable line, with nothing invented for values that could not be read."""
    parts = []
    gpu = next((item for item in hardware.get('gpus', []) if item['vram_bytes'] == hardware.get('vram_bytes')), None)
    if hardware.get('unified_memory'):
        parts.append('Apple silicon, shared memory')
    elif gpu and gpu['vram_bytes'] >= _GIB:
        parts.append(f"{gpu['vram_bytes'] / _GIB:.0f} GB graphics memory ({gpu['name']})")
    elif hardware.get('gpus'):
        parts.append('no separate graphics memory detected (' + hardware['gpus'][0]['name'] + ')')
    else:
        parts.append('no graphics card detected')
    if hardware.get('ram_bytes'):
        parts.append(f"{hardware['ram_bytes'] / _GIB:.0f} GB memory")
    if hardware.get('cpu_physical'):
        parts.append(f"{hardware['cpu_physical']} processor cores")
    elif hardware.get('cpu_logical'):
        parts.append(f"{hardware['cpu_logical']} processor threads")
    return ' · '.join(parts)


def cached(folder, *, max_age_days=30, refresh=False):
    """Return a recent saved snapshot, or take and save a new one."""
    path = Path(folder) / 'hardware.json'
    if not refresh:
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            age = datetime.now(timezone.utc) - datetime.fromisoformat(saved['probed_at'])
            if 0 <= age.days < max_age_days and saved.get('platform') == sys.platform and 'ram_bytes' in saved:
                return saved
        except (OSError, ValueError, KeyError, TypeError):
            pass
    hardware = probe()
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(hardware, indent=2), encoding='utf-8')
        temporary.replace(path)
    except OSError:
        pass
    return hardware
