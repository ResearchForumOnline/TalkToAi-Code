"""Detect existing project checks without installing dependencies."""
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree


class _PageStructure(HTMLParser):
    """Check document structure without loading scripts, images or remote URLs."""
    CDATA_CONTENT_ELEMENTS = (*HTMLParser.CDATA_CONTENT_ELEMENTS, 'title', 'textarea')
    def __init__(self):
        super().__init__()
        self.elements = set()
        self.closed = set()
        self.text = False

    def handle_starttag(self, tag, attrs):
        if any(re.search(r"""["'<]""", name) for name, _ in attrs):
            raise self.tag_error(tag)
        self.elements.add(tag)

    def tag_error(self, tag):
        line, column = self.getpos()
        return ValueError(f'Malformed <{tag}> opening tag at line {line}, column {column + 1}. '
                          'Check that every quoted attribute value and the opening tag are closed.')

    def handle_endtag(self, tag):
        self.closed.add(tag)

    def handle_data(self, data):
        if data.strip():
            self.text = True

    def verify(self, document):
        self.feed(document)
        # close() recovers an unfinished opening tag as ordinary text. Check
        # the parser's buffered token first, while its source position is known.
        unfinished = re.match(r'<([a-zA-Z][^\s/>]*)', self.rawdata)
        if unfinished and not self.cdata_elem:
            raise self.tag_error(unfinished[1][:80])
        self.close()
        if not self.elements - {'html', 'head', 'body', 'title', 'meta', 'link'}:
            if not ('body' in self.elements and self.text):
                raise ValueError('The page has no document content.')
        if self.rawdata or (self.elements & {'script', 'style'}) - self.closed:
            raise ValueError('A script, style block or HTML tag is incomplete.')


def check_edited_files(paths):
    """Fallback for a small standalone deliverable with no project test command."""
    paths = list(dict.fromkeys(paths))
    supported = {'.html', '.htm', '.py', '.svg', '.json'}
    if not paths or len(paths) > 16 or any(path.suffix.lower() not in supported for path in paths):
        return None
    results = []
    for path in paths:
        try:
            with path.open('rb') as stream:
                data = stream.read(500_001)
            if not data or len(data) > 500_000:
                raise ValueError('Expected a nonempty file of at most 500 KB.')
            suffix = path.suffix.lower()
            if suffix == '.py':
                compile(data, path.name, 'exec', dont_inherit=True)
                results.append(f'{path.name}: Python syntax checked. The program was not executed.')
            elif suffix == '.svg':
                root = ElementTree.fromstring(data)
                if root.tag not in ('svg', '{http://www.w3.org/2000/svg}svg'):
                    raise ValueError('Expected an SVG document element.')
                results.append(f'{path.name}: SVG XML and root element checked. Rendering was not run.')
            elif suffix == '.json':
                json.loads(data)
                results.append(f'{path.name}: JSON parsed successfully.')
            else:
                _PageStructure().verify(data.decode('utf-8-sig'))
                results.append(f'{path.name}: HTML structure checked. Browser rendering and scripts were not run.')
        except (OSError, UnicodeError, ValueError, SyntaxError, RecursionError, ElementTree.ParseError) as exc:
            return f'Exit 1\nStandalone file check failed for {path.name}: {exc}'
    return 'Exit 0\nStandalone file checks\n' + '\n'.join(results)


def detect_checks(root):
    root=Path(root)
    if (root/'project.godot').is_file():
        return {'engine':'Godot','commands':[], 'note':'Uses the configured Godot headless import check.'}
    if (root/'ProjectSettings/ProjectVersion.txt').is_file():
        return {'engine':'Unity','commands':[], 'note':'Uses the project’s Unity Editor for a batch import and compile check.'}
    if (root/'package.json').is_file():
        data=json.loads((root/'package.json').read_text(encoding='utf-8-sig'))
        scripts=data.get('scripts',{})
        commands=[]
        # Respect the package manager already selected by the project.
        manager='pnpm' if (root/'pnpm-lock.yaml').exists() else 'yarn' if (root/'yarn.lock').exists() else 'npm'
        for name in ('lint','typecheck','test','build'):
            script=scripts.get(name,'')
            if not isinstance(script,str) or not script.strip() or 'no test specified' in script.lower():continue
            if '--watch' in script:continue
            suffix=' -- --run' if manager=='npm' and name=='test' and 'vitest' in script and 'run' not in script.split() else ''
            commands.append(f'{manager} run {name}{suffix}')
        return {'engine':'JavaScript / TypeScript','commands':commands,'note':'Runs existing package scripts with CI=true; dependencies are not installed automatically.'}
    if (root/'Cargo.toml').is_file():
        return {'engine':'Rust','commands':['cargo test --offline'],'note':'Uses cached dependencies only.'}
    if list(root.glob('*.sln')) or list(root.glob('*.csproj')):
        return {'engine':'.NET','commands':['dotnet test --no-restore'],'note':'Uses already restored dependencies.'}
    config=''
    for name in ('pyproject.toml','pytest.ini','setup.cfg'):
        path=root/name
        if path.is_file() and path.stat().st_size<250000:config+='\n'+path.read_text(encoding='utf-8',errors='replace')
    if 'pytest' in config.lower() or (root/'conftest.py').exists() or (root/'tests/conftest.py').exists():
        return {'engine':'Python / pytest','commands':['python -m pytest -q'],'note':'Uses the installed pytest runner.'}
    if list(root.glob('test*.py')) or (root/'tests').is_dir():
        folder='tests' if (root/'tests').is_dir() else '.'
        return {'engine':'Python / unittest','commands':[f'python -m unittest discover -s {folder} -v'],'note':'Zero discovered tests are reported as unverified.'}
    return {'engine':'General','commands':[],'note':'No supported checks detected; inspect project documentation for the correct command.'}
