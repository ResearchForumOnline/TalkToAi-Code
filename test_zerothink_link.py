import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from providers import ProviderProfile,save_profiles,load_profiles
from zerothink_link import parse_reply,pairing_url,stream,save_token,load_token
from agent_core import schema

class AccountTests(unittest.TestCase):
    def test_structured_tools_validate_entire_batch(self):
        tools=[schema('read_file','Read',{'path':'file'})]
        result=parse_reply(json.dumps({'content':'Reading','tool_calls':[{'name':'read_file','arguments':{'path':'game.py'}}]}),tools)
        self.assertEqual(result['tool_calls'][0]['function']['name'],'read_file')
        for reply in ('run this command','{"tool_calls":[{"name":"delete_everything","arguments":{}}]}','{"tool_calls":[{"name":"read_file","arguments":{"path":2}}]}'):
            with self.assertRaises(ValueError):parse_reply(reply,tools)

    def test_pairing_origin_locked(self):
        self.assertIn('user_code=',pairing_url('https://zerothink.talktoai.org/cli/connect?user_code=demo'))
        for url in ('https://evil.example/cli/connect?user_code=x','http://zerothink.talktoai.org/cli/connect?user_code=x'):
            with self.assertRaises(ValueError):pairing_url(url)

    def test_account_profile_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'providers.json'
            profile=ProviderProfile('Vault','https://zerothink.talktoai.org','fixture',kind='zerothink')
            save_profiles(path,[profile]);self.assertEqual(load_profiles(path)[0].kind,'zerothink')

    def test_windows_session_encryption_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder,patch('zerothink_link.token_path',return_value=Path(folder)/'account.dpapi'):
            save_token('synthetic-test-session')
            self.assertNotIn(b'synthetic-test-session',(Path(folder)/'account.dpapi').read_bytes())
            self.assertEqual(load_token(),'synthetic-test-session')

    def test_vault_inference_uses_session_not_provider_key(self):
        profile=ProviderProfile('Vault','https://zerothink.talktoai.org','fixture',kind='zerothink')
        with patch('zerothink_link.load_token',return_value='synthetic-session'),patch('zerothink_link.request',return_value={'status':'success','reply':'{"content":"Hello","tool_calls":[]}'}) as request:
            events=list(stream(profile,{'messages':[{'role':'user','content':'hello'}],'tools':[]},threading.Event()))
            self.assertEqual(events[0]['message']['content'],'Hello')
            self.assertEqual(request.call_args.args[2],'synthetic-session')
            self.assertNotIn('key',request.call_args.args[1])
