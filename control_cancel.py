"""Temporary physical-Escape cancellation; never records keyboard input."""
import ctypes
from ctypes import wintypes
import os
import threading
from types import SimpleNamespace

WH_KEYBOARD_LL=13
WM_KEYDOWN=0x0100
WM_SYSKEYDOWN=0x0104
WM_QUIT=0x0012
VK_ESCAPE=0x1B
LLKHF_INJECTED=0x10
LLKHF_LOWER_IL_INJECTED=0x02


def is_physical_escape(code,message,vk_code,flags):
    return (code>=0 and message in (WM_KEYDOWN,WM_SYSKEYDOWN) and vk_code==VK_ESCAPE
            and not flags & (LLKHF_INJECTED|LLKHF_LOWER_IL_INJECTED))


class _WindowsAPI:
    def __init__(self):
        if os.name!='nt':raise OSError('Global Escape requires Windows')
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        result=ctypes.c_ssize_t;uintptr=ctypes.c_size_t;intptr=ctypes.c_ssize_t
        self.callback_type=ctypes.WINFUNCTYPE(result,ctypes.c_int,uintptr,intptr)
        class Keyboard(ctypes.Structure):
            _fields_=[('vkCode',wintypes.DWORD),('scanCode',wintypes.DWORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('dwExtraInfo',uintptr)]
        self.keyboard_type=Keyboard
        self.user.SetWindowsHookExW.argtypes=[ctypes.c_int,self.callback_type,ctypes.c_void_p,wintypes.DWORD]
        self.user.SetWindowsHookExW.restype=ctypes.c_void_p
        self.user.UnhookWindowsHookEx.argtypes=[ctypes.c_void_p];self.user.UnhookWindowsHookEx.restype=wintypes.BOOL
        self.user.CallNextHookEx.argtypes=[ctypes.c_void_p,ctypes.c_int,uintptr,intptr];self.user.CallNextHookEx.restype=result
        self.user.GetMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),ctypes.c_void_p,wintypes.UINT,wintypes.UINT];self.user.GetMessageW.restype=ctypes.c_int
        self.user.PeekMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),ctypes.c_void_p,wintypes.UINT,wintypes.UINT,wintypes.UINT];self.user.PeekMessageW.restype=wintypes.BOOL
        self.user.PostThreadMessageW.argtypes=[wintypes.DWORD,wintypes.UINT,uintptr,intptr];self.user.PostThreadMessageW.restype=wintypes.BOOL
        self.kernel.GetCurrentThreadId.argtypes=[];self.kernel.GetCurrentThreadId.restype=wintypes.DWORD
        self.kernel.GetModuleHandleW.argtypes=[wintypes.LPCWSTR];self.kernel.GetModuleHandleW.restype=ctypes.c_void_p
        self.callback=None

    def thread_id(self):return self.kernel.GetCurrentThreadId()
    def ensure_queue(self):
        message=wintypes.MSG();self.user.PeekMessageW(ctypes.byref(message),None,0,0,0)
    def install(self,on_escape):
        def hook(code,message,pointer):
            try:
                if code>=0 and message in (WM_KEYDOWN,WM_SYSKEYDOWN) and pointer:
                    event=ctypes.cast(pointer,ctypes.POINTER(self.keyboard_type)).contents
                    if is_physical_escape(code,message,event.vkCode,event.flags):on_escape()
            except Exception:
                pass  # Never interrupt the user's keyboard hook chain.
            return self.user.CallNextHookEx(None,code,message,pointer)
        self.callback=self.callback_type(hook)
        handle=self.user.SetWindowsHookExW(WH_KEYBOARD_LL,self.callback,self.kernel.GetModuleHandleW(None),0)
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        return handle
    def pump(self,stop):
        message=wintypes.MSG()
        while not stop.is_set():
            result=self.user.GetMessageW(ctypes.byref(message),None,0,0)
            if result==0:return
            if result==-1:raise ctypes.WinError(ctypes.get_last_error())
    def quit(self,identifier):
        if identifier:self.user.PostThreadMessageW(identifier,WM_QUIT,0,0)
    def uninstall(self,handle):
        self.user.UnhookWindowsHookEx(handle)
        self.callback=None


class EscapeCancel:
    """Arm during one control session; callbacks run off the hook/UI threads.

    arm returns True only after hook registration. Callbacks should signal a
    captured task cancellation event or a thread-safe Qt signal, never Qt UI.
    Always disarm at task completion. This helper observes but never consumes Esc.
    """
    def __init__(self,api_factory=None,setup_timeout=1.0):
        self._factory=api_factory or _WindowsAPI
        self._timeout=max(.01,min(2.0,float(setup_timeout)))
        self._lock=threading.RLock();self._session=None

    def arm(self,callback):
        if not callable(callback):raise TypeError('Escape callback must be callable')
        with self._lock:
            self.disarm()
            try:api=self._factory()
            except (OSError,AttributeError):return False
            session=SimpleNamespace(api=api,stop=threading.Event(),pending=threading.Event(),ready=threading.Event(),
                                    installed=False,identifier=0,hook_thread=None,callback_thread=None)
            self._session=session
            def dispatch():
                session.pending.wait()
                if not session.stop.is_set():
                    try:callback()
                    except Exception:pass
            def run():
                handle=None
                try:
                    session.identifier=api.thread_id();api.ensure_queue()
                    if session.stop.is_set():return
                    handle=api.install(session.pending.set)
                    if not handle:raise OSError('Keyboard hook was not installed')
                    session.installed=True;session.ready.set()
                    if not session.stop.is_set():api.pump(session.stop)
                except Exception:
                    pass
                finally:
                    if handle:
                        try:api.uninstall(handle)
                        except Exception:pass
                    session.installed=False;session.stop.set();session.pending.set();session.ready.set()
            session.callback_thread=threading.Thread(target=dispatch,name='TalkToAi Escape signal',daemon=True)
            session.hook_thread=threading.Thread(target=run,name='TalkToAi Escape hook',daemon=True)
            session.callback_thread.start();session.hook_thread.start()
            ready=session.ready.wait(self._timeout)
            if not ready or not session.installed or session.stop.is_set():
                self.disarm();return False
            return True

    def disarm(self):
        with self._lock:
            session=self._session;self._session=None
            if session is None:return
            session.stop.set();session.pending.set()
            try:session.api.quit(session.identifier)
            except Exception:pass
            current=threading.current_thread()
            for thread in (session.hook_thread,session.callback_thread):
                if thread and thread is not current:thread.join(.3)
