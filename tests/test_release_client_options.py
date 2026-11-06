import json,os,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch
from veriworld.common.vlm import VLMClient,load_configs

class ReleaseClientOptionsTests(unittest.TestCase):
    def configured(self,options,extra=None):
        with TemporaryDirectory() as root,patch.dict(os.environ,{'VERIWORLD_TEST_KEY':'local-test-key'}):
            p=Path(root)/'config.json';p.write_text(json.dumps([{'name':'test','model':'pinned-model','api_key':'env:VERIWORLD_TEST_KEY','base_url':'https://test.invalid/v1','client_options':options,'extra_params':extra or {}}]))
            return VLMClient.from_config(load_configs(p)[0])
    def test_kimi_temperature_timeout_and_identity(self):
        c=self.configured({'temperature':1,'timeout':600,'max_retries':1,'expected_model':'pinned-model'})
        response=Mock();response.json.return_value={'model':'pinned-model','choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]}
        with patch('veriworld.common.vlm.requests.post',return_value=response) as post:
            self.assertEqual(c.chat([{'role':'user','content':'test'}]),'ok')
        self.assertEqual(post.call_args.kwargs['json']['temperature'],1)
        self.assertEqual(post.call_args.kwargs['timeout'],600)
        self.assertEqual(c.last_returned_model,'pinned-model')
    def test_omitted_temperature_and_completion_token_parameter(self):
        c=self.configured({'omit_temperature':True,'token_parameter':'max_completion_tokens'}, {'temperature':.1,'max_tokens':999,'model':'forbidden-override'})
        response=Mock();response.json.return_value={'model':'pinned-model','choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]}
        with patch('veriworld.common.vlm.requests.post',return_value=response) as post:c.chat([],max_tokens=321)
        body=post.call_args.kwargs['json']
        self.assertNotIn('temperature',body);self.assertNotIn('max_tokens',body)
        self.assertEqual(body['max_completion_tokens'],321);self.assertEqual(body['model'],'pinned-model')
    def test_model_drift_rejected_and_provider_evidence_retained(self):
        c=self.configured({'expected_model':'pinned-model','max_retries':1})
        response=Mock();response.json.return_value={'model':'another-version','choices':[{'message':{'content':'answer'},'finish_reason':'stop'}]}
        with patch('veriworld.common.vlm.requests.post',return_value=response):
            with self.assertRaisesRegex(ValueError,'expected_model'):c.chat([])
        self.assertEqual(c.last_response['model'],'another-version')
    def test_explicit_anthropic_protocol_with_proxy_url_and_image(self):
        c=self.configured({'protocol':'anthropic','omit_temperature':True,'expected_model':'pinned-model'})
        response=Mock();response.json.return_value={'model':'pinned-model','content':[{'type':'text','text':'ok'}],'stop_reason':'end_turn'}
        messages=[{'role':'user','content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,eA=='}}]}]
        with patch('veriworld.common.vlm.requests.post',return_value=response) as post:self.assertEqual(c.chat(messages),'ok')
        self.assertTrue(post.call_args.args[0].endswith('/messages'))
        body=post.call_args.kwargs['json'];self.assertNotIn('temperature',body)
        self.assertEqual(body['messages'][0]['content'][0]['source']['data'],'eA==')
    def test_unknown_options_rejected_before_network(self):
        with self.assertRaisesRegex(ValueError,'Unknown'):self.configured({'typo_temperature':1})

    def test_alias_request_still_checks_exact_returned_snapshot(self):
        c=self.configured({'request_model':'provider-alias','expected_model':'pinned-model','max_retries':1})
        response=Mock();response.json.return_value={'model':'pinned-model','choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]}
        with patch('veriworld.common.vlm.requests.post',return_value=response) as post:c.chat([])
        self.assertEqual(post.call_args.kwargs['json']['model'],'provider-alias')
        self.assertEqual(c.last_returned_model,'pinned-model')

if __name__=='__main__':unittest.main()
