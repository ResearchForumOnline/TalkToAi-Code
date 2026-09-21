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
            windows=[{'handle':str(w.handle),'title':w.window_text()[:200]} for w in self.desktop.windows() if w.is_visible()][:80]
            self.handles={w['handle'] for w in windows}
            return json.dumps(windows)
        if action=='inspect':
            if str(target) not in self.handles:raise ValueError('List windows first, then inspect a returned handle.')
            self.observed=0;self.controls={}
            self.window=self.desktop.window(handle=int(target)).wrapper_object()
            entries=[]
            for control in self.window.descendants()[:250]:
                try:
                    if not control.is_visible():continue
                    info=control.element_info
                    if info.element.CurrentIsPassword:continue
                    key=str(len(entries));self.controls[key]=control
                    rect=control.rectangle()
                    entries.append({'id':key,'name':control.window_text()[:300],'type':info.control_type,
                                    'bounds':[rect.left,rect.top,rect.right,rect.bottom]})
                except Exception:continue
            self.observed=time.monotonic()
            window_rect=self.window.rectangle()
            return json.dumps({'title':self.window.window_text(),'window_bounds':[window_rect.left,window_rect.top,window_rect.right,window_rect.bottom],'controls':entries,
                               'instruction':'Use a returned control id. Input consumes this snapshot. Inspect again after each action. Stop button cancels further actions.'})
        if action=='wait':
            seconds=float(value or '1')
            if not 0 <= seconds <= 10:raise ValueError('Computer wait must be between 0 and 10 seconds.')
            if self.cancel.wait(seconds):raise InterruptedError('Computer control stopped.')
            return f'Waited {seconds:g} seconds. Inspect the window again.'
        if not self.window or time.monotonic()-self.observed>90:
            raise ValueError('Inspect the target window first; observations expire after 90 seconds or one input.')
        if action=='screenshot':
            folder=self.project/'.talktoai-code/screenshots';folder.mkdir(parents=True,exist_ok=True)
            path=folder/('computer-'+uuid.uuid4().hex+'.png')
            self.window.capture_as_image().save(path)
            return json.dumps({'artifact':str(path),'type':'image','note':'Evidence only; model sees accessibility text, not this image.'})
        control=self.controls.get(str(target))
        if action not in ('click','fill','select','focus','key','click_point'):raise ValueError('Unknown computer action.')
        if action in ('click','fill','select','focus') and control is None:raise ValueError('Use a control id from the latest inspect result.')
        self.observed=0
        if self.cancel.is_set():raise InterruptedError('Computer control stopped.')
        if action=='click':control.click_input()
        elif action=='fill':control.set_edit_text(value)
        elif action=='select':control.select(value)
        elif action=='focus':control.set_focus()
        elif action=='key':
            keys={'enter':'{ENTER}','escape':'{ESC}','tab':'{TAB}','up':'{UP}','down':'{DOWN}',
                  'left':'{LEFT}','right':'{RIGHT}','ctrl+s':'^s','ctrl+a':'^a','ctrl+z':'^z','f5':'{F5}'}
            if value.lower() not in keys:raise ValueError('Supported keys: '+', '.join(keys))
            self.window.type_keys(keys[value.lower()],set_foreground=True)
        else:
            x,y=map(int,value.split(','));rect=self.window.rectangle()
            if not (0<=x<rect.width() and 0<=y<rect.height()):raise ValueError('Point must be inside the inspected window.')
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
