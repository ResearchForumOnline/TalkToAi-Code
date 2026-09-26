"""Task-owned Edge browser for web research and application testing."""
import json
import uuid
from pathlib import Path
from urllib.parse import urlsplit, quote_plus


class BrowserTools:
    def __init__(self, project, cancel):
        self.project=Path(project);self.cancel=cancel
        self.runtime=None;self.browser=None;self.page=None
        self.errors=[]

    def start(self):
        if self.page:return
        from playwright.sync_api import sync_playwright
        self.runtime=sync_playwright().start()
        try:
            self.browser=self.runtime.chromium.launch(channel='msedge',headless=True)
            self.page=self.browser.new_page(viewport={'width':1280,'height':800})
            self.page.set_default_timeout(8000)
            self.page.on('pageerror',lambda error:self.errors.append(str(error)[:1500]))
        except Exception:
            self.close();raise

    def execute(self, action, target='', value=''):
        if self.cancel.is_set():raise InterruptedError('Task stopped.')
        self.start()
        if action=='search':
            query=str(target).strip()[:1000]
            if not query:raise ValueError('Enter a web search query.')
            self.page.goto('https://www.bing.com/search?q='+quote_plus(query),wait_until='domcontentloaded',timeout=25000)
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
        links=self.page.locator('a[href]').evaluate_all("nodes => nodes.filter(a => a.innerText.trim() && /^https?:/.test(a.href)).slice(0,80).map(a => ({title:a.innerText.trim().slice(0,200),url:a.href}))")
        return json.dumps({'links':links,'url':self.page.url,'title':self.page.title(),'page':snapshot[:15000],'errors':self.errors[-8:]})

    def close(self):
        if self.browser:
            try:self.browser.close()
            except Exception:pass
        if self.runtime:
            try:self.runtime.stop()
            except Exception:pass
        self.runtime=None;self.browser=None;self.page=None
