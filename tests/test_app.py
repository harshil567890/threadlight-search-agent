import json
import os
import tempfile
import unittest
from unittest.mock import patch

from app import app, db, grounding_sources, locks


class FakeUpstream:
    ok = True

    def __init__(self, events):
        self.events = events

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_lines(self):
        for event in self.events:
            yield ('data: ' + json.dumps(event)).encode()
            yield b''


class AgentTests(unittest.TestCase):
    def setUp(self):
        config_patch = patch('app.dotenv_values', return_value={})
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        app.config.update(TESTING=True, DATABASE=os.path.join(self.temp.name, 'test.db'))
        self.client = app.test_client()
        self.chat = self.client.post('/api/chats').json['id']

    def tearDown(self):
        locks.clear()
        self.temp.cleanup()

    def events(self):
        return [
            {'candidates': [{'content': {'parts': [{'text': 'Verified '}]}}]},
            {'candidates': [{'content': {'parts': [{'text': 'answer.'}]}, 'finishReason': 'STOP',
                'groundingMetadata': {
                    'webSearchQueries': ['verified answer'],
                    'groundingChunks': [{'web': {'uri': 'https://example.com', 'title': 'Primary source'}}],
                    'groundingSupports': [{'segment': {'startIndex': 0, 'endIndex': 16}, 'groundingChunkIndices': [0]}],
                    'searchEntryPoint': {'renderedContent': '<div>Google Search suggestions</div>'}}}]}
        ]

    def send(self, text='What changed today?'):
        return self.client.post('/api/chat', json={'chat_id': self.chat, 'message': text, 'timezone': 'Asia/Kolkata'})

    @patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only'})
    @patch('app.requests.post')
    def test_stream_only_search_citations_and_persistent_memory(self, post):
        post.return_value = FakeUpstream(self.events())
        response = self.send('I prefer Python.')
        events = [json.loads(line) for line in response.data.splitlines()]
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['sources'][0]['url'], 'https://example.com')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['tools'], [{'google_search': {}}])
        self.assertNotIn('Authorization', post.call_args.kwargs['headers'])
        self.assertEqual(post.call_args.kwargs['headers']['x-goog-api-key'], 'test-only')
        self.assertNotIn('test-only', post.call_args.args[0])
        self.assertTrue(post.call_args.args[0].endswith(':streamGenerateContent'))
        saved = self.client.get('/api/chats/' + self.chat).json['messages']
        self.assertEqual([m['role'] for m in saved], ['user', 'assistant'])
        self.assertTrue(saved[0]['created_at'].endswith('+00:00'))
        self.assertIn(saved[0]['created_at'], payload['contents'][0]['parts'][0]['text'])
        self.assertEqual(saved[1]['content'], 'Verified answer.')
        self.assertIn('Google Search suggestions', saved[1]['search_suggestions'])
        new_chat = self.client.post('/api/chats').json['id']
        self.chat = new_chat
        post.return_value = FakeUpstream(self.events())
        self.send('What language do I prefer?').get_data()
        self.assertIn('I prefer Python.', post.call_args.kwargs['json']['systemInstruction']['parts'][0]['text'])
        # Separate app client with the same cookie represents a new browser connection.
        resumed = app.test_client()
        resumed.set_cookie('threadlight', self.client.get_cookie('threadlight').value)
        self.assertEqual(len(resumed.get('/api/chats').json), 2)

    def test_owner_isolation(self):
        other = app.test_client()
        self.assertEqual(other.get('/api/chats').json, [])
        self.assertEqual(other.get('/api/chats/' + self.chat).status_code, 404)
        self.assertEqual(other.delete('/api/chats/' + self.chat).status_code, 404)
        self.assertEqual(other.get('/api/export').json['chats'], [])

    @patch.dict(os.environ, {'GEMINI_API_KEY': ''})
    @patch('app.dotenv_values')
    def test_key_edits_are_loaded_without_restart(self, values):
        values.return_value = {'GEMINI_API_KEY': ''}
        self.assertFalse(self.client.get('/api/status').json['configured'])
        values.return_value = {'GEMINI_API_KEY': 'local-secret'}
        response = self.client.get('/api/status')
        self.assertTrue(response.json['configured'])
        self.assertEqual(response.json['provider'], 'gemini')
        self.assertNotIn('local-secret', response.text)

    def test_validation_and_origin(self):
        for value in ['', 'x' * 8001, 123, ['bad']]:
            self.assertEqual(self.send(value).status_code, 400)
        self.assertEqual(self.client.post('/api/chats', headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/chat', json=['invalid']).status_code, 400)

    @patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only'})
    @patch('app.requests.post')
    def test_transport_failure_preserves_question(self, post):
        import requests
        post.side_effect = requests.Timeout('test timeout')
        self.assertIn('could not be reached', self.send().get_data().decode())
        self.assertEqual(len(self.client.get('/api/chats/' + self.chat).json['messages']), 1)
        self.assertFalse(locks)

    @patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only'})
    @patch('app.requests.post')
    def test_upstream_failure_does_not_expose_provider_body(self, post):
        upstream = FakeUpstream([])
        upstream.ok = False
        upstream.status_code = 429
        post.return_value = upstream
        result = self.send().get_data().decode()
        self.assertIn('HTTP 429', result)
        self.assertNotIn('test-only', result)
        self.assertFalse(locks)

    @patch.dict(os.environ, {'GEMINI_API_KEY': ''})
    def test_missing_key_does_not_fake_answer(self):
        self.assertEqual(self.send().status_code, 503)
        self.assertEqual(self.client.get('/api/chats/' + self.chat).json['messages'], [])

    @patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only'})
    @patch('app.requests.post')
    def test_incomplete_reply_not_saved_and_lock_released(self, post):
        post.return_value = FakeUpstream([{'candidates': [{'content': {'parts': [{'text': 'Partial'}]}}]}])
        result = self.send().get_data().decode()
        self.assertIn('before completion', result)
        saved = self.client.get('/api/chats/' + self.chat).json['messages']
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['role'], 'user')
        self.assertFalse(locks)

    def test_grounding_byte_offsets_unicode_and_unsafe_sources(self):
        text = 'Café 🌍 is here.'
        metadata = {
            'groundingChunks': [{'web': {'uri': 'https://example.com', 'title': 'Source'}},
                                {'web': {'uri': 'javascript:alert(1)', 'title': 'Unsafe'}}],
            'groundingSupports': [{'segment': {'startIndex': 0, 'endIndex': len('Café 🌍'.encode('utf-8'))},
                                   'groundingChunkIndices': [0, 1, 99, -1]}]}
        result = grounding_sources(text, metadata)
        self.assertEqual(len(result), 1)
        self.assertEqual(text[result[0]['start_index']:result[0]['end_index']], 'Café 🌍')

    @patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only'})
    @patch('app.requests.post')
    def test_gemini_safety_and_token_limit_are_not_saved_as_answers(self, post):
        for ending in [{'promptFeedback': {'blockReason': 'SAFETY'}},
                       {'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'parts': [{'text': 'Truncated'}]}}]}]:
            post.return_value = FakeUpstream([ending])
            events = [json.loads(line) for line in self.send().get_data().splitlines()]
            self.assertEqual(events[-1]['type'], 'error')
        saved = self.client.get('/api/chats/' + self.chat).json['messages']
        self.assertTrue(all(m['role'] == 'user' for m in saved))
        self.assertFalse(locks)

    @patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only'})
    @patch('app.requests.post')
    def test_thought_parts_are_not_exposed(self, post):
        post.return_value = FakeUpstream([{'candidates': [{'content': {'parts': [
            {'text': 'Hidden reasoning', 'thought': True}, {'text': 'Public answer'}]}, 'finishReason': 'STOP'}]}])
        result = self.send().get_data().decode()
        self.assertIn('Public answer', result)
        self.assertNotIn('Hidden reasoning', result)

    @patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only'})
    @patch('app.requests.post')
    def test_delete_removes_memories_and_export(self, post):
        post.return_value = FakeUpstream(self.events())
        self.send('Remember my preference.').get_data()
        self.assertEqual(len(self.client.get('/api/export').json['chats'][0]['messages']), 2)
        self.assertEqual(self.client.delete('/api/chats/' + self.chat).status_code, 200)
        self.assertEqual(self.client.get('/api/export').json['chats'], [])
        with app.app_context():
            self.assertEqual(db().execute('SELECT COUNT(*) FROM messages').fetchone()[0], 0)

    def test_assets_and_cookie_security(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Threadlight', response.text)
        self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
        response.close()
        other = app.test_client()
        cookie = other.get('/api/status').headers['Set-Cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)


if __name__ == '__main__':
    unittest.main()
