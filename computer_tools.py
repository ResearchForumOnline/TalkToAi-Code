"""Original Windows accessibility integration using pywinauto.

Each input consumes a recent observation; inspect again afterwards.
"""
import json
import time
import uuid
from pathlib import Path


class ComputerTools:
    def __init__(self, project, cancel, desktop=None):
        self.project=Path(project);self.cancel=cancel;self.desktop=desktop
        self.window=None;self.controls={};self.observed=0;self.handles=set()
        self.window_bounds=None;self.pixel_observed=0;self.pixel_bounds=None
        self.window_handle=None;self.window_identity=None

    def _validate_window(self):
        if self.cancel.is_set():raise InterruptedError('Computer control stopped.')
        if (not self.window or not self.window.is_visible() or not self.window.is_enabled()
                or self.window.handle != self.window_handle):
            raise ValueError('The observed window is now hidden, disabled or replaced. List windows and inspect again.')
        identity=getattr(self.window.element_info, 'runtime_id', None)
        if self.window_identity is not None and (tuple(identity) if isinstance(identity,(tuple,list)) else identity) != self.window_identity:
            raise ValueError('The observed window was replaced. List windows and inspect again.')
        rect=self.window.rectangle()
        if (rect.left,rect.top,rect.right,rect.bottom)!=self.window_bounds:
            raise ValueError('The window moved or resized after inspection. Inspect again before acting.')
        return rect

    def connect(self):
        if self.desktop is None:
            import comtypes
            comtypes.CoInitialize()
            from pywinauto import Desktop
            self.desktop=Desktop(backend='uia')

    def execute(self, action, target='', value=''):
        if self.cancel.is_set():raise InterruptedError('Computer control stopped.')
        self.connect()
        if action=='windows':
            self.window=None;self.controls={};self.observed=0
            self.window_bounds=None;self.pixel_observed=0;self.pixel_bounds=None
            windows=[{'handle':str(w.handle),'title':w.window_text()[:200]} for w in self.desktop.windows() if w.is_visible()][:80]
            self.handles={w['handle'] for w in windows}
            return json.dumps(windows)
        if action=='inspect':
            if str(target) not in self.handles:raise ValueError('List windows first, then inspect a returned handle.')
            self.observed=0;self.controls={}
            self.window_bounds=None;self.pixel_observed=0;self.pixel_bounds=None
            self.window=self.desktop.window(handle=int(target)).wrapper_object()
            if not self.window.is_visible() or not self.window.is_enabled():
                self.window=None
                raise ValueError('The listed window is now hidden or disabled. List windows again.')
            self.window_handle=self.window.handle
            identity=getattr(self.window.element_info, 'runtime_id', None)
            self.window_identity=tuple(identity) if isinstance(identity,(tuple,list)) else None
            entries=[];snapshot=uuid.uuid4().hex[:12]
            for control in self.window.descendants()[:250]:
                try:
                    if not control.is_visible():continue
                    info=control.element_info
                    if info.element.CurrentIsPassword:continue
                    key=f'{snapshot}:{len(entries)}'
                    rect=control.rectangle()
                    entries.append({'id':key,'name':control.window_text()[:300],'type':info.control_type,
                                    'enabled':bool(control.is_enabled()),
                                    'bounds':[rect.left,rect.top,rect.right,rect.bottom]})
                    self.controls[key]=control
                except Exception:continue
            self.observed=time.monotonic()
            window_rect=self.window.rectangle()
            self.window_bounds=(window_rect.left,window_rect.top,window_rect.right,window_rect.bottom)
            return json.dumps({'title':self.window.window_text(),'window_bounds':[window_rect.left,window_rect.top,window_rect.right,window_rect.bottom],'controls':entries,
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
            rect=self.window.rectangle()
            bounds=(rect.left,rect.top,rect.right,rect.bottom)
            if bounds!=self.window_bounds:
                raise ValueError('The window moved or resized after inspection. Inspect again before taking a point-click screenshot.')
            folder=self.project/'.talktoai-code/screenshots';folder.mkdir(parents=True,exist_ok=True)
            path=folder/('computer-'+uuid.uuid4().hex+'.png')
            self.window.capture_as_image().save(path)
            self.pixel_observed=time.monotonic();self.pixel_bounds=bounds
            return json.dumps({'artifact':str(path),'type':'image','window_bounds':list(bounds),
                               'coordinate_system':'window-relative x,y',
                               'note':'Use point coordinates only from this screenshot. It expires after 30 seconds or a window move. Inspect after input; screenshot alone does not prove a task result.'})
        control=self.controls.get(str(target))
        if action not in ('click','fill','select','focus','key','scroll','click_point'):raise ValueError('Unknown computer action.')
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
                    self.window.set_focus();control.click_input()
            else:
                self.window.set_focus();control.click_input()
        elif action=='fill':control.set_edit_text(value)
        elif action=='select':control.select(value)
        elif action=='focus':control.set_focus()
        elif action=='key':
            keys={'enter':'{ENTER}','escape':'{ESC}','tab':'{TAB}','up':'{UP}','down':'{DOWN}',
                  'left':'{LEFT}','right':'{RIGHT}','home':'{HOME}','end':'{END}',
                  'pageup':'{PGUP}','pagedown':'{PGDN}','shift+tab':'+{TAB}',
                  'ctrl+f':'^f','ctrl+tab':'^{TAB}','ctrl+shift+tab':'^+{TAB}',
                  'alt+left':'%{LEFT}','ctrl+home':'^{HOME}','ctrl+end':'^{END}',
                  'ctrl+s':'^s','ctrl+a':'^a','ctrl+z':'^z','f5':'{F5}'}
            if value.lower() not in keys:raise ValueError('Supported keys: '+', '.join(keys))
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
            self.window.click_input(coords=(x,y))
        return 'Input delivered. Inspect the window again to verify the result; input delivery alone does not establish success.'


def _worker(pipe, project):
    import threading
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
        import multiprocessing
        context=multiprocessing.get_context('spawn')
        self.cancel=cancel;self.pipe,child=context.Pipe()
        self.process=context.Process(target=_worker,args=(child,str(project)),daemon=True)
        self.process.start();child.close()

    def execute(self, action, target='', value=''):
        if not self.process.is_alive():raise RuntimeError('Computer session ended. Start a new task turn.')
        if self.cancel.is_set():self.close();raise InterruptedError('Computer control stopped.')
        self.pipe.send((action,target,value));deadline=time.monotonic()+20
        while not self.pipe.poll(.1):
            if self.cancel.is_set() or time.monotonic()>deadline or not self.process.is_alive():
                self.close()
                raise InterruptedError('Computer control stopped, failed or exceeded 20 seconds. Inspect again before retrying; input may already have occurred.')
        ok,result=self.pipe.recv()
        if not ok:raise RuntimeError(result)
        return result

    def close(self):
        if self.process.is_alive():self.process.terminate()
        self.process.join(timeout=2);self.pipe.close()
