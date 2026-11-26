"""Check and explain a model-written Python scene script before any engine runs it.

Everything here is deterministic: no model, network or process is used. The
script is parsed, never imported or executed. Repairs are limited to changes
that make an unparseable reply parse; a repair is kept only when the result
compiles, and each one is reported.
"""
import ast
import re
import textwrap

from file_draft import strip_fences


_QUOTES = str.maketrans({'\u201c': '"', '\u201d': '"', '\u2018': "'", '\u2019': "'", '\u00a0': ' ', '\u200b': ''})
# The app saves and reopens the scene itself. These calls in a scene script only
# add minutes (a render), overwrite the result, or end Blender before the check.
_APP_OWNED = {
    ('bpy', 'ops', 'render', 'render'): 'the app does not render while building a scene',
    ('bpy', 'ops', 'render', 'opengl'): 'the app does not render while building a scene',
    ('bpy', 'ops', 'wm', 'save_as_mainfile'): 'the app saves the scene itself',
    ('bpy', 'ops', 'wm', 'save_mainfile'): 'the app saves the scene itself',
    ('bpy', 'ops', 'wm', 'quit_blender'): 'the app closes Blender after checking the scene',
    ('bpy', 'ops', 'wm', 'read_factory_settings'): 'the app already starts from an empty scene',
    ('bpy', 'ops', 'wm', 'read_homefile'): 'the app already starts from an empty scene',
    ('bpy', 'ops', 'wm', 'open_mainfile'): 'the app already starts from an empty scene',
}
_EXIT_CALLS = {('sys', 'exit'), ('exit',), ('quit',), ('os', '_exit')}
_OUT_OF_SCOPE = {'subprocess', 'socket', 'urllib', 'requests', 'http', 'ftplib', 'smtplib', 'shutil', 'ctypes',
                 'webbrowser', 'winreg', 'multiprocessing'}


def describe_syntax_error(error, source, *, name='the script'):
    """Line, column, the offending line and a caret; no angle-bracket filenames."""
    lines = source.splitlines()
    number = error.lineno or 0
    column = error.offset or 0
    message = (error.msg or 'invalid syntax').strip()
    text = f'Python syntax error in {name}, line {number}'
    if column:
        text += f', column {column}'
    text += f': {message}.'
    if 1 <= number <= len(lines):
        line = lines[number - 1].rstrip()
        shown = line if len(line) <= 200 else line[:200] + ' …'
        text += f'\n    {shown}'
        if 0 < column <= len(shown) + 1:
            text += '\n    ' + ' ' * (column - 1) + '^'
    if number and number >= len(lines) and re.search(r'never closed|unexpected EOF|expected an indented block|'
                                                     r'unterminated|incomplete input', message, re.I):
        text += '\nThe file appears to stop before it is finished.'
    return text


def stops_early(error, source):
    """True when a syntax error is the ordinary signature of a cut-off file."""
    lines = source.rstrip().splitlines()
    message = (error.msg or '')
    at_end = (error.lineno or 0) >= len(lines)
    return bool(re.search(r'never closed|unexpected EOF|incomplete input', message, re.I)
                or (at_end and re.search(r'expected an indented block|unterminated|invalid syntax|expected', message, re.I)))


def _compiles(source):
    try:
        compile(source, 'scene.py', 'exec', dont_inherit=True)
    except (SyntaxError, ValueError):  # ValueError: source contains null bytes
        return False
    return True


def _drop_prose(source):
    """Remove sentences a model wrapped around its code, when that is all that is wrong."""
    lines = source.split('\n')
    code = re.compile(r'^\s*(?:import |from \w|def |class |#|@|"""|\'\'\'|[A-Za-z_][\w.\[\]\'" ]*\s*[=(+\-*/%]|'
                      r'for |while |if |try:|with |return\b|print\(|bpy\.|\)|\]|\}|$)')
    start = 0
    while start < len(lines) and not code.match(lines[start]):
        start += 1
    end = len(lines)
    while end > start and lines[end - 1].strip() and not code.match(lines[end - 1]) and not lines[end - 1].startswith((' ', '\t')):
        end -= 1
    return '\n'.join(lines[start:end]) + '\n'


def repair_script(source):
    """Return (source, fixes). The source is unchanged when it already compiles."""
    if not isinstance(source, str):
        raise TypeError('The script must be text.')
    text = source.replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '')
    if text.startswith('\ufeff'):
        text = text[1:]
    if _compiles(text):
        return text, ([] if text == source else ['normalized line endings'])

    def unfence(value):
        if value.lstrip().startswith(('```', '~~~')):
            return strip_fences(value)
        return re.sub(r'^[ \t]*(?:```|~~~)[\w.+#-]*[ \t]*$\n?', '', value, flags=re.M)

    steps = (
        ('removed Markdown code fences', unfence),
        ('replaced typographic quotes and spaces', lambda value: value.translate(_QUOTES)),
        ('converted tabs to spaces', lambda value: value.expandtabs(4)),
        ('removed text around the code', _drop_prose),
        ('removed shared leading indentation', textwrap.dedent),
    )
    fixes = []
    current = text
    for label, change in steps:
        candidate = change(current)
        if candidate == current:
            continue
        current = candidate
        fixes.append(label)
        if _compiles(current):
            return (current if current.endswith('\n') else current + '\n'), fixes
    return text, []


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return tuple(reversed(parts))
    return ()


def _fix_blender_properties(source):
    """Correct known RNA paths on the scene, preserving the rest of the original script."""
    tree = ast.parse(source)
    replacements = []

    def visit_block(statements, aliases):
        # Only a simple, preceding assignment in the same straight-line block
        # establishes a type. A conditional, loop or new scope starts fresh.
        aliases = dict(aliases)
        for statement in statements:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr)):
                for field, value in ast.iter_fields(statement):
                    if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                        visit_block(value, {})
                aliases.clear()
                continue
            for node in ast.walk(statement):
                if not isinstance(node, ast.Attribute) or node.attr not in ('image_format', 'file_format'):
                    continue
                path = _dotted(node.value)
                if not path:
                    continue
                path = aliases.get(path[0], (path[0],)) + path[1:]
                if path == ('bpy', 'context', 'scene', 'render'):
                    replacement = 'image_settings.file_format'
                elif path == ('bpy', 'context', 'scene', 'render', 'image_settings') and node.attr == 'image_format':
                    replacement = 'file_format'
                else:
                    continue
                replacements.append((node.end_lineno - 1, node.end_col_offset, node.attr, replacement))
            if isinstance(statement, ast.Assign):
                value = _dotted(statement.value)
                resolved = aliases.get(value[0], (value[0],)) + value[1:] if value else ()
                for target in statement.targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store):
                            aliases.pop(name.id, None)
                    if isinstance(target, ast.Name) and resolved[:3] == ('bpy', 'context', 'scene'):
                        aliases[target.id] = resolved
            elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                aliases.pop(statement.target.id, None)

    visit_block(tree.body, {})
    if not replacements:
        return source, []
    # Python's AST column offsets use UTF-8 bytes, including for non-ASCII source.
    lines = source.encode('utf-8').split(b'\n')
    for row, end, attr, replacement in sorted(replacements, reverse=True):
        start = end - len(attr)
        lines[row] = lines[row][:start] + replacement.encode('ascii') + lines[row][end:]
    return b'\n'.join(lines).decode('utf-8'), ['corrected the Blender image format property']


def lint_blender_script(source):
    """Return (source, notes, problems) for a script that already compiles.

    Calls the app performs itself are replaced with ``pass`` and noted. Problems
    are things the model should fix; they are returned as plain sentences.
    """
    tree = ast.parse(source)
    notes, problems = [], []
    imported = set()
    uses_bpy = False
    replace = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
        elif isinstance(node, ast.Name) and node.id == 'bpy':
            uses_bpy = True
    for statement in ast.walk(tree):
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            continue
        name = _dotted(statement.value.func)
        reason = _APP_OWNED.get(name) or ('the app closes Blender after checking the scene' if name in _EXIT_CALLS else None)
        if reason:
            replace.append((statement.lineno, statement.col_offset, statement.end_lineno,
                            statement.end_col_offset, '.'.join(name), reason))
    updated = source
    if replace:
        # Replace exactly the expression, including on a line shared with a mesh operation.
        data = source.encode('utf-8')
        starts = [0] + [m.end() for m in re.finditer(b'\n', data)]
        for first, column, last, end_column, name, reason in sorted(replace, reverse=True):
            start, end = starts[first - 1] + column, starts[last - 1] + end_column
            replacement = b'pass'
            if not data[end:].partition(b'\n')[0].strip():
                replacement += f'  # {name}() removed: {reason}'.encode('utf-8')
            data = data[:start] + replacement + data[end:]
            notes.append(f'{name}() removed: {reason}')
        updated = data.decode('utf-8')
        if not _compiles(updated):
            updated, notes = source, []
    if uses_bpy and 'bpy' not in imported:
        updated = 'import bpy\n' + updated
        notes.append('added the missing "import bpy"')
    if not uses_bpy:
        problems.append('The script never uses bpy, so it cannot build a Blender scene.')
    outside = sorted(imported & _OUT_OF_SCOPE)
    if outside:
        problems.append('A scene script should only build the scene. Remove the use of ' + ', '.join(outside) + '.')
    updated, property_notes = _fix_blender_properties(updated)
    return updated, notes + property_notes, problems


_ERROR_LINE = re.compile(r'[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt|Warning)\b')
_HINTS = (
    (r'key ["\']Material Output["\'] not found',
     'After clearing material nodes, create an output with nodes.new("ShaderNodeOutputMaterial"). '
     'Connect the surface shader to its Surface input and displacement to its Displacement input.'),
    (r'key ["\'](?:World BSDF|Principled BSDF)["\'] not found',
     'Check whether this is a world or material node tree. A world uses a Background shader linked '
     'to a World Output. A material can use a Principled BSDF linked to a Material Output.'),
    (r'(?:RenderSettings|ImageFormatSettings).*has no attribute.*(?:image_format|file_format|png)',
     'Set the file type on scene.render.image_settings.file_format, for example to "PNG".'),
    (r'context is incorrect|poll\(\) failed',
     'Operators such as mode_set or select_all need an active object. Build meshes with '
     'bpy.ops.mesh.primitive_*_add or bpy.data.meshes.new instead of changing modes.'),
    (r'(?:Emission|Specular|Subsurface|Transmission|Sheen|Clearcoat|Coat)[\w ]*["\']? not found|'
     r"has no attribute '(?:Emission|Specular|Subsurface|Transmission|Sheen|Clearcoat)",
     'Principled BSDF input names differ between Blender versions. Look an input up with '
     'node.inputs.get(name) and skip it when it is missing.'),
    (r'enum "[^"]*" not found|not found in \(', 'An option value does not exist in this Blender version. '
     'Leave render engine and other settings at their defaults.'),
    (r'No module named', 'Only bpy, bmesh, mathutils and the Python standard library are available.'),
    (r'has no attribute', 'That name does not exist in this Blender version. Use only widely supported bpy calls.'),
)


def describe_blender_failure(output, source='', *, script_file='', script_name='scene.py'):
    """Turn Blender's console output into the error a model needs in order to fix its script."""
    text = (output or '').replace('\r\n', '\n')
    exit_code = re.match(r'Exit (-?\d+)\n', text)
    start = text.rfind('Traceback (most recent call last):')
    if start < 0:
        errors = [line.strip() for line in text.split('\n') if _ERROR_LINE.match(line.strip())
                  or line.strip().startswith('Error:')]
        tail = '\n'.join(errors[-6:]) if errors else '\n'.join(text.strip().split('\n')[-12:])
        head = ('Blender stopped' + (f' with exit code {exit_code.group(1)}' if exit_code else '')
                + ' without a Python traceback.')
        return (head + '\n' + tail[-1500:]).strip()
    frames = []
    summary = ''
    for line in text[start:].split('\n')[1:80]:
        if line.startswith((' ', '\t')):
            frames.append(line)
            continue
        if line.strip():
            summary = line.strip()
        break
    located = None
    for line in frames:
        match = re.search(r'File "([^"]+)", line (\d+)', line)
        if not match:
            continue
        path = match.group(1).replace('\\', '/')
        if script_file:
            if path.endswith('/' + script_file) or path == script_file:
                located = int(match.group(2))
        elif not (path.endswith(('/build.py', '/runpy.py')) or '/scripts/' in path or path.startswith('<')):
            located = int(match.group(2))
    report = 'Blender ran the script and Python raised: ' + (summary[:600] or 'an error it did not name')
    lines = source.split('\n') if source else []
    if located and 1 <= located <= len(lines):
        report += f'\nAt {script_name} line {located}:\n    ' + lines[located - 1].strip()[:200]
    evidence = summary + '\n' + '\n'.join(frames)
    for pattern, hint in _HINTS:
        if re.search(pattern, evidence, re.I):
            report += '\nHint: ' + hint
            break
    return report
