"""Checks that selected source can be promoted and recovered without losing later work."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import candidate_apply
from candidate_apply import CandidateApplyError, apply_candidate, preview_application, rollback_application


def sha(data):
    return hashlib.sha256(data).hexdigest()


class CandidateApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / 'project'
        self.candidate = self.base / 'candidate'
        self.project.mkdir(); self.candidate.mkdir()
        self.before = b'value = 1\n'
        self.after = b'value = 2\n'
        (self.project / 'app.py').write_bytes(self.before)
        (self.candidate / 'app.py').write_bytes(self.after)
        self.report = self.candidate / 'SKYNET-REPORT.json'
        self.data = {
            'schema': 'talktoai.skynet.candidate.v4', 'original_project': str(self.project.resolve()),
            'candidate_dir': str(self.candidate.resolve()), 'goal': 'Improve app',
            'selected_iteration': 1, 'cancelled': False, 'policy_status': 'unchanged',
            'evaluation_mode': 'checks_only', 'iterations': [{'iteration': 1, 'selected': True,
            'candidate_dir': str(self.candidate.resolve()),
            'checks': {'status': 'passed'}}],
            'changed_files': [{'path': 'app.py', 'status': 'modified', 'sha256': sha(self.after)}],
            'baseline_hashes': {'app.py': sha(self.before)}, 'diff_path': str(self.candidate / 'SKYNET-CANDIDATE.diff')}
        self.save()

    def save(self):
        self.data['baseline_inventory']={p.name:sha(p.read_bytes()) for p in self.project.glob('*.py')}
        self.data['candidate_inventory']={p.name:sha(p.read_bytes()) for p in self.candidate.glob('*.py')}
        self.data['baseline_modes']={p.name:stat.S_IMODE(p.stat().st_mode) for p in self.project.glob('*.py')}
        self.data['candidate_modes']={p.name:stat.S_IMODE(p.stat().st_mode) for p in self.candidate.glob('*.py')}
        diff=self.candidate/'SKYNET-CANDIDATE.diff'
        diff.write_text('Fixture diff; source hashes are checked independently.',encoding='utf-8')
        self.data['diff_sha256']=sha(diff.read_bytes())
        self.report.write_text(json.dumps(self.data), encoding='utf-8')

    def test_selected_candidate_applies_with_original_backup_and_can_restore(self):
        preview = preview_application(self.report, self.project)
        self.assertEqual(preview['changes'], [{'path': 'app.py', 'status': 'modified'}])
        result = apply_candidate(self.report, self.project)
        self.assertEqual((self.project / 'app.py').read_bytes(), self.after)
        manifest = Path(result['manifest'])
        self.assertEqual((manifest.parent / 'original' / 'app.py').read_bytes(), self.before)
        self.assertEqual(json.loads(manifest.read_text())['state'], 'applied')
        restored = rollback_application(manifest, self.project)
        self.assertTrue(restored['restored'])
        self.assertEqual((self.project / 'app.py').read_bytes(), self.before)

    def test_project_or_candidate_changed_after_check_is_rejected(self):
        (self.project / 'app.py').write_bytes(b'user edit\n')
        with self.assertRaisesRegex(ValueError, 'Project source changed'):
            apply_candidate(self.report, self.project)
        self.assertEqual((self.project / 'app.py').read_bytes(), b'user edit\n')
        (self.project / 'app.py').write_bytes(self.before)
        (self.candidate / 'app.py').write_bytes(b'unchecked edit\n')
        with self.assertRaisesRegex(ValueError, 'Candidate source changed'):
            apply_candidate(self.report, self.project)

    def test_rollback_preserves_later_user_edits(self):
        result = apply_candidate(self.report, self.project)
        (self.project / 'app.py').write_bytes(b'later user edit\n')
        with self.assertRaisesRegex(ValueError, 'Project changed after application'):
            rollback_application(result['manifest'], self.project)
        self.assertEqual((self.project / 'app.py').read_bytes(), b'later user edit\n')

    def test_add_and_delete_are_recovered_with_original_bytes(self):
        (self.project / 'obsolete.py').write_bytes(b'old = True\n')
        (self.candidate / 'feature.py').write_bytes(b'new = True\n')
        self.data['changed_files'] += [
            {'path':'obsolete.py','status':'deleted','sha256':None},
            {'path':'feature.py','status':'added','sha256':sha(b'new = True\n')}]
        self.data['baseline_hashes'].update({'obsolete.py':sha(b'old = True\n'),'feature.py':None})
        self.save()
        result = apply_candidate(self.report,self.project)
        self.assertFalse((self.project/'obsolete.py').exists())
        self.assertEqual((self.project/'feature.py').read_bytes(),b'new = True\n')
        rollback_application(result['manifest'],self.project)
        self.assertEqual((self.project/'obsolete.py').read_bytes(),b'old = True\n')
        self.assertFalse((self.project/'feature.py').exists())

    def test_unselected_or_protected_candidate_cannot_apply(self):
        self.data['selected_iteration'] = None; self.save()
        with self.assertRaisesRegex(ValueError, 'no selected candidate'):
            apply_candidate(self.report, self.project)
        self.data['selected_iteration'] = 1
        self.data['changed_files'][0]['path'] = 'test_app.py'
        self.data['baseline_hashes'] = {'test_app.py': sha(self.before)}
        self.save()
        with self.assertRaisesRegex(ValueError, 'protected or unsupported'):
            apply_candidate(self.report, self.project)

    def test_metric_candidate_requires_better_recorded_score(self):
        self.data['evaluation_mode'] = 'metric'
        self.data['baseline_metric'] = {'status': 'measured', 'value': 5.0}
        self.data['selected_metric'] = {'status': 'measured', 'value': 5.0}
        self.data['evaluation_contract'] = {'direction': 'minimize'}
        self.data['iterations'][0]['metric'] = self.data['selected_metric']
        self.save()
        with self.assertRaisesRegex(ValueError, 'does not improve'):
            apply_candidate(self.report, self.project)

    def test_unrelated_source_or_frozen_candidate_test_change_invalidates_promotion(self):
        (self.project/'other.py').write_text('before = 1\n')
        (self.candidate/'other.py').write_text('before = 1\n')
        (self.project/'test_app.py').write_text('assert True\n')
        (self.candidate/'test_app.py').write_text('assert True\n')
        self.save()
        (self.project/'other.py').write_text('user changed this\n')
        with self.assertRaisesRegex(ValueError,'Project source changed'):
            apply_candidate(self.report,self.project)
        (self.project/'other.py').write_text('before = 1\n')
        (self.candidate/'test_app.py').write_text('assert False\n')
        with self.assertRaisesRegex(ValueError,'Candidate source changed'):
            apply_candidate(self.report,self.project)

    def test_restore_can_resume_after_a_file_replacement_fails(self):
        (self.project/'second.py').write_bytes(b'old = 1\n')
        (self.candidate/'second.py').write_bytes(b'old = 2\n')
        self.data['changed_files'].append({'path':'second.py','status':'modified',
                                           'sha256':sha(b'old = 2\n')})
        self.data['baseline_hashes']['second.py']=sha(b'old = 1\n')
        self.save()
        result=apply_candidate(self.report,self.project)
        real=candidate_apply._replace_bytes
        attempts=[]
        def fail_second(target,source):
            attempts.append(str(target))
            if len(attempts)==2:raise OSError('Fixture interrupted restore')
            return real(target,source)
        with patch.object(candidate_apply,'_replace_bytes',side_effect=fail_second):
            with self.assertRaisesRegex(OSError,'Fixture interrupted restore'):
                rollback_application(result['manifest'],self.project)
        self.assertEqual(json.loads(Path(result['manifest']).read_text())['state'],'restoring')
        self.assertTrue(rollback_application(result['manifest'],self.project)['restored'])
        self.assertEqual((self.project/'app.py').read_bytes(),self.before)
        self.assertEqual((self.project/'second.py').read_bytes(),b'old = 1\n')

    def test_manifest_write_failure_after_edit_keeps_restore_available(self):
        real=candidate_apply._write_json
        calls=[0]
        def fail_after_replacement(path,value):
            calls[0]+=1
            if calls[0]>=3:raise OSError('Fixture manifest write failed')
            return real(path,value)
        with patch.object(candidate_apply,'_write_json',side_effect=fail_after_replacement):
            with self.assertRaises(CandidateApplyError) as failure:
                apply_candidate(self.report,self.project)
        manifest=Path(failure.exception.manifest)
        self.assertTrue(manifest.is_file())
        self.assertEqual((self.project/'app.py').read_bytes(),self.after)
        self.assertTrue(rollback_application(manifest,self.project)['restored'])
        self.assertEqual((self.project/'app.py').read_bytes(),self.before)

    @unittest.skipIf(os.name=='nt','POSIX executable permission check')
    def test_apply_and_restore_preserve_executable_mode(self):
        path=self.project/'app.py'; source=self.candidate/'app.py'
        path.chmod(0o755);source.chmod(0o755)
        result=apply_candidate(self.report,self.project)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o755)
        rollback_application(result['manifest'],self.project)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o755)

    @unittest.skipIf(os.name=='nt','POSIX mode integrity check')
    def test_mode_only_changes_in_candidate_or_backup_reject_promotion_and_restore(self):
        original=self.project/'app.py'; proposed=self.candidate/'app.py'
        original.chmod(0o755);proposed.chmod(0o755);self.save()
        proposed.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'Candidate source changed'):
            apply_candidate(self.report,self.project)
        proposed.chmod(0o755)
        result=apply_candidate(self.report,self.project)
        backup=Path(result['manifest']).parent/'original'/'app.py'
        backup.chmod(0o600)
        with self.assertRaisesRegex(ValueError,'Original backup changed'):
            rollback_application(result['manifest'],self.project)
        self.assertEqual(stat.S_IMODE(original.stat().st_mode),0o755)


if __name__ == '__main__':
    unittest.main()
