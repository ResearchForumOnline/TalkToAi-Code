import tempfile
import threading
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock,patch
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

class PortableProviderKeyTests(unittest.TestCase):
    def setUp(self):
        import providers
        self.providers=providers
        self.key_path=Path('fixture-provider.dpapi')
        self.session=patch('providers._SESSION_KEYS',{});self.session.start();self.addCleanup(self.session.stop)
        self.path=patch('providers.key_path',return_value=self.key_path);self.path.start();self.addCleanup(self.path.stop)

    def backend(self, secure=False):
        backend=type('SecureFixture' if secure else 'PlaintextKeyring',(),{})()
        return SimpleNamespace(get_keyring=lambda:backend,get_password=Mock(return_value=''),
                               set_password=Mock(),errors=SimpleNamespace(KeyringError=RuntimeError))

    def test_keyless_local_routes_work_without_a_secure_keyring(self):
        for endpoint in ('http://127.0.0.1:1234/v1','http://localhost:1234/v1','http://[::1]:1234/v1'):
            profile=ProviderProfile('local fixture',endpoint,'fixture-model')
            backend=self.backend()
            with patch('providers.os.name','posix'),patch.dict('sys.modules',{'keyring':backend}):
                self.assertEqual(self.providers.api_key(profile),'')
            backend.get_password.assert_not_called()

    def test_marked_self_hosted_no_key_route_does_not_read_insecure_store(self):
        profile=ProviderProfile('self hosted fixture','https://self-hosted.invalid/v1','fixture-model',cost_tier='self_hosted')
        backend=self.backend()
        with patch('providers.os.name','posix'),patch.dict('sys.modules',{'keyring':backend}):
            self.assertEqual(self.providers.api_key(profile),'')
        backend.get_password.assert_not_called()

    def test_missing_keyring_allows_keyless_local_and_marked_self_hosted_only(self):
        profiles=(ProviderProfile('local fixture','http://127.0.0.1:1234/v1','fixture-model'),
                  ProviderProfile('self hosted fixture','https://self-hosted.invalid/v1','fixture-model',cost_tier='self_hosted'))
        with patch('providers.os.name','posix'),patch.dict('sys.modules',{'keyring':None}):
            for profile in profiles:self.assertEqual(self.providers.api_key(profile),'')
            external=ProviderProfile('external fixture','https://external.invalid/v1','fixture-model')
            with self.assertRaisesRegex(ValueError,'secure system credential store'):
                self.providers.api_key(external)

    def test_unmarked_external_and_cloud_routes_still_require_secure_lookup(self):
        for endpoint,tier in (('https://external.invalid/v1','unknown'),('https://cloud.invalid/v1','free'),
                              ('https://localhost.external.invalid/v1','unknown')):
            profile=ProviderProfile('cloud fixture',endpoint,'fixture-model',cost_tier=tier)
            backend=self.backend()
            with patch('providers.os.name','posix'),patch.dict('sys.modules',{'keyring':backend}):
                with self.assertRaisesRegex(ValueError,'secure system credential store'):
                    self.providers.api_key(profile)
            backend.get_password.assert_not_called()

    def test_session_key_works_without_system_keyring(self):
        profile=ProviderProfile('cloud fixture','https://cloud.invalid/v1','fixture-model',cost_tier='free')
        self.providers._SESSION_KEYS[self.providers.key_identity(profile)]='dummy-session-fixture-key'
        with patch('providers.os.name','posix'),patch.dict('sys.modules',{'keyring':None}):
            self.assertEqual(self.providers.api_key(profile),'dummy-session-fixture-key')

    def test_usable_secure_keyring_preserves_saved_local_authentication(self):
        profile=ProviderProfile('local fixture','http://127.0.0.1:1234/v1','fixture-model')
        backend=self.backend(secure=True);backend.get_password.return_value='dummy-local-fixture-key'
        with patch('providers.os.name','posix'),patch.dict('sys.modules',{'keyring':backend}):
            self.assertEqual(self.providers.api_key(profile),'dummy-local-fixture-key')
        backend.get_password.assert_called_once()

    def test_remembering_key_still_rejects_an_insecure_backend(self):
        profile=ProviderProfile('local fixture','http://127.0.0.1:1234/v1','fixture-model',cost_tier='self_hosted')
        backend=self.backend()
        with patch('providers.os.name','posix'),patch.dict('sys.modules',{'keyring':backend}):
            with self.assertRaisesRegex(ValueError,'secure system credential store'):
                self.providers.store_api_key(profile,'dummy-local-fixture-key',remember=True)
        backend.set_password.assert_not_called()
        self.assertNotIn(self.providers.key_identity(profile),self.providers._SESSION_KEYS)

if __name__=='__main__':unittest.main()
