"""Offline checks that response diagnostics reach the task's truncation gate."""
import unittest
from unittest.mock import patch

import requests

from veriworld.common.vlm import VLMClient
from veriworld.benchmark.trial_error_prediction.light_war._response_diagnostics import (
    is_truncated, vlm_call_info,
)


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status_code = status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError('simulated provider error', response=self)


class ResponseMetadataTests(unittest.TestCase):
    def test_openai_truncation_reaches_harness_and_preserves_text(self):
        data = {'model': 'actual-model-v1', 'usage': {'completion_tokens': 99},
                'choices': [{'finish_reason': 'length', 'message': {'content': 'partial action'}}]}
        client = VLMClient('requested-model', 'not-a-key', 'https://example.invalid/v1', max_retries=1)
        with patch('veriworld.common.vlm.requests.post', return_value=Response(data)):
            self.assertEqual(client.chat([{'role': 'user', 'content': 'test'}]), 'partial action')
        self.assertTrue(is_truncated(vlm_call_info(client)))
        self.assertEqual(client.last_raw_finish_reason, 'length')
        self.assertEqual(client.last_returned_model, 'actual-model-v1')
        self.assertEqual(vlm_call_info(client)['returned_model'], 'actual-model-v1')
        self.assertEqual(vlm_call_info(client)['completion_tokens'], 99)
        self.assertEqual(client.last_response, data)

    def test_later_error_does_not_reuse_prior_metadata(self):
        data = {'model': 'v1', 'usage': {'total_tokens': 12},
                'choices': [{'finish_reason': 'stop', 'message': {'content': 'done'}}]}
        client = VLMClient('v1', 'not-a-key', 'https://example.invalid/v1', max_retries=1)
        with patch('veriworld.common.vlm.requests.post', side_effect=[Response(data), Response({}, 404)]):
            self.assertEqual(client.chat([]), 'done')
            with self.assertRaises(requests.HTTPError):
                client.chat([])
        self.assertIsNone(client.last_response)
        self.assertIsNone(client.last_returned_model)
        self.assertIsNone(client.last_usage)
        self.assertEqual(vlm_call_info(client), {})

    def test_anthropic_truncation_and_text_blocks(self):
        data = {'model': 'actual-claude-v1', 'stop_reason': 'max_tokens',
                'usage': {'input_tokens': 5, 'output_tokens': 99},
                'content': [{'type': 'text', 'text': 'one'}, {'type': 'text', 'text': 'two'}]}
        client = VLMClient('claude', 'not-a-key', 'https://anthropic.example.invalid/v1', max_retries=1)
        with patch('veriworld.common.vlm.requests.post', return_value=Response(data)):
            self.assertEqual(client.chat([{'role': 'user', 'content': 'test'}]), 'onetwo')
        self.assertTrue(is_truncated(vlm_call_info(client)))
        self.assertEqual(client.last_part_count, 2)
        self.assertEqual(client.last_returned_model, 'actual-claude-v1')
        self.assertEqual(client.last_usage, data['usage'])

    def test_retry_records_only_the_successful_response(self):
        data = {'model': 'v1', 'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}]}
        client = VLMClient('v1', 'not-a-key', 'https://example.invalid/v1', max_retries=2)
        with patch('veriworld.common.vlm.requests.post', side_effect=[Response({}, 503), Response(data)]), \
             patch('veriworld.common.vlm.time.sleep'):
            self.assertEqual(client.chat([]), 'ok')
        self.assertFalse(is_truncated(vlm_call_info(client)))
        self.assertEqual(client.last_response, data)

    def test_gemini_sdk_response_metadata(self):
        try:
            from google.genai import types
        except ImportError:
            self.skipTest('Optional google-genai SDK is not installed')
        response = types.GenerateContentResponse(
            model_version='gemini-v1',
            candidates=[types.Candidate(finish_reason='MAX_TOKENS',
                content=types.Content(parts=[types.Part(text='partial')]))],
            usage_metadata=types.GenerateContentResponseUsageMetadata(total_token_count=17))
        client = VLMClient('gemini', 'not-a-key', 'google-genai-direct', max_retries=1)
        client._record_gemini_metadata(response)
        self.assertTrue(is_truncated(vlm_call_info(client)))
        self.assertEqual(client.last_returned_model, 'gemini-v1')
        self.assertEqual(client.last_part_count, 1)
        self.assertEqual(vlm_call_info(client)['total_token_count'], 17)


if __name__ == '__main__':
    unittest.main()
