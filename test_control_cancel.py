import os
import threading
import unittest
from control_cancel import EscapeCancel, is_physical_escape, WM_KEYDOWN, WM_SYSKEYDOWN, VK_ESCAPE, LLKHF_INJECTED, LLKHF_LOWER_IL_INJECTED


class FakeAPI:
    def __init__(self,fail=False):
        self.fail=fail;self.wake=threading.Event();self.uninstalled=[];self.on_escape=None
    def thread_id(self):return 123
    def ensure_queue(self):pass
    def install(self,callback):
        if self.fail:raise OSError('No hook')
        self.on_escape=callback;return 456
    def pump(self,stop):self.wake.wait(2)
    def quit(self,identifier):self.wake.set()
    def uninstall(self,handle):self.uninstalled.append(handle)


class EscapeTests(unittest.TestCase):
    def test_only_physical_escape_keydown_matches(self):
        self.assertTrue(is_physical_escape(0,WM_KEYDOWN,VK_ESCAPE,0))
        self.assertTrue(is_physical_escape(0,WM_SYSKEYDOWN,VK_ESCAPE,0))
        for args in ((-1,WM_KEYDOWN,VK_ESCAPE,0),(0,0x101,VK_ESCAPE,0),(0,WM_KEYDOWN,65,0),
                     (0,WM_KEYDOWN,VK_ESCAPE,LLKHF_INJECTED),(0,WM_KEYDOWN,VK_ESCAPE,LLKHF_LOWER_IL_INJECTED)):
            self.assertFalse(is_physical_escape(*args))

    def test_armed_callback_runs_off_hook_thread_once_and_disarm_unhooks(self):
        api=FakeAPI();called=threading.Event();threads=[]
        def callback():threads.append(threading.current_thread().name);called.set()
        control=EscapeCancel(lambda:api)
        self.assertTrue(control.arm(callback))
        api.on_escape();self.assertTrue(called.wait(1));api.on_escape()
        control.disarm();control.disarm()
        self.assertEqual(threads,['TalkToAi Escape signal'])
        self.assertEqual(api.uninstalled,[456])

    def test_failed_registration_never_reports_armed(self):
        api=FakeAPI(fail=True);control=EscapeCancel(lambda:api)
        self.assertFalse(control.arm(lambda:None));control.disarm()
        self.assertEqual(api.uninstalled,[])

    def test_factory_failure_returns_false(self):
        def factory():raise OSError('Unsupported platform')
        self.assertFalse(EscapeCancel(factory).arm(lambda:None))

    def test_disarm_prevents_later_callback(self):
        api=FakeAPI();called=threading.Event();control=EscapeCancel(lambda:api)
        self.assertTrue(control.arm(called.set));control.disarm();api.on_escape()
        self.assertFalse(called.is_set())

    def test_registration_after_setup_timeout_is_cleaned_up(self):
        class SlowAPI(FakeAPI):
            def install(self,callback):
                threading.Event().wait(.05)
                return super().install(callback)
        api=SlowAPI();control=EscapeCancel(lambda:api,setup_timeout=.01)
        self.assertFalse(control.arm(lambda:None))
        self.assertEqual(api.uninstalled,[456])

    def test_rearming_releases_previous_session(self):
        first=FakeAPI();second=FakeAPI();apis=iter([first,second]);control=EscapeCancel(lambda:next(apis))
        self.assertTrue(control.arm(lambda:None));self.assertTrue(control.arm(lambda:None))
        self.assertEqual(first.uninstalled,[456]);control.disarm();self.assertEqual(second.uninstalled,[456])

    @unittest.skipUnless(os.name=='nt' and os.environ.get('TALKTOAI_NATIVE_ACCEPTANCE')=='1','Opt-in Windows hook registration only; no input generated')
    def test_native_hook_registration_and_cleanup_without_keyboard_input(self):
        control=EscapeCancel();called=threading.Event()
        try:self.assertTrue(control.arm(called.set))
        finally:control.disarm()
        self.assertFalse(called.is_set())
        # Exercise the native structure layout and injected-event classification
        # without generating any keyboard event or touching another app.
        from control_cancel import _WindowsAPI
        native=_WindowsAPI();record=native.keyboard_type()
        record.vkCode=VK_ESCAPE;record.flags=LLKHF_INJECTED
        self.assertFalse(is_physical_escape(0,WM_KEYDOWN,record.vkCode,record.flags))
        record.flags=0
        self.assertTrue(is_physical_escape(0,WM_KEYDOWN,record.vkCode,record.flags))


if __name__=='__main__':unittest.main()
