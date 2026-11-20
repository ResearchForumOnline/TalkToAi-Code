"""Managed and explicitly connected browsers for web research and interaction."""
import json
import os
import shutil
import socket
import subprocess
import uuid
import base64
import re
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
        self.observed_page=None;self.observed_url='';self.observed_snapshot=''
        self.errors=[]
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

    def _tabs(self):
        pages=self._pages()
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
        previous_page=self.page
        previous_pages=list(self.context.pages)
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
        elif action=='click':self._click_observed(target)
        elif action=='fill':
            self._require_observation()
            if not target or target not in self.observed_snapshot:
                raise ValueError('Use an exact field label from the latest browser inspection.')
            field=self.page.get_by_label(target,exact=True).filter(visible=True)
            if field.count()!=1:raise ValueError('The field label does not match exactly one visible control.')
            if field.evaluate("el => el.matches('input[type=password]')"):
                raise ValueError('The password field requires user input.')
            if self.cancel.is_set():raise InterruptedError('Task stopped before browser input.')
            self.observed_page=None
            field.fill(value)
        elif action=='press':
            self._require_observation()
            self.observed_page=None
            self.page.keyboard.press(target)
        elif action=='back':self.page.go_back(wait_until='domcontentloaded',timeout=15000)
        elif action=='reload':self.page.reload(wait_until='domcontentloaded',timeout=15000)
        elif action=='screenshot':
            folder=self.project/'.talktoai-code/screenshots';folder.mkdir(parents=True,exist_ok=True)
            path=folder/('browser-'+uuid.uuid4().hex+'.png')
            self.page.screenshot(path=str(path))
            return json.dumps({'artifact':str(path),'type':'image','url':self.page.url})
        if action in ('click','fill','press'):
            # Follow immediate popups, not arbitrary delayed/background windows.
            # Pump queued page events without waiting for a popup that may not exist.
            if not previous_page.is_closed():previous_page.wait_for_timeout(50)
            opened=[page for page in self._pages() if page not in previous_pages]
            if len(opened)>1:
                self.observed_page=None
                raise ValueError('The action opened multiple browser pages. List the tabs and select the intended page.')
            if opened:self.page=opened[0]
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
        snapshot=self.page.locator('body').aria_snapshot()
        self.observed_page=self.page;self.observed_url=self.page.url;self.observed_snapshot=snapshot[:15000]
        links=self._links()
        return json.dumps({'browser':self.active_browser,'mode':self.mode,'tabs':self._tabs(),'search_engine':self.active_search,'search_attempts':self.search_attempts,'links':links,'url':self.page.url,'title':self.page.title(),'page':snapshot[:15000],'errors':self.errors[-8:]})

    def _configure_page(self,page):
        page.set_default_timeout(8000)
        def error(value):
            self.errors.append(str(value)[:1500]);self.errors[:]=self.errors[-8:]
        page.on('pageerror',error)

    def _click_observed(self,target):
        self._require_observation()
        roles=set()
        for role,quoted in re.findall(r'^\s*- ([a-z]+) ("(?:[^"\\]|\\.)*")',self.observed_snapshot,re.M):
            try:name=json.loads(quoted)
            except ValueError:continue
            if name==target:roles.add(role)
        locator=None
        for role in sorted(roles):
            candidate=self.page.get_by_role(role,name=target,exact=True)
            locator=candidate if locator is None else locator.or_(candidate)
        if locator is None:
            if not target or target not in self.observed_snapshot:
                raise ValueError('Use a control name or text from the latest browser inspection.')
            locator=self.page.get_by_text(target,exact=True)
        locator=locator.filter(visible=True)
        count=locator.count()
        if count!=1:
            raise ValueError(f'Observed browser target matches {count} visible controls; inspect and choose a unique name. No click was sent.')
        if self.cancel.is_set():raise InterruptedError('Task stopped before browser input.')
        self.observed_page=None
        locator.click()

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
        self.tab_ids.clear()
