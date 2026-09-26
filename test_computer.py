import json
import threading
import unittest
from unittest.mock import MagicMock
from computer_tools import ComputerTools


class ComputerTests(unittest.TestCase):
    def setUp(self):
        self.desktop=MagicMock();self.window=MagicMock();self.control=MagicMock()
        self.desktop.window.return_value.wrapper_object.return_value=self.window
        self.window.descendants.return_value=[self.control]
        self.window.window_text.return_value='Fixture'
        self.control.window_text.return_value='Player name'
        self.control.element_info.control_type='Edit'
        self.control.element_info.element.CurrentIsPassword=False
        rect=self.control.rectangle.return_value
        rect.left=0;rect.top=0;rect.right=100;rect.bottom=40
        self.window.rectangle.return_value=rect
        self.window.handle=123;self.desktop.windows.return_value=[self.window]
        self.cancel=threading.Event();self.engine=ComputerTools('.',self.cancel,self.desktop)
        self.engine.execute('windows')

    def inspect(self):
        result=self.engine.execute('inspect','123')
        controls=json.loads(result)['controls']
        self.control_id=controls[0]['id'] if controls else None
        return result

    def test_inspect_then_fill_requires_reobservation(self):
        result=json.loads(self.inspect())
        self.assertEqual(result['controls'][0]['name'],'Player name')
        self.engine.execute('fill',self.control_id,'Builder')
        self.control.set_edit_text.assert_called_once_with('Builder')
        with self.assertRaises(ValueError):self.engine.execute('click',self.control_id)

    def test_cancel_prevents_input(self):
        self.inspect();self.cancel.set()
        with self.assertRaises(InterruptedError):self.engine.execute('click',self.control_id)
        self.control.click_input.assert_not_called()

    def test_password_controls_not_exposed(self):
        self.control.element_info.element.CurrentIsPassword=True
        self.assertEqual(json.loads(self.inspect())['controls'],[])

    def test_requires_observed_control(self):
        self.inspect()
        with self.assertRaises(ValueError):self.engine.execute('click','99')
        self.control.click_input.assert_not_called()

    def test_select_and_wait_are_available(self):
        self.inspect();self.engine.execute('select',self.control_id,'Windowed')
        self.control.select.assert_called_once_with('Windowed')
        self.assertIn('Waited',self.engine.execute('wait','','0'))

    def test_click_prefers_accessibility_invoke(self):
        self.inspect();self.engine.execute('click',self.control_id)
        self.control.invoke.assert_called_once_with()
        self.control.click_input.assert_not_called()

    def test_click_only_falls_back_for_missing_invoke_pattern(self):
        from pywinauto.uia_defines import NoPatternInterfaceError
        self.control.invoke.side_effect=NoPatternInterfaceError()
        self.inspect();self.engine.execute('click',self.control_id)
        self.window.set_focus.assert_called_once_with()
        self.control.click_input.assert_called_once_with()

    def test_unknown_invoke_failure_never_repeats_input(self):
        self.control.invoke.side_effect=RuntimeError('Provider failed after an uncertain action')
        self.inspect()
        with self.assertRaises(RuntimeError):self.engine.execute('click',self.control_id)
        self.control.click_input.assert_not_called()

    def test_previous_snapshot_id_cannot_target_new_control(self):
        self.inspect();old_id=self.control_id
        self.inspect()
        self.assertNotEqual(old_id,self.control_id)
        with self.assertRaisesRegex(ValueError,'latest inspect'):
            self.engine.execute('click',old_id)
        self.control.invoke.assert_not_called()
        self.engine.execute('click',self.control_id)
        self.control.invoke.assert_called_once_with()

    def test_disabled_control_reported_and_input_rejected(self):
        self.control.is_enabled.return_value=False
        result=json.loads(self.inspect())
        self.assertFalse(result['controls'][0]['enabled'])
        with self.assertRaisesRegex(ValueError,'hidden or disabled'):
            self.engine.execute('fill',self.control_id,'Builder')
        self.control.set_edit_text.assert_not_called()

    def test_control_hidden_after_inspect_is_rejected(self):
        self.inspect();self.control.is_visible.return_value=False
        with self.assertRaisesRegex(ValueError,'hidden or disabled'):
            self.engine.execute('click',self.control_id)
        self.control.invoke.assert_not_called()

    def test_control_changed_to_password_after_inspect_is_rejected(self):
        self.inspect();self.control.element_info.element.CurrentIsPassword=True
        with self.assertRaisesRegex(ValueError,'password field'):
            self.engine.execute('fill',self.control_id,'Builder')
        self.control.set_edit_text.assert_not_called()

    def test_failed_control_inspection_does_not_leave_actionable_id(self):
        self.control.rectangle.side_effect=RuntimeError('Control disappeared')
        result=json.loads(self.inspect())
        self.assertEqual(result['controls'],[])
        self.assertEqual(self.engine.controls,{})
