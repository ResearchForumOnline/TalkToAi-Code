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

    def test_scroll_delivers_bounded_pattern_steps_and_consumes_snapshot(self):
        self.inspect();self.engine.execute('scroll',self.control_id,'down,line,3')
        self.assertEqual(self.control.scroll.call_count,3)
        self.control.scroll.assert_called_with('down','line',count=1,retry_interval=0)
        self.window.click_input.assert_not_called()
        with self.assertRaisesRegex(ValueError,'Inspect the target'):
            self.engine.execute('scroll',self.control_id,'down,line,1')

    def test_scroll_invalid_count_or_direction_never_delivers_input(self):
        for value in ('down,line,0','down,line,6','down,line,500','diagonal,line,2','up,pixel,1','down,line,1.5','down,line'):
            self.inspect()
            with self.assertRaises(ValueError):self.engine.execute('scroll',self.control_id,value)
        self.control.scroll.assert_not_called()
        self.window.scroll.assert_not_called()

    def test_scroll_cancel_between_steps_stops_without_repeating(self):
        self.inspect();self.control.scroll.side_effect=lambda *a,**kw:self.cancel.set()
        with self.assertRaises(InterruptedError):self.engine.execute('scroll',self.control_id,'down,page,5')
        self.assertEqual(self.control.scroll.call_count,1)

    def test_scroll_unsupported_pattern_never_falls_back_to_mouse(self):
        self.inspect();self.control.scroll.side_effect=AttributeError('Not scrollable')
        with self.assertRaises(AttributeError):self.engine.execute('scroll',self.control_id,'down,line,2')
        self.assertEqual(self.control.scroll.call_count,1)
        self.window.click_input.assert_not_called()
        self.control.wheel_mouse_input.assert_not_called()

    def test_window_scroll_rechecks_visibility_between_steps(self):
        self.inspect();self.window.scroll.side_effect=lambda *a,**kw:setattr(self.window.is_visible,'return_value',False)
        with self.assertRaisesRegex(ValueError,'hidden'):
            self.engine.execute('scroll','','right,line,3')
        self.assertEqual(self.window.scroll.call_count,1)

    def test_hidden_or_replaced_window_blocks_keys_and_scroll(self):
        self.inspect();self.window.is_visible.return_value=False
        with self.assertRaisesRegex(ValueError,'hidden'):self.engine.execute('key','','pageup')
        self.window.type_keys.assert_not_called()
        self.window.is_visible.return_value=True
        self.inspect();self.window.handle=456
        with self.assertRaisesRegex(ValueError,'replaced'):self.engine.execute('scroll','','down,page,1')
        self.window.scroll.assert_not_called()

    def test_changed_window_runtime_identity_blocks_input(self):
        self.window.element_info.runtime_id=[1,2,3];self.inspect()
        self.window.element_info.runtime_id[2]=4
        with self.assertRaisesRegex(ValueError,'replaced'):self.engine.execute('key','','home')
        self.window.type_keys.assert_not_called()

    def test_moved_window_blocks_keyboard_navigation(self):
        self.inspect();self.window.rectangle.return_value.left=10
        with self.assertRaisesRegex(ValueError,'moved or resized'):self.engine.execute('key','','ctrl+f')
        self.window.type_keys.assert_not_called()

    def test_navigation_keys_are_exact_and_clipboard_shortcuts_rejected(self):
        for value,expected in [('home','{HOME}'),('end','{END}'),('pageup','{PGUP}'),
                               ('pagedown','{PGDN}'),('shift+tab','+{TAB}'),('ctrl+f','^f'),
                               ('alt+left','%{LEFT}'),('ctrl+tab','^{TAB}')]:
            self.inspect();self.engine.execute('key','',value)
            self.window.type_keys.assert_called_with(expected,set_foreground=True)
        before=self.window.type_keys.call_count
        for value in ('ctrl+c','ctrl+v'):
            self.inspect()
            with self.assertRaisesRegex(ValueError,'Supported keys'):self.engine.execute('key','',value)
        self.assertEqual(self.window.type_keys.call_count,before)
