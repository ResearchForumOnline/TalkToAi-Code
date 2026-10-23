"""Zero-price policy, metadata filtering and actual streaming request regressions."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from api_router import ApiRouter, ProviderHTTPError
from providers import ProviderProfile, configure_request, list_models, load_profiles, save_profiles, validate_cost_policy


class FreeProviderPolicyTests(unittest.TestCase):
    def router(self, model='openrouter/free', **kwargs):
        return ProviderProfile('Free router','https://openrouter.ai/api/v1',model,
                               cost_tier='free',fallback_enabled=True,free_only=True,**kwargs)

    def test_free_router_and_named_free_variant_have_zero_price_ceilings(self):
        for model in ('openrouter/free','meta-llama/example:free'):
            request={'models':['paid/model'],'plugins':[{'id':'web'}],'route':'fallback',
                     'provider':{'max_price':{'prompt':99}}}
            configure_request(self.router(model),request,{})
            self.assertEqual(request['model'],model)
            self.assertEqual(request['provider']['max_price'],{'prompt':0,'completion':0,'request':0,'image':0})
            self.assertTrue(request['provider']['require_parameters'])
            self.assertFalse(set(request)&{'models','plugins','route'})

    def test_paid_or_auto_primary_in_free_profile_is_blocked_before_transport(self):
        for model in ('openrouter/auto','paid/model','model:free:online'):
            primary=self.router(model);called=[]
            with self.assertRaises(ValueError):
                list(ApiRouter().stream(primary,[],{},threading.Event(),lambda *args:called.append(1)))
            self.assertEqual(called,[])

    def test_strict_route_skips_paid_unknown_and_unverifiable_free_accounts(self):
        primary=self.router();calls=[]
        options=[ProviderProfile('Paid','https://paid.invalid/v1','model',fallback_enabled=True,cost_tier='paid'),
                 ProviderProfile('Unknown','https://unknown.invalid/v1','model',fallback_enabled=True),
                 ProviderProfile('Account labelled free','https://api.groq.com/openai/v1','model',fallback_enabled=True,cost_tier='free'),
                 ProviderProfile('Local','http://127.0.0.1:1234/v1','local',fallback_enabled=True,cost_tier='self_hosted')]
        def stream(profile,*args):
            calls.append(profile.label)
            if profile is primary:raise ProviderHTTPError(429)
            yield {'message':{'content':'local reply'},'done':True}
        with patch('api_router.key_ready',return_value=True):
            result=list(ApiRouter().stream(primary,options,{},threading.Event(),stream))
        self.assertEqual(calls,['Free router','Local'])
        self.assertEqual(result[-1]['api_provider'],'Local')

    def test_strict_quota_failure_with_only_paid_alternative_stops(self):
        calls=[];paid=ProviderProfile('Paid','https://paid.invalid/v1','model',fallback_enabled=True,cost_tier='paid')
        def stream(profile,*args):
            calls.append(profile.label);raise ProviderHTTPError(402)
            yield
        with self.assertRaises(ProviderHTTPError):
            list(ApiRouter().stream(self.router(),[paid],{},threading.Event(),stream))
        self.assertEqual(calls,['Free router'])

    def test_official_free_router_is_eligible_as_an_alternative(self):
        primary=ProviderProfile('Local','http://127.0.0.1:1234/v1','local',fallback_enabled=True,cost_tier='self_hosted')
        calls=[]
        def stream(profile,*args):
            calls.append(profile.label)
            if profile is primary:raise OSError('fixture service stopped')
            yield {'message':{},'done':True}
        with patch('api_router.key_ready',return_value=True):
            list(ApiRouter().stream(primary,[self.router()],{},threading.Event(),stream))
        self.assertEqual(calls,['Local','Free router'])

    def test_direct_cloud_cannot_be_mislabelled_self_hosted_for_strict_route(self):
        for endpoint in ('https://api.openai.com/v1','https://api.meta.ai/v1','https://api.groq.com/openai/v1'):
            profile=ProviderProfile('Cloud',endpoint,'model',cost_tier='self_hosted',free_only=True)
            with self.assertRaises(ValueError):validate_cost_policy(profile)

    def test_free_only_flag_round_trips_without_key_material(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'profiles.json';save_profiles(path,[self.router()])
            self.assertTrue(load_profiles(path)[0].free_only)
            self.assertNotIn('api_key_value',path.read_text())
        self.assertFalse(ProviderProfile('Old','https://example.invalid/v1','model').free_only)

    def test_free_catalog_hides_paid_models_and_preserves_router(self):
        raw=json.dumps({'data':[{'id':'paid/model'},{'id':'example/model:free'},{'id':'openrouter/auto'}]}).encode()
        with patch('providers.api_key',return_value='dummy-fixture-key'),patch('providers.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value=raw
            self.assertEqual(list_models(self.router()),['example/model:free','openrouter/free'])

    def test_known_endpoints_reject_wrong_scheme_and_paths(self):
        for endpoint in ('http://api.meta.ai/v1','https://api.meta.ai/wrong',
                         'http://openrouter.ai/api/v1','https://openrouter.ai/wrong'):
            with self.assertRaises(ValueError):ProviderProfile('Bad endpoint',endpoint,'model')

    def test_actual_stream_serializes_zero_price_policy_and_tools(self):
        import agent_core
        from test_api_integration import provider_server
        with provider_server('success') as (endpoint,requests):
            port=int(endpoint.split(':')[2].split('/')[0])
            def loopback_connection(*args,**kwargs):return http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            with patch('agent_core.http.client.HTTPSConnection',side_effect=loopback_connection),patch('providers.api_key',return_value='dummy-fixture-key'):
                events=list(agent_core._provider_stream(self.router(),{'messages':[{'role':'user','content':'fixture'}],
                           'stream':True,'tools':[{'type':'function','function':{'name':'read_file','parameters':{'type':'object'}}}]},threading.Event()))
        self.assertTrue(events[-1]['done'])
        self.assertEqual(requests[0]['path'],'/api/v1/chat/completions')
        self.assertEqual(requests[0]['payload']['model'],'openrouter/free')
        self.assertEqual(requests[0]['payload']['provider']['max_price']['completion'],0)
        self.assertEqual(requests[0]['payload']['tools'][0]['function']['name'],'read_file')


if __name__=='__main__':unittest.main()
