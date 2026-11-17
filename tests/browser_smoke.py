"""Optional browser smoke: run app.py first, then python tests/browser_smoke.py.
Install requirements-dev.txt and run python -m playwright install chromium.
Uses mocked completion events, never a live model call.
"""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright


with sync_playwright() as p:
    browser = p.chromium.launch(channel=os.getenv('BROWSER_CHANNEL') or None)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(os.getenv('BASE_URL', 'http://127.0.0.1:8000'))
    page.get_by_role('heading', name='Where were we?').wait_for()
    Path('docs').mkdir(exist_ok=True)
    page.screenshot(path='docs/preview.png', full_page=True, animations='disabled')
    page.get_by_role('button', name='New conversation').click()
    page.locator('#delete').wait_for(state='visible')
    page.locator('#question').fill('A browser smoke test')
    page.get_by_role('button', name='Send message').click()
    page.get_by_role('status').filter(has_text='Add GEMINI_API_KEY').wait_for()
    assert page.locator('#question').input_value() == 'A browser smoke test'
    text = 'Verified fact [1].'
    events = [
        {'type': 'status', 'message': 'Searching the web'},
        {'type': 'delta', 'text': text},
        {'type': 'done', 'text': text, 'created_at': '2026-11-17T10:00:00+00:00', 'elapsed': 0.8,
         'sources': [{'url': 'https://example.com', 'title': 'Example source', 'start_index': 14, 'end_index': 17}],
         'search_suggestions': '<style>body{color:green}</style><p>Google Search suggestions</p>'}]
    page.route('**/api/chat', lambda route: route.fulfill(
        status=200, content_type='application/x-ndjson', body='\n'.join(json.dumps(e) for e in events)+'\n'))
    page.get_by_role('button', name='Send message').click()
    page.locator('.message.assistant .sources a').wait_for()
    assert page.locator('.message.assistant .content a').get_attribute('href') == 'https://example.com/'
    assert page.locator('.message.assistant .content').text_content() == text
    frame = page.locator('iframe.search-suggestions')
    assert frame.get_attribute('sandbox') == 'allow-popups allow-popups-to-escape-sandbox'
    page.frame_locator('iframe.search-suggestions').get_by_text('Google Search suggestions').wait_for()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert page.locator('#send').is_visible()
    assert not errors, errors
    browser.close()
    print('Browser smoke passed: setup error, message stream, inline sources, mobile layout, no JS errors.')
