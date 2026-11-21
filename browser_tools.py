"""Managed tabs with per-observation accessibility references.

The reference workflow draws on Hermes' documented browser snapshots. Page
selection and reading stay scoped to the user-authorized tab, as in BrowserQwen.
"""
import json
import os
import shutil
import socket
import subprocess
import uuid
import base64
import re
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit, quote_plus, parse_qs

SEARCH_ENGINES = {
    'duckduckgo': 'https://html.duckduckgo.com/html/?q=',
    'bing': 'https://www.bing.com/search?q=',
    'google': 'https://www.google.com/search?q=',
    'brave': 'https://search.brave.com/search?q=',
}
BROWSERS = ('edge', 'chrome', 'brave', 'firefox', 'chromium')
BROWSER_MODES = ('headless', 'visible', 'attach')
DEFAULT_CDP_PORT = 9222
CHROMIUM_PROGRAMS = {
    'edge': (('msedge', 'microsoft-edge', 'microsoft-edge-stable'), 'Microsoft/Edge/Application/msedge.exe', 'Microsoft Edge'),
    'chrome': (('chrome', 'google-chrome', 'google-chrome-stable'), 'Google/Chrome/Application/chrome.exe', 'Google Chrome'),
    'brave': (('brave-browser', 'brave-browser-stable', 'brave'), 'BraveSoftware/Brave-Browser/Application/brave.exe', 'Brave Browser'),
    'chromium': (('chromium', 'chromium-browser'), '', 'Chromium'),
}
_ACTIONS = {'search','open','tabs','switch','new_tab','inspect','click','fill','press','back','reload','screenshot'}
_INPUT_ACTIONS = {'click', 'fill', 'press'}
_REF = re.compile(r'(?:f[0-9]+)?e[0-9]+\Z')
_ACCESSIBLE_NODE = re.compile(r'^(?P<indent>\s*)- (?P<role>[a-z][a-z0-9-]*)(?: (?P<name>"(?:[^"\\]|\\.)*"))?(?P<rest>.*)$')
_NODE_PROPERTY = re.compile(r'^\s*\[([^\]\r\n]+)\]')
_MAX_TREE = 9500
_MAX_TEXT = 4500
_TEXT_WALKER = r"""body => {
  const excluded = 'script,style,template,noscript,input,textarea,select,[contenteditable],[hidden],[aria-hidden=true]';
  const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
  const text = [];
  let size = 0;
  let scanned = 0;
  while (scanned++ < 3000 && size < 5000 && walker.nextNode()) {
    const parent = walker.currentNode.parentElement;
    if (!parent || parent.closest(excluded)) continue;
    if (parent.checkVisibility && !parent.checkVisibility({ opacityProperty: true, visibilityProperty: true })) continue;
    if (getComputedStyle(parent).visibility !== 'visible') continue;
    const range = document.createRange();
    range.selectNodeContents(walker.currentNode);
    if (!range.getClientRects().length) continue;
    const line = walker.currentNode.textContent.replace(/\s+/g, ' ').trim().slice(0, 400);
    if (line) { text.push(line); size += line.length + 1; }
  }
  return text.join('\n');
}"""


def _tree_for_model(raw, generation, visible_lines):
    """Expose roles, names and refs, omitting ARIA scalar values and text children."""
    lines, references = [], {}
    used = 0
    for line in raw[:160000].splitlines():
        item = _ACCESSIBLE_NODE.fullmatch(line)
        if not item:
            continue
        properties = []
        rest = item['rest']
        raw_reference = ''
        while prop := _NODE_PROPERTY.match(rest):
            text = prop[1]
            rest = rest[prop.end():]
            if text.startswith('ref=') and _REF.fullmatch(text[4:]):
                raw_reference = text[4:]
            elif re.fullmatch(r'[a-z]+(?:=(?:true|false|mixed|[0-9]+))?', text):
                properties.append(prop[0].strip())
        if not raw_reference:
            continue
        quoted = item['name']
        try:
            name = json.loads(quoted) if quoted else ''
        except ValueError:
            name = ''
        name_is_text = False
        if not name and rest.lstrip().startswith(':'):
            # A generic clickable span may have no accessible name. Use its
            # scalar only if independently present as rendered, non-editable text.
            candidate = rest.lstrip()[1:].strip()
            if candidate.startswith('"'):
                try: candidate = json.loads(candidate)
                except ValueError: candidate = ''
            if isinstance(candidate, str) and candidate in visible_lines:
                name = candidate
                name_is_text = True
        ref = f'{generation}:{raw_reference}'
        record = f"{item['indent']}- {item['role']}"
        if name:
            record += ' ' + json.dumps(name[:250], ensure_ascii=False)
        record += ' ' + ' '.join([f'[ref={ref}]', *properties])
        used += len(record) + 1
        if used > _MAX_TREE:
            break
        lines.append(record)
        references[ref] = (raw_reference, item['role'], name, name_is_text)
    return '\n'.join(lines), references


def find_browser_program(name):
    names, windows, mac = CHROMIUM_PROGRAMS[name]
    for name in names:
        executable = shutil.which(name)
        if executable:
            return executable
    candidates = [Path('/Applications') / f'{mac}.app/Contents/MacOS/{mac}']
    for env in ('LOCALAPPDATA', 'ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)'):
        if windows and os.environ.get(env):
            candidates.append(Path(os.environ[env]) / windows)
    return next((str(path) for path in candidates if path.is_file()), None)


def browser_port(value):
    port_text = str(value).strip()
    if not re.fullmatch(r'[0-9]{1,5}', port_text) or not 1 <= int(port_text) <= 65535:
        raise ValueError('Enter the local browser debugging port (1–65535).')
    return int(port_text)


def launch_connectable_browser(browser, port, state):
    """User-initiated setup of a persistent profile distinct from the default browser."""
    port=browser_port(port)
    if browser=='auto':
        browser=next((name for name in CHROMIUM_PROGRAMS if find_browser_program(name)), 'edge')
    if browser not in CHROMIUM_PROGRAMS:
        raise ValueError('Choose Edge, Chrome, Brave or Chromium to open a connectable browser.')
    executable=find_browser_program(browser)
    if not executable:
        raise ValueError(f'{CHROMIUM_PROGRAMS[browser][2]} was not found. Choose an installed browser.')
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(('127.0.0.1',port))
        except OSError:
            raise ValueError(f'Local port {port} is already in use. Connect to the browser already listening there, or choose a different port before opening a browser.') from None
    profile=Path(state).resolve() / 'browser-profiles' / browser
    profile.mkdir(parents=True,exist_ok=True)
    subprocess.Popen([executable, f'--user-data-dir={profile}', '--remote-debugging-address=127.0.0.1',
                      f'--remote-debugging-port={port}', '--no-first-run', '--no-default-browser-check',
                      'about:blank'],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return CHROMIUM_PROGRAMS[browser][2]


def web_url(target):
    parsed = urlsplit(target)
    return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and parsed.username is None and parsed.password is None


def search_order(preferred):
    order = ('duckduckgo', 'bing', 'google', 'brave')
    if preferred == 'auto':
        return order
    if preferred not in SEARCH_ENGINES:
        raise ValueError('Choose Auto, DuckDuckGo, Bing, Google or Brave for web search.')
    return (preferred,) + tuple(engine for engine in order if engine != preferred)


class BrowserTools:
    def __init__(self, project, cancel, browser_name='auto', search_engine='auto', mode='headless', cdp_port=DEFAULT_CDP_PORT, strict_browser=False):
        self.project=Path(project);self.cancel=cancel
        self.browser_name=browser_name;self.search_engine=search_engine
        if mode not in BROWSER_MODES:
            raise ValueError('Choose Headless, Visible or Connect for browser control.')
        self.mode=mode
        self.strict_browser=strict_browser
        self.cdp_port=browser_port(cdp_port) if mode=='attach' else DEFAULT_CDP_PORT
        self.runtime=None;self.browser=None;self.page=None;self.context=None
        self.observed_page=None;self.observed_url='';self.observed_refs={}
        self.page_generations={};self.page_errors={}
        self.active_browser='';self.active_search='';self.search_attempts=[]
        self.tab_ids={}
        self.tab_count=0

    def _pages(self):
        return [page for page in self.context.pages if not page.is_closed()
                and (page.url=='about:blank' or web_url(page.url))]

    def start(self):
        if self.context and self.browser.is_connected():return
        if self.context:self.close()
        if self.browser_name not in ('auto',)+BROWSERS:
            raise ValueError('Choose Auto, Edge, Chrome, Brave, Firefox or Chromium for the browser.')
        from playwright.sync_api import sync_playwright
        self.runtime=sync_playwright().start()
        try:
            if self.mode=='attach':
                self._attach()
            else:
                self._launch()
            self.context.on('page',self._configure_page)
            for page in self.context.pages:self._configure_page(page)
        except Exception:
            self.close();raise

    def _attach(self):
        endpoint=f'http://127.0.0.1:{self.cdp_port}'
        try:
            self.browser=self.runtime.chromium.connect_over_cdp(endpoint, timeout=5000, no_defaults=True)
        except Exception as exc:
            raise RuntimeError(f'Could not connect to a Chromium browser at {endpoint}. Start Chrome, Brave or Edge with debugging enabled for a separate browser profile, or choose Visible browser in Settings.') from exc
        if not self.browser.contexts:
            raise RuntimeError('The connected browser does not have an accessible profile.')
        self.context=self.browser.contexts[0]
        self.active_browser='Connected Chromium browser'

    def _launch(self):
        failures=[]
        headless = self.mode=='headless'
        order=BROWSERS if self.browser_name=='auto' else (self.browser_name,)
        if self.browser_name!='auto' and not self.strict_browser:
            order+=tuple(name for name in BROWSERS if name!=self.browser_name)
        for name in order:
            if self.cancel.is_set():raise InterruptedError('Task stopped.')
            try:
                if name=='firefox':
                    self.browser=self.runtime.firefox.launch(headless=headless,timeout=10000)
                elif name=='brave':
                    executable=find_browser_program('brave')
                    if not executable:raise RuntimeError('Brave executable was not found.')
                    self.browser=self.runtime.chromium.launch(executable_path=executable,headless=headless,timeout=10000)
                else:
                    channel={'edge':'msedge','chrome':'chrome'}.get(name)
                    self.browser=self.runtime.chromium.launch(channel=channel,headless=headless,timeout=10000)
                self.active_browser=name
                break
            except Exception as exc:
                failures.append(name+': '+str(exc).splitlines()[0][:180])
        if self.browser is None:
            hint='Check the requested browser is installed.' if self.strict_browser else 'Install Edge, Chrome, Brave or a Playwright browser.'
            raise RuntimeError('The browser could not start. '+hint+' '+ '; '.join(failures))
        self.context=self.browser.new_context(viewport={'width':1280,'height':800})

    def _tabs(self, current_only=False):
        pages=[self.page] if current_only and self.page else ([] if current_only else self._pages())
        self.tab_ids={page:tab_id for page,tab_id in self.tab_ids.items() if not page.is_closed()}
        tabs=[]
        for page in pages:
            if page not in self.tab_ids:
                self.tab_count+=1
                self.tab_ids[page]=f'tab-{self.tab_count}'
            tabs.append({'id':self.tab_ids[page],'url':page.url,'current':page is self.page})
        return tabs

    def _require_observation(self):
        if self.observed_page is not self.page or self.observed_url!=self.page.url:
            raise ValueError('Inspect the current browser page before interacting with it.')
        if self.cancel.is_set():raise InterruptedError('Task stopped before browser input.')

    def _read_visible_text(self, page):
        visible = []
        used = 0
        for frame in page.frames[:8]:
            if self.cancel.is_set(): raise InterruptedError('Task stopped.')
            if frame.url not in ('about:blank', 'about:srcdoc') and not web_url(frame.url):
                continue
            try:
                if frame is not page.main_frame:
                    parent = frame.frame_element()
                    try:
                        if not parent.is_visible():
                            continue
                    finally:
                        parent.dispose()
                content = frame.locator('body').evaluate(_TEXT_WALKER, timeout=600)
            except Exception as exc:
                if not type(exc).__module__.startswith('playwright.'):raise
                continue
            visible.append(content)
            used += len(content)
            if used >= _MAX_TEXT:
                break
        return '\n'.join(visible)[:_MAX_TEXT]

    def _observe(self):
        page = self.page
        self.observed_page = None
        for _ in range(2):
            if self.cancel.is_set():raise InterruptedError('Task stopped.')
            url = page.url
            if url!='about:blank' and not web_url(url):
                break
            epoch = self.page_generations.get(page, 0)
            raw = page.aria_snapshot(mode='ai', timeout=4000)
            visible = self._read_visible_text(page)
            links = self._links()
            title = page.title()
            if page.is_closed():
                break
            if url==page.url and self.page_generations.get(page, 0)==epoch:
                snapshot, references = _tree_for_model(raw, 's'+uuid.uuid4().hex[:8], set(visible.splitlines()))
                return self._record_observation(page,url,title,snapshot,references,visible,links)
        raise ValueError('Page contents changed while they were being inspected. Inspect the current page again.')

    def _record_observation(self,page,url,title,snapshot,references,visible,links):
        self.observed_page=page;self.observed_url=url;self.observed_refs=references
        if visible:
            snapshot += '\n\nVisible page text\n' + visible
        errors=list(self.page_errors.get(page, ()))
        return json.dumps({
            'browser':self.active_browser,'mode':self.mode,'url':url,'title':title,
            'page':snapshot,'target_hint':'Use a [ref=...] ID from this observation or a unique exact control name. Re-inspect after changes.',
            'tabs':self._tabs(current_only=self.mode=='attach'),
            'search_engine':self.active_search,'search_attempts':self.search_attempts,
            'links':links,'errors':errors,
        })

    def execute(self, action, target='', value=''):
        try:
            return self._execute(action,target,value)
        except ImportError as exc:
            if exc.name and exc.name.split('.')[0]=='playwright':
                raise RuntimeError('Playwright could not load. Use the current TalkToAi Code build or reinstall its runtime dependencies.') from exc
            raise
        except Exception as exc:
            if type(exc).__module__.startswith('playwright.'):
                raise RuntimeError('Browser action failed: '+str(exc).splitlines()[0]) from exc
            raise

    def _execute(self, action, target, value):
        if self.cancel.is_set():raise InterruptedError('Task stopped.')
        if action not in _ACTIONS:
            raise ValueError('Use search, open, tabs, switch, new_tab, inspect, click, fill, press, back, reload or screenshot.')
        if (action=='open' or (action=='new_tab' and target)) and not web_url(target):
            raise ValueError('Enter an http(s) page URL without credentials.')
        preferred=str(value or self.search_engine).strip().lower() if action=='search' else ''
        if action=='search' and not str(target).strip():raise ValueError('Enter a web search query.')
        self.search_attempts=[] if action=='search' else self.search_attempts
        if action=='search' and preferred=='serper':
            from search_provider import search
            try:
                links=search(str(target).strip()[:1000])
                self.active_search='serper'
                return json.dumps({'browser':'Serper API','search_engine':'serper','search_attempts':[],
                    'links':links,'url':'https://google.serper.dev/search','title':'Serper results',
                    'page':'\n'.join(item['title']+' — '+item['snippet'] for item in links)[:15000],'errors':[]})
            except Exception as exc:
                self.search_attempts.append('serper: '+str(exc).splitlines()[0][:200])
                preferred='auto'
        self.start()
        if action=='tabs':
            return json.dumps({'browser':self.active_browser,'mode':self.mode,'tabs':self._tabs()})
        if action=='switch':
            selected=next((page for page in self._pages() if self.tab_ids.get(page)==target),None)
            if selected is None:raise ValueError('List the browser tabs and choose a returned tab ID.')
            self.page=selected
            self.observed_page=None
            if self.mode!='headless':self.page.bring_to_front()
        elif action=='new_tab':
            self.page=self.context.new_page()
            self.observed_page=None
            if target:self.page.goto(target,wait_until='domcontentloaded',timeout=25000)
        elif self.page is None or self.page.is_closed():
            if self.mode=='attach':
                if action not in ('open','search'):
                    raise ValueError('List the connected browser tabs and select a tab before inspecting or interacting. Open creates a new tab when none is selected.')
                self.page=self.context.new_page()
            else:
                pages=self._pages()
                self.page=pages[0] if pages else self.context.new_page()
            self.observed_page=None
        if self.page.url!='about:blank' and not web_url(self.page.url):
            raise ValueError('Select an HTTP or HTTPS tab before using browser tools.')
        if action in ('search','open','back','reload'):
            self.observed_page=None
        if action=='search':
            query=str(target).strip()[:1000]
            if not query:raise ValueError('Enter a web search query.')
            self.active_search=''
            for engine in search_order(preferred):
                if self.cancel.is_set():raise InterruptedError('Task stopped.')
                try:
                    self.page.goto(SEARCH_ENGINES[engine]+quote_plus(query),wait_until='domcontentloaded',timeout=15000)
                    links=self._links()
                    external=[link for link in links if not any(host in (urlsplit(link['url']).hostname or '') for host in ('duckduckgo.com','bing.com','google.com','brave.com'))]
                    if external:
                        self.active_search=engine
                        break
                    self.search_attempts.append(engine+': no usable source links')
                except Exception as exc:
                    self.search_attempts.append(engine+': '+str(exc).splitlines()[0][:200])
            if not self.active_search:
                raise RuntimeError('Web search returned no usable source links. '+ '; '.join(self.search_attempts))
        elif action=='open':
            self.page.goto(target,wait_until='domcontentloaded',timeout=25000)
        elif action in _INPUT_ACTIONS:self._input(action,target,value)
        elif action=='back':self.page.go_back(wait_until='domcontentloaded',timeout=15000)
        elif action=='reload':self.page.reload(wait_until='domcontentloaded',timeout=15000)
        elif action=='screenshot':
            folder=self.project/'.talktoai-code/screenshots';folder.mkdir(parents=True,exist_ok=True)
            path=folder/('browser-'+uuid.uuid4().hex+'.png')
            self.page.screenshot(path=str(path))
            return json.dumps({'artifact':str(path),'type':'image','url':self.page.url})
        if self.cancel.is_set():raise InterruptedError('Task stopped.')
        if self.page.is_closed():
            self.observed_page=None
            if self.mode=='attach':
                self.page=None
                return json.dumps({'browser':self.active_browser,'mode':self.mode,'page_closed':True,'tabs':self._tabs()})
            pages=self._pages()
            self.page=pages[0] if pages else self.context.new_page()
        if self.page.url!='about:blank' and not web_url(self.page.url):
            self.observed_page=None
            raise ValueError('The browser navigated to a non-web page. List the tabs and select an HTTP or HTTPS page.')
        self.page.wait_for_load_state('domcontentloaded',timeout=8000)
        return self._observe()

    def _configure_page(self,page):
        if page in self.page_generations:return
        self.page_generations[page]=0
        self.page_errors[page]=deque(maxlen=4)
        page.set_default_timeout(8000)
        # Keep diagnostics scoped to this tab without serializing exception
        # payloads, which can contain form values or private page data.
        page.on('pageerror',lambda _error:self.page_errors.get(page, deque()).append('Page script reported an error.'))
        def changed(_frame):
            if page in self.page_generations:
                self.page_generations[page]+=1
            if self.observed_page is page:
                self.observed_page=None
        page.on('framenavigated',changed)
        page.on('frameattached',changed)
        page.on('framedetached',changed)
        def closed():
            changed(None)
            self.page_generations.pop(page,None);self.page_errors.pop(page,None)
        page.on('close',lambda _page:closed())

    def _target(self, target):
        target=str(target).strip()
        if target.startswith('[ref=') and target.endswith(']'):
            target=target[5:-1]
        record = self.observed_refs.get(target)
        if not record:
            matches=[entry for entry in self.observed_refs.values() if target and entry[2]==target]
            if len(matches)>1:
                raise ValueError(f'Observed name matches {len(matches)} visible controls. Use the intended control’s ref ID.')
            if not matches:
                raise ValueError('Use a ref ID or exact control name from the latest browser inspection.')
            record=matches[0]
        raw_ref, role, name, name_is_text = record
        # Playwright 1.63 exposes this selector engine. Newer versions expose
        # the same frame-scoped reference through the public get_by_ref API.
        get_by_ref=getattr(self.page,'get_by_ref',None)
        locator=get_by_ref(raw_ref) if get_by_ref else self.page.locator('aria-ref='+raw_ref)
        if name:
            match=(self.page.get_by_text(name,exact=True) if name_is_text else
                   self.page.get_by_role(role,name=name,exact=True))
            locator=locator.and_(match)
        locator=locator.filter(visible=True)
        if locator.count()!=1:
            raise ValueError('The observed browser control moved, changed or was replaced. Inspect again before acting.')
        return locator.element_handle(timeout=600)

    def _input(self,action,target,value):
        self._require_observation()
        page=self.page
        element=self._target(target) if action!='press' else None
        opened=[]
        try:
            if action=='fill' and element.evaluate(r"""el => el.matches('input[type=password]') ||
                    /(?:^|\s)(?:current-password|new-password|one-time-code)(?:\s|$)/i.test(el.autocomplete || '')"""):
                raise ValueError('The password field or verification-code field requires user input.')
            self._require_observation()
            self.observed_page=None
            def on_popup(popup):
                opened.append(popup)
            page.on('popup',on_popup)
            try:
                if action=='press':page.keyboard.press(target)
                elif action=='fill':element.fill(value,timeout=2500)
                else:element.click(timeout=2500)
                if not page.is_closed() and not self.cancel.is_set():
                    page.wait_for_timeout(60)
            finally:
                page.remove_listener('popup',on_popup)
        finally:
            if element:
                try:element.dispose()
                except Exception as exc:
                    if not type(exc).__module__.startswith('playwright.'):raise
        live_popups=[popup for popup in opened if not popup.is_closed()
                     and (popup.url=='about:blank' or web_url(popup.url))]
        if len(live_popups)>1:
            raise ValueError('The action opened multiple browser pages. List the tabs and select the intended page.')
        if live_popups:
            self.page=live_popups[0]

    def _links(self):
        links=self.page.locator('a[href]').evaluate_all("nodes => nodes.filter(a => (a.textContent || '').trim() && /^https?:/.test(a.href)).slice(0,100).map(a => ({title:(a.textContent || '').trim().slice(0,200),url:a.href}))")
        for link in links:
            parsed=urlsplit(link['url'])
            if parsed.hostname in ('duckduckgo.com','www.duckduckgo.com') and parsed.path.startswith('/l/'):
                original=parse_qs(parsed.query).get('uddg',[''])[0]
                if urlsplit(original).scheme in ('http','https'):
                    link['url']=original
            elif parsed.hostname in ('google.com','www.google.com') and parsed.path=='/url':
                original=parse_qs(parsed.query).get('q',[''])[0]
                if urlsplit(original).scheme in ('http','https'):
                    link['url']=original
            elif parsed.hostname in ('bing.com','www.bing.com') and parsed.path.startswith('/ck/a'):
                encoded=parse_qs(parsed.query).get('u',[''])[0]
                if encoded.startswith('a1'):
                    try:
                        original=base64.urlsafe_b64decode(encoded[2:]+'='*(-len(encoded[2:])%4)).decode('utf-8')
                        if urlsplit(original).scheme in ('http','https'):
                            link['url']=original
                    except (ValueError,UnicodeError):
                        pass
        return links

    def close(self):
        if self.browser and self.mode!='attach':
            try:self.browser.close()
            except Exception:pass
        if self.runtime:
            try:self.runtime.stop()
            except Exception:pass
        self.runtime=None;self.browser=None;self.page=None;self.context=None;self.observed_page=None
        self.observed_refs.clear();self.page_generations.clear();self.page_errors.clear()
        self.tab_ids.clear()
