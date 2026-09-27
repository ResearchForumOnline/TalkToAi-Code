import json
import tempfile
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
        rect.width.return_value=100;rect.height.return_value=40
        self.window.rectangle.return_value=rect
        self.window.handle=123;self.desktop.windows.return_value=[self.window]
        self.temp_project=tempfile.TemporaryDirectory()
        self.cancel=threading.Event();self.engine=ComputerTools(self.temp_project.name,self.cancel,self.desktop)
        self.engine.execute('windows')

    def tearDown(self):
        self.temp_project.cleanup()

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

    def test_point_click_requires_recent_screenshot_of_same_window(self):
        self.inspect()
        with self.assertRaisesRegex(ValueError,'fresh screenshot'):
            self.engine.execute('click_point','','30,20')
        self.inspect()
        image=json.loads(self.engine.execute('screenshot'))
        self.assertEqual(image['coordinate_system'],'window-relative x,y')
        self.engine.execute('click_point','','30,20')
        self.window.click_input.assert_called_once_with(coords=(30,20))

    def test_point_click_rejects_window_move_and_expired_image(self):
        self.inspect()
        self.engine.execute('screenshot')
        rect=self.window.rectangle.return_value
        rect.right=140
        with self.assertRaisesRegex(ValueError,'moved or resized'):
            self.engine.execute('click_point','','30,20')
        self.window.click_input.assert_not_called()
        rect.right=100
        self.inspect()
        self.engine.execute('screenshot')
        self.engine.pixel_observed-=31
        with self.assertRaisesRegex(ValueError,'fresh screenshot'):
            self.engine.execute('click_point','','30,20')
        self.window.click_input.assert_not_called()

    def test_point_click_rejects_invalid_or_outside_coordinates(self):
        for point in ('30', 'x,10', '-1,4', '100,20'):
            self.inspect()
            self.engine.execute('screenshot')
            with self.assertRaises(ValueError):
                self.engine.execute('click_point','',point)
        self.window.click_input.assert_not_called()
