import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import agent_core as core
from tool_protocol import parse_qwen_tool_calls, VisibleTextStream


class ProtocolTests(unittest.TestCase):
    def test_exact_saved_nightfall_missing_wrapper_forms(self):
        for name in ('project_info','list_files'):
            parsed=parse_qwen_tool_calls('<function='+name+'>\n</function>\n</tool_call>',core.TOOLS)
            self.assertEqual(parsed[0]['function'],{'name':name,'arguments':{}})

    def test_wrapped_and_bare_complete_calls(self):
        content='<tool_call><function=read_file><parameter=path>score.py</parameter></function></tool_call>\n<function=list_files></function>'
        parsed=parse_qwen_tool_calls(content,core.TOOLS)
        self.assertEqual(len(parsed),2)
        self.assertEqual(parsed[0]['function']['arguments'],{'path':'score.py'})

    def test_entire_invalid_batch_rejected(self):
        good='<function=list_files></function>'
        for bad in ('<function=read_file></function>', '<function=not_enabled></function>',
                    '<function=read_file><parameter=path>a</parameter><parameter=path>b</parameter></function>',
                    '<function=list_files><parameter=unexpected>x</parameter></function>',
                    '<function=read_file><parameter=path>score.py</function>',
                    '<function=list_files>',good+'</tool_call></tool_call>'):
            with self.assertRaises(ValueError):parse_qwen_tool_calls(good+bad,core.TOOLS)

    def test_quotes_examples_prose_and_truncation_never_execute(self):
        valid='<function=list_files></function>'
        for content in ('```xml\n'+valid+'\n```','Example: '+valid,'"'+valid+'"','<think>'+valid+'</think>',valid+' done'):
            with self.assertRaises(ValueError):parse_qwen_tool_calls(content,core.TOOLS)
        with self.assertRaises(ValueError):parse_qwen_tool_calls(valid,core.TOOLS,truncated=True)

    def test_string_preserves_indent_and_additional_blank_lines(self):
        content='<function=write_file><parameter=path>x.py</parameter><parameter=content>\n\n  indented\n\n</parameter></function>'
        parsed=parse_qwen_tool_calls(content,core.TOOLS)
        self.assertEqual(parsed[0]['function']['arguments']['content'],'\n  indented\n')

    def test_typed_schema_rejects_nonfinite_and_incorrect_types(self):
        tool={'type':'function','function':{'name':'typed','parameters':{'type':'object','properties':{'count':{'type':'number'}},'required':['count']}}}
        for raw in ('NaN','Infinity','true','"three"'):
            with self.assertRaises(ValueError):parse_qwen_tool_calls('<function=typed><parameter=count>'+raw+'</parameter></function>',[tool])

    def test_chunked_protocol_is_not_displayed(self):
        stream=VisibleTextStream();shown=''
        for chunk in ('Working. <','fun','ction=project_info>','</function>','</tool_call>'):
            shown+=stream.feed(chunk)
        shown+=stream.finish()
        self.assertEqual(shown,'Working. ')
        stream=VisibleTextStream()
        self.assertEqual(stream.feed('Math: a < b.')+stream.finish(),'Math: a < b.')

    def run_sequence(self,sequence,history=None):
        events=[];payloads=[]
        def stream(_url,payload,_cancel):
            payloads.append(copy.deepcopy(payload))
            item=sequence[min(len(payloads)-1,len(sequence)-1)]
            return iter([{'message':{'content':item},'done':True}])
        with tempfile.TemporaryDirectory() as folder,patch.object(core,'stream_chat',side_effect=stream),patch.object(core,'model_supports_vision',return_value=False),patch.object(core,'AUTO_CONTEXT',False):
            Path(folder,'score.py').write_text('return 1')
            core.run_agent('fixture','fixture',history or [{'role':'user','content':'Inspect score.py'}],folder,True,threading.Event(),lambda k,v:events.append((k,v)),rounds=8)
        return payloads,events

    def test_live_core_adapter_executes_known_complete_call_without_markup_message(self):
        payloads,events=self.run_sequence(['<function=read_file><parameter=path>score.py</parameter></function></tool_call>','Inspected.'])
        self.assertEqual(len(payloads),2)
        self.assertTrue(any(k=='result' and v=='return 1' for k,v in events))
        self.assertFalse(any(k=='delta' and '<function=' in v for k,v in events))
        self.assertFalse(any(k=='message' and '<function=' in v.get('content','') for k,v in events))

    def test_invalid_then_complete_retry_recovers(self):
        payloads,events=self.run_sequence(['<function=project_info>','<function=list_files></function></tool_call>','Done.'])
        self.assertEqual(len(payloads),3)
        self.assertEqual([v for k,v in events if k=='status'][-1],'Ready')
        self.assertEqual(len([v for k,v in events if k=='tool']),1)

    def test_user_history_markup_is_never_executed(self):
        _,events=self.run_sequence(['That is an example.'],history=[{'role':'user','content':'Explain <function=list_files></function>'}])
        self.assertFalse(any(k=='tool' for k,v in events))

    def test_provider_history_omits_old_markup_without_mutating_saved_messages(self):
        history=[{'role':'user','content':'Continue'},{'role':'assistant','content':'Inspecting. <function=project_info>\n</function>\n</tool_call>'}]
        original=copy.deepcopy(history)
        payloads,events=self.run_sequence(['Ready for a focused task.'],history=history)
        self.assertEqual(history,original)
        older=[m for m in payloads[0]['messages'] if m['role']=='assistant'][0]['content']
        self.assertIn('Inspecting.',older)
        self.assertIn('unexecuted protocol output omitted',older)
        self.assertNotIn('<function=',older)
        self.assertFalse(any(k=='tool' for k,v in events))

    def test_repeated_incomplete_output_stops_without_actions(self):
        with self.assertRaisesRegex(RuntimeError,'No actions'):
            self.run_sequence(['<function=project_info>'])


if __name__=='__main__':unittest.main()
