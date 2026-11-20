"""Windows accessibility integration using pywinauto and Win32 window discovery.

Each input consumes a recent observation; inspect again afterwards.
"""
import json
import os
import sys
import time
import uuid
from itertools import islice
from pathlib import Path


_MAX_CONTROLS = 250
_MAX_SCANNED_CONTROLS = 1200
_CALL_TIMEOUT = 20


class ComputerTools:
    def __init__(self, project, cancel, desktop=None):
        self.project=Path(project);self.cancel=cancel;self.desktop=desktop
        self.window=None;self.controls={};self.observed=0;self.handles=set()
        self.window_bounds=None;self.pixel_observed=0;self.pixel_bounds=None
        self.window_handle=None;self.window_identity=None
        self.native=desktop is None
        self.window_processes={}

    def _validate_window(self):
        if self.cancel.is_set():raise InterruptedError('Computer control stopped.')
        if (not self.window or not self.window.is_visible() or not self.window.is_enabled()
                or self.window.handle != self.window_handle):
            raise ValueError('The observed window is now hidden, disabled or replaced. List windows and inspect again.')
        if self.native and self._process(self.window_handle)!=self.window_processes.get(self.window_handle):
            raise ValueError('The observed window is owned by a different process. List windows and inspect again.')
        identity=getattr(self.window.element_info, 'runtime_id', None)
        if self.window_identity is not None and (tuple(identity) if isinstance(identity,(tuple,list)) else identity) != self.window_identity:
            raise ValueError('The observed window was replaced. List windows and inspect again.')
        rect=self.window.rectangle()
        if (rect.left,rect.top,rect.right,rect.bottom)!=self.window_bounds:
            raise ValueError('The window moved or resized after inspection. Inspect again before acting.')
        return rect

    def connect(self):
        if self.desktop is None:
            if os.name!='nt':raise RuntimeError('Native computer control requires an interactive Windows desktop.')
            sys.coinit_flags=2
            try:
                from pywinauto import Desktop
                self.desktop=Desktop(backend='uia')
            except (ImportError, OSError) as exc:
                raise RuntimeError('Windows accessibility dependencies could not load. Use the current TalkToAi Code build or reinstall its runtime dependencies.') from exc

    def _process(self, handle):
        import win32process
        return win32process.GetWindowThreadProcessId(handle)

    def _windows(self):
        if not self.native:
            return [{'handle':str(window.handle),'title':window.window_text()[:200]}
                    for window in self.desktop.windows() if window.is_visible()][:80]
        import pywintypes
        import win32gui
        windows=[]
        self.window_processes.clear()

        def collect(handle, _):
            if len(windows)>=120 or not win32gui.IsWindowVisible(handle):
                return
            title=win32gui.GetWindowText(handle)
            if title:
                try:identity=self._process(handle)
                except pywintypes.error:return
                self.window_processes[handle]=identity
                windows.append({'handle':str(handle),'title':title[:200],'pid':identity[1],
                                'minimized':bool(win32gui.IsIconic(handle))})

        win32gui.EnumWindows(collect, None)
        if not windows:
            raise RuntimeError('No titled windows were found. Open the target app on the signed-in Windows desktop. A locked or disconnected remote session can prevent control.')
        return windows

    def _activate(self):
        self.window.set_focus()
        if self.native:
            import pywintypes
            import win32gui
            if win32gui.GetForegroundWindow()!=self.window_handle:
                try:win32gui.SetForegroundWindow(self.window_handle)
                except pywintypes.error:pass
            if win32gui.GetForegroundWindow()!=self.window_handle:
                raise ValueError('Windows denied foreground access. Bring the target window to the front and inspect it again.')

    def _assert_point_visible(self, x, y):
        if self.native:
            import win32con
            import win32gui
            point_window=win32gui.WindowFromPoint((x,y))
            if not point_window or win32gui.GetAncestor(point_window, win32con.GA_ROOT)!=self.window_handle:
                raise ValueError('Another window covers the observed point. Activate and inspect the target again.')

    def _click_control(self, control):
        self._activate()
        self._validate_window()
        if self.native:
            area=control.rectangle()
            self._assert_point_visible((area.left+area.right)//2, (area.top+area.bottom)//2)
        if self.cancel.is_set():raise InterruptedError('Computer control stopped.')
        control.click_input()

    def execute(self, action, target='', value=''):
        if self.cancel.is_set():raise InterruptedError('Computer control stopped.')
        self.connect()
        if action=='windows':
            self.window=None;self.controls={};self.observed=0
            self.window_bounds=None;self.pixel_observed=0;self.pixel_bounds=None
            self.handles.clear()
            windows=self._windows()
            self.handles={w['handle'] for w in windows}
            return json.dumps(windows)
        if action=='inspect':
            if str(target) not in self.handles:raise ValueError('List windows first, then inspect a returned handle.')
            self.observed=0;self.controls={}
            self.window_bounds=None;self.pixel_observed=0;self.pixel_bounds=None
            handle=int(target)
            if self.native and self._process(handle)!=self.window_processes.get(handle):
                raise ValueError('The listed window changed ownership. List windows again.')
            self.window=self.desktop.window(handle=handle).wrapper_object()
            if not self.window.is_visible() or not self.window.is_enabled():
                self.window=None
                raise ValueError('The listed window is now hidden or disabled. List windows again.')
            self.window_handle=self.window.handle
            identity=getattr(self.window.element_info, 'runtime_id', None)
            self.window_identity=tuple(identity) if isinstance(identity,(tuple,list)) else None
            entries=[];snapshot=uuid.uuid4().hex[:12]
            query=str(value).strip().casefold()
            items=self.window.iter_descendants() if self.native else self.window.descendants()
            deadline=time.monotonic()+8
            truncated=False
            for index,control in enumerate(islice(items,_MAX_SCANNED_CONTROLS+1)):
                if index>=_MAX_SCANNED_CONTROLS or time.monotonic()>deadline or len(entries)>=_MAX_CONTROLS:
                    truncated=True
                    break
                if self.cancel.is_set():raise InterruptedError('Computer control stopped.')
                try:
                    info=control.element_info
                    if info.element.CurrentIsPassword:continue
                    name=control.window_text()[:300]
                    if query and query not in name.casefold() and query not in info.control_type.casefold():continue
                    if not control.is_visible():continue
                    key=f'{snapshot}:{len(entries)}'
                    rect=control.rectangle()
                    entries.append({'id':key,'name':name,'type':info.control_type,
                                    'enabled':bool(control.is_enabled()),
                                    'bounds':[rect.left,rect.top,rect.right,rect.bottom]})
                    self.controls[key]=control
                except Exception:continue
            self.observed=time.monotonic()
            window_rect=self.window.rectangle()
            self.window_bounds=(window_rect.left,window_rect.top,window_rect.right,window_rect.bottom)
            return json.dumps({'title':self.window.window_text(),'window_bounds':[window_rect.left,window_rect.top,window_rect.right,window_rect.bottom],'controls':entries,'scan_truncated':truncated,
                               'instruction':'Use a returned control id from this snapshot; older ids are invalid. For a point click, take a screenshot first and use window-relative x,y from that image. Input consumes this snapshot. Inspect again after each action. Stop button cancels further actions.'})
        if action=='wait':
            seconds=float(value or '1')
            if not 0 <= seconds <= 10:raise ValueError('Computer wait must be between 0 and 10 seconds.')
            if self.cancel.wait(seconds):raise InterruptedError('Computer control stopped.')
            return f'Waited {seconds:g} seconds. Inspect the window again.'
        if not self.window or time.monotonic()-self.observed>90:
            raise ValueError('Inspect the target window first; observations expire after 90 seconds or one input.')
        rect=self._validate_window()
        if action=='screenshot':
            if self.native:self._activate()
            rect=self._validate_window()
            bounds=(rect.left,rect.top,rect.right,rect.bottom)
            if bounds!=self.window_bounds:
                raise ValueError('The window moved or resized after inspection. Inspect again before taking a point-click screenshot.')
            folder=self.project/'.talktoai-code/screenshots';folder.mkdir(parents=True,exist_ok=True)
            path=folder/('computer-'+uuid.uuid4().hex+'.png')
            image=self.window.capture_as_image()
            if image is None:
                raise RuntimeError('The window image is unavailable. Restore the target window and inspect again.')
            image.save(path)
            self.pixel_observed=time.monotonic();self.pixel_bounds=bounds
            return json.dumps({'artifact':str(path),'type':'image','window_bounds':list(bounds),
                               'coordinate_system':'window-relative x,y',
                               'note':'Use point coordinates only from this screenshot. It expires after 30 seconds or a window move. Inspect after input; screenshot alone does not prove a task result.'})
        control=self.controls.get(str(target))
        if action not in ('click','fill','select','focus','activate','key','scroll','click_point'):raise ValueError('Unknown computer action.')
        if (action in ('click','fill','select','focus') or (action=='scroll' and target)) and control is None:raise ValueError('Use a control id from the latest inspect result.')
        self.observed=0
        if self.cancel.is_set():raise InterruptedError('Computer control stopped.')
        if action in ('click','fill','select','focus') or (action=='scroll' and control is not None):
            # UI providers can change between observation and delivery. Do not
            # turn a stale target into an input to an inaccessible/replaced field.
            if not control.is_visible() or not control.is_enabled():
                raise ValueError('The observed control is now hidden or disabled. Inspect again before acting.')
            if control.element_info.element.CurrentIsPassword:
                raise ValueError('The observed control is now a password field. Inspect again before acting.')
        if action=='click':
            # Invoke the observed accessibility control directly when supported.
            # This avoids DPI/occlusion problems with physical coordinates.
            from pywinauto.uia_defines import NoPatternInterfaceError
            invoke=getattr(control,'invoke',None)
            if callable(invoke):
                try:invoke()
                except NoPatternInterfaceError:
                    self._click_control(control)
            else:
                self._click_control(control)
        elif action=='fill':control.set_edit_text(value)
        elif action=='select':control.select(value)
        elif action=='focus':control.set_focus()
        elif action=='activate':self._activate()
        elif action=='key':
            keys={'enter':'{ENTER}','escape':'{ESC}','tab':'{TAB}','up':'{UP}','down':'{DOWN}',
                  'left':'{LEFT}','right':'{RIGHT}','home':'{HOME}','end':'{END}',
                  'pageup':'{PGUP}','pagedown':'{PGDN}','shift+tab':'+{TAB}',
                  'ctrl+f':'^f','ctrl+tab':'^{TAB}','ctrl+shift+tab':'^+{TAB}',
                  'alt+left':'%{LEFT}','ctrl+home':'^{HOME}','ctrl+end':'^{END}',
                  'ctrl+s':'^s','ctrl+a':'^a','ctrl+z':'^z','f5':'{F5}'}
            if value.lower() not in keys:raise ValueError('Supported keys: '+', '.join(keys))
            if self.native:
                self._activate()
                self._validate_window()
            self.window.type_keys(keys[value.lower()],set_foreground=True)
        elif action=='scroll':
            # Use the observed UI Automation scroll pattern, never global mouse wheel input.
            try:
                direction,amount,count_text=(part.strip().lower() for part in value.split(','))
                count=int(count_text)
            except (AttributeError,TypeError,ValueError):
                raise ValueError('Scroll value must be direction,amount,count; e.g. down,line,3.') from None
            if direction not in ('up','down','left','right') or amount not in ('line','page') or not 1<=count<=5:
                raise ValueError('Scroll requires up/down/left/right, line/page and count 1 to 5.')
            recipient=control if control is not None else self.window
            for _ in range(count):
                self._validate_window()
                if not recipient.is_visible() or not recipient.is_enabled():
                    raise ValueError('The observed scroll target is now hidden or disabled. Inspect again.')
                if self.cancel.is_set():raise InterruptedError('Computer control stopped.')
                recipient.scroll(direction,amount,count=1,retry_interval=0)
        else:
            if not self.pixel_observed or time.monotonic()-self.pixel_observed>30:
                raise ValueError('Take a fresh screenshot of the inspected window before a point click.')
            rect=self.window.rectangle()
            bounds=(rect.left,rect.top,rect.right,rect.bottom)
            if bounds!=self.pixel_bounds or bounds!=self.window_bounds:
                raise ValueError('The window moved or resized since the screenshot. Inspect and screenshot again.')
            try:
                if not isinstance(value,str):raise ValueError
                parts=value.split(',')
                if len(parts)!=2:raise ValueError
                x,y=(int(part.strip()) for part in parts)
            except (TypeError,ValueError):
                raise ValueError('Point coordinates must be window-relative integer x,y from the latest screenshot.') from None
            if not (0<=x<rect.width() and 0<=y<rect.height()):raise ValueError('Point must be inside the screenshot window.')
            if self.native:
                import win32gui
                if win32gui.GetForegroundWindow()!=self.window_handle:
                    raise ValueError('The target lost focus after the screenshot. Activate, inspect and take a new screenshot.')
                self._assert_point_visible(rect.left+x,rect.top+y)
            if self.cancel.is_set():raise InterruptedError('Computer control stopped.')
            self.window.click_input(coords=(x,y))
        return 'Input delivered. Inspect the window again to verify the result; input delivery alone does not establish success.'


def _worker(pipe, project):
    import threading
    if os.name=='nt':
        import ctypes
        # The worker's geometry and captures must share the same pixel coordinate space.
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    engine=ComputerTools(project,threading.Event())
    try:
        while True:
            args=pipe.recv()
            if args is None:break
            try:pipe.send((True,engine.execute(*args)))
            except Exception as exc:pipe.send((False,f'{type(exc).__name__}: {exc}'))
    except EOFError:pass
    finally:pipe.close()


class ComputerSession:
    """Isolate potentially hung UI Automation providers from the app and Stop."""
    def __init__(self, project, cancel):
        self.project=project
        self.cancel=cancel
        self.pipe=None
        self.process=None

    def _start(self):
        import multiprocessing
        context=multiprocessing.get_context('spawn')
        self.pipe,child=context.Pipe()
        self.process=context.Process(target=_worker,args=(child,str(self.project)),daemon=True)
        try:
            self.process.start()
            if os.name=='nt':
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(self.process.pid)
        except Exception:
            self.close()
            raise
        finally:
            child.close()

    def execute(self, action, target='', value=''):
        if self.cancel.is_set():self.close();raise InterruptedError('Computer control stopped.')
        if not self.process or not self.process.is_alive():
            self.close()
            if action!='windows':raise RuntimeError('The computer worker ended. List windows again before taking any action.')
            self._start()
        deadline=time.monotonic()+_CALL_TIMEOUT
        try:
            self.pipe.send((action,target,value))
            while not self.pipe.poll(.1):
                if self.cancel.is_set():
                    self.close()
                    raise InterruptedError('Computer control stopped. Input may already have occurred.')
                if time.monotonic()>deadline:
                    self.close()
                    raise RuntimeError('Windows accessibility exceeded 20 seconds. List windows and inspect the target again before retrying; input may already have occurred.')
            if self.cancel.is_set():
                self.close()
                raise InterruptedError('Computer control stopped. Input may already have occurred.')
            ok,result=self.pipe.recv()
        except (EOFError, OSError) as exc:
            if isinstance(exc,InterruptedError):raise
            self.close()
            raise RuntimeError('The computer worker exited. List windows and inspect the target again before retrying; input may already have occurred.') from exc
        if not ok:raise RuntimeError(result)
        return result

    def close(self):
        if self.pipe:
            self.pipe.close()
            self.pipe=None
        if self.process and self.process.pid is not None:
            if self.process.is_alive():self.process.terminate()
            self.process.join(timeout=2)
        self.process=None
