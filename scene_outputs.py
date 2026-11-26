"""Produce and register scene files from an already normalized design."""
import io
import json
import re
import uuid

import scene_kit
from workspace_outputs import register_output


def requests_local_generator(brief):
    if re.search(r"\b(?:offline|without (?:an? |the )?(?:llm|model|api)|no (?:llm|model|api)|built[- ]in generator)\b", brief, re.I):
        return True
    return bool(re.search(r'\b(?:quick|fast)\b', brief, re.I)
                and re.search(r'\b(?:your choice|of your choosing|anything you (?:want|like))\b', brief, re.I))


def local_design(brief):
    """Only draw into the selected project, and only for a supported subject."""
    if re.search(r'(?:[A-Za-z]:[\\/]|(?:^|\s)(?:~/|/\w+)|\b(?:desktop|downloads|documents|google drive|onedrive|dropbox)\b)', brief, re.I):
        return None
    return scene_kit.template_scene(brief)[1]


def build_scene_files(tools, design, blender, emit):
    """Return the files, evidence and any Blender failure after checking the actual output."""
    if tools.cancel.is_set():
        raise InterruptedError('Task stopped.')
    stem = 'scene-' + uuid.uuid4().hex[:8]
    files = []
    blender_problem = ''
    if blender:
        emit('status', 'Building the scene in Blender…')
        try:
            blend = tools.build_blender_scene(blender, scene_kit.to_blender_script(design), timeout=300)
        except InterruptedError:
            raise
        except (ValueError, OSError, RuntimeError) as exc:
            blender_problem = str(exc)[:600]
            emit('status', 'Blender could not build the generated scene. Saving it as a GLB instead…')
        else:
            files.append((blend.name, 'file', 'Blender scene'))
            script = blend.with_suffix('.py')
            if script.is_file():
                files.append((script.name, 'file', 'Script that rebuilds the scene'))
            verified = 'Blender saved and reopened the scene and checked that it contains geometry.'
    if not files:
        model = tools.write_generated(stem + '.glb', scene_kit.to_glb(design))
        checked = scene_kit.check_glb(model.read_bytes())
        files.append((model.name, 'file', '3D scene (GLB)'))
        verified = (f"The GLB was written and read back: {checked['objects']} objects, "
                    f"{checked['triangles']} triangles, {checked['materials']} materials.")
        note = 'This page contains the geometry. You can view it offline.'
        viewer = tools.write_generated(stem + '-viewer.html', scene_kit.to_viewer_html(design, note).encode('utf-8'))
        files.append((viewer.name, 'viewer', 'Interactive 3D viewer'))
    if tools.cancel.is_set():
        raise InterruptedError('Task stopped.')
    try:
        picture = io.BytesIO()
        scene_kit.render_preview(design).save(picture, format='PNG', optimize=True)
        preview = tools.write_generated(stem + '-preview.png', picture.getvalue())
        files.append((preview.name, 'image', 'Preview picture'))
    except InterruptedError:
        raise
    except (ImportError, OSError, ValueError) as exc:
        emit('status', 'Preview picture skipped: ' + str(exc)[:120])
    evidence = {'status': 'passed', 'summary': verified}
    for name, kind, title in files:
        artifact = json.loads(register_output(tools, name, title))
        if name == files[0][0]:
            evidence['primary_output'] = {'name': name, 'sha256': artifact['sha256']}
        artifact['type'] = kind
        if kind == 'viewer':
            artifact['auto_open'] = True
        emit('artifact', artifact)
    return files, evidence, blender_problem
