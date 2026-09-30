import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from providers import ProviderProfile,save_profiles,load_profiles,_secure_keyring
from api_router import ApiRouter,ProviderHTTPError,ProviderPartialResponseError,retry_delay

class ApiRouterTests(unittest.TestCase):
    def setUp(self):
        self.ready=patch('api_router.key_ready',return_value=True);self.ready.start();self.addCleanup(self.ready.stop)
    def profile(self,label,tier='free',enabled=True,**kwargs):
        return ProviderProfile(label,'https://'+label+'.invalid/v1','model',fallback_enabled=enabled,cost_tier=tier,**kwargs)
    def test_retry_after_and_cooldown_skip(self):
        a=self.profile('a');b=self.profile('b');router=ApiRouter(clock=lambda:10);calls=[]
        def stream(p,*args):
            calls.append(p.label)
            if p is a:raise ProviderHTTPError(429,'120')
            yield {'message':{'content':'ok'},'done':False}
            yield {'message':{},'done':True,'api_usage':{'total_tokens':42}}
        results=list(router.stream(a,[a,b],{},threading.Event(),stream))
        self.assertEqual(calls,['a','b']);self.assertEqual(router.remaining(a),120)
        self.assertEqual(results[-1]['api_usage_source'],'provider_reported')
        calls.clear();list(router.stream(a,[a,b],{},threading.Event(),stream));self.assertEqual(calls,['b'])
    def test_paid_unknown_and_opted_out_never_called(self):
        a=self.profile('a');profiles=[self.profile('paid','paid'),self.profile('unknown','unknown'),self.profile('disabled',enabled=False)]
        calls=[]
        def stream(p,*args):
            calls.append(p.label);raise ProviderHTTPError(429)
            yield
        with self.assertRaises(ProviderHTTPError):list(ApiRouter().stream(a,profiles,{},threading.Event(),stream))
        self.assertEqual(calls,['a'])
    def test_endpoint_quota_prevents_switching_models_or_keys_same_provider(self):
        a=self.profile('a');same=ProviderProfile('same','https://a.invalid/v1','different-model','DIFFERENT_KEY',fallback_enabled=True,cost_tier='free');b=self.profile('b');calls=[]
        def stream(p,*args):
            calls.append(p.label)
            if p is a:raise ProviderHTTPError(429,'120')
            yield {'message':{},'done':True}
        list(ApiRouter().stream(a,[same,b],{},threading.Event(),stream));self.assertEqual(calls,['a','b'])

    def test_missing_key_alternative_skipped_and_billing_quota_falls_back(self):
        a=self.profile('a');missing=self.profile('missing');b=self.profile('b');calls=[]
        def stream(p,*args):
            calls.append(p.label)
            if p is a:raise ProviderHTTPError(402)
            yield {'message':{},'done':True}
        with patch('api_router.key_ready',side_effect=lambda p:p is not missing):
            list(ApiRouter().stream(a,[missing,b],{},threading.Event(),stream))
        self.assertEqual(calls,['a','b'])

    def test_auth_error_no_fallback(self):
        a=self.profile('a');b=self.profile('b');calls=[]
        def stream(p,*args):
            calls.append(p.label);raise ProviderHTTPError(401)
            yield
        with self.assertRaises(ProviderHTTPError):list(ApiRouter().stream(a,[b],{},threading.Event(),stream))
        self.assertEqual(calls,['a'])
    def test_partial_content_and_tools_never_replayed(self):
        for message in ({'content':'partial'},{'tool_calls':[{'function':{'name':'edit_file'}}]}):
            a=self.profile('a');b=self.profile('b');calls=[]
            def stream(p,*args):
                calls.append(p.label);yield {'message':message,'done':False};raise OSError('interrupted')
            with self.assertRaises(ProviderPartialResponseError):list(ApiRouter().stream(a,[b],{},threading.Event(),stream))
            self.assertEqual(calls,['a'])
    def test_vision_and_free_openrouter_filters(self):
        a=self.profile('a',supports_vision=True);text=self.profile('text');vision=self.profile('vision',supports_vision=True)
        paid_model=ProviderProfile('router','https://openrouter.ai/api/v1','paid-model',fallback_enabled=True,cost_tier='free',supports_vision=True)
        calls=[]
        def stream(p,*args):
            calls.append(p.label)
            if p is a:raise ProviderHTTPError(429)
            yield {'message':{},'done':True}
        list(ApiRouter().stream(a,[text,paid_model,vision],{'messages':[{'images':['fixture']}]},threading.Event(),stream));self.assertEqual(calls,['a','vision'])
    def test_cancel_before_any_request(self):
        event=threading.Event();event.set()
        with self.assertRaises(InterruptedError):list(ApiRouter().stream(self.profile('a'),[],{},event,lambda *args:iter([])))
    def test_profile_metadata_round_trip_no_secret(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'profiles.json';p=self.profile('a',supports_vision=True,priority=7);save_profiles(path,[p]);loaded=load_profiles(path)[0]
            self.assertTrue(loaded.fallback_enabled);self.assertTrue(loaded.supports_vision);self.assertEqual(loaded.priority,7)
            self.assertNotIn('api_key_value',path.read_text())
    def test_old_profiles_default_to_no_fallback(self):
        self.assertFalse(ProviderProfile('old','https://old.invalid/v1','model').fallback_enabled)
    def test_secure_keyring_rejects_plaintext(self):
        backend=type('PlaintextKeyring',(),{})();module=type('Fixture',(),{'get_keyring':lambda self:backend})()
        with self.assertRaises(ValueError):_secure_keyring(module)
    def test_store_credentials_have_separate_path_and_namespace(self):
        import providers
        p=self.profile('a')
        with patch('updates.is_store_package',return_value=False):
            direct=providers.key_path(p);direct_namespace=providers.key_namespace()
        with patch('updates.is_store_package',return_value=True):
            store=providers.key_path(p);store_namespace=providers.key_namespace()
        self.assertNotEqual(direct,store);self.assertNotEqual(direct_namespace,store_namespace)

    def test_retry_after_date(self):
        self.assertEqual(retry_delay('Thu, 01 Jan 1970 00:02:00 GMT',now=0),120)
        self.assertEqual(retry_delay('bad'),60)

if __name__=='__main__':unittest.main()
