"""Task-owned browser for web research and application testing."""
import json
import uuid
import base64
from pathlib import Path
from urllib.parse import urlsplit, quote_plus, parse_qs

SEARCH_ENGINES = {
    'duckduckgo': 'https://html.duckduckgo.com/html/?q=',
    'bing': 'https://www.bing.com/search?q=',
    'google': 'https://www.google.com/search?q=',
    'brave': 'https://search.brave.com/search?q=',
}


def search_order(preferred):
    order = ('duckduckgo', 'bing', 'google', 'brave')
    if preferred == 'auto':
        return order
    if preferred not in SEARCH_ENGINES:
        raise ValueError('Choose Auto, DuckDuckGo, Bing, Google or Brave for web search.')
    return (preferred,) + tuple(engine for engine in order if engine != preferred)


class BrowserTools:
    def __init__(self, project, cancel, browser_name='auto', search_engine='auto'):
        self.project=Path(project);self.cancel=cancel
        self.browser_name=browser_name;self.search_engine=search_engine
        self.runtime=None;self.browser=None;self.page=None
        self.errors=[]
        self.active_browser='';self.active_search='';self.search_attempts=[]

    def start(self):
        if self.page:return
        from playwright.sync_api import sync_playwright
        self.runtime=sync_playwright().start()
        failures=[]
        options=('edge','chrome','firefox','chromium')
        if self.browser_name not in ('auto',)+options:
            raise ValueError('Choose Auto, Edge, Chrome, Firefox or Chromium for the browser.')
        order=options if self.browser_name=='auto' else (self.browser_name,)+tuple(x for x in options if x!=self.browser_name)
        try:
            for name in order:
                try:
                    if name=='firefox': self.browser=self.runtime.firefox.launch(headless=True)
                    elif name=='chromium': self.browser=self.runtime.chromium.launch(headless=True)
                    else: self.browser=self.runtime.chromium.launch(channel='msedge' if name=='edge' else 'chrome',headless=True)
                    self.active_browser=name
                    break
                except Exception as exc:
                    failures.append(name+': '+str(exc).splitlines()[0][:180])
            if self.browser is None:
                raise RuntimeError('No supported browser could start. Install Edge or Chrome, or a Playwright browser. '+ '; '.join(failures))
            self.page=self.browser.new_page(viewport={'width':1280,'height':800})
            self.page.set_default_timeout(8000)
            self.page.on('pageerror',lambda error:self.errors.append(str(error)[:1500]))
        except Exception:
            self.close();raise

    def execute(self, action, target='', value=''):
        if self.cancel.is_set():raise InterruptedError('Task stopped.')
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
            parsed=urlsplit(target)
            if parsed.scheme not in ('http','https') or parsed.username or parsed.password:
                raise ValueError('Open an http(s) page URL without credentials.')
            self.page.goto(target,wait_until='domcontentloaded',timeout=25000)
        elif action=='click':self.page.get_by_text(target,exact=True).click()
        elif action=='fill':self.page.get_by_label(target,exact=True).fill(value)
        elif action=='press':self.page.keyboard.press(target)
        elif action=='screenshot':
            folder=self.project/'.talktoai-code/screenshots';folder.mkdir(parents=True,exist_ok=True)
            path=folder/('browser-'+uuid.uuid4().hex+'.png')
            self.page.screenshot(path=str(path))
            return json.dumps({'artifact':str(path),'type':'image','url':self.page.url})
        elif action!='inspect':raise ValueError('Use search, open, inspect, click, fill, press or screenshot.')
        snapshot=self.page.locator('body').aria_snapshot()
        links=self._links()
        return json.dumps({'browser':self.active_browser,'search_engine':self.active_search,'search_attempts':self.search_attempts,'links':links,'url':self.page.url,'title':self.page.title(),'page':snapshot[:15000],'errors':self.errors[-8:]})

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
        if self.browser:
            try:self.browser.close()
            except Exception:pass
        if self.runtime:
            try:self.runtime.stop()
            except Exception:pass
        self.runtime=None;self.browser=None;self.page=None
