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

    def test_inspect_then_fill_requires_reobservation(self):
        result=json.loads(self.engine.execute('inspect','123'))
        self.assertEqual(result['controls'][0]['name'],'Player name')
        self.engine.execute('fill','0','Builder')
        self.control.set_edit_text.assert_called_once_with('Builder')
        with self.assertRaises(ValueError):self.engine.execute('click','0')

    def test_cancel_prevents_input(self):
        self.engine.execute('inspect','123');self.cancel.set()
        with self.assertRaises(InterruptedError):self.engine.execute('click','0')
        self.control.click_input.assert_not_called()

    def test_password_controls_not_exposed(self):
        self.control.element_info.element.CurrentIsPassword=True
        self.assertEqual(json.loads(self.engine.execute('inspect','123'))['controls'],[])

    def test_requires_observed_control(self):
        self.engine.execute('inspect','123')
        with self.assertRaises(ValueError):self.engine.execute('click','99')
        self.control.click_input.assert_not_called()

    def test_select_and_wait_are_available(self):
        self.engine.execute('inspect','123');self.engine.execute('select','0','Windowed')
        self.control.select.assert_called_once_with('Windowed')
        self.assertIn('Waited',self.engine.execute('wait','','0'))
