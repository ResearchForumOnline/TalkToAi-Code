"""Detect existing project checks without installing dependencies."""
import json
from pathlib import Path


def detect_checks(root):
    root=Path(root)
    if (root/'project.godot').is_file():
        return {'engine':'Godot','commands':[], 'note':'Uses the configured Godot headless import check.'}
    if (root/'ProjectSettings/ProjectVersion.txt').is_file():
        return {'engine':'Unity','commands':[], 'note':'Unity requires the project-specific editor/test command. Inspect its documentation.'}
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
