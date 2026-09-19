import os

os.environ['DISABLE_CACHE_PREWARM'] = '1'
from app import app


def test_brand_images_are_small_public_and_support_revalidation():
    client = app.test_client()
    for path in ('/static/logo-t2-v2.webp', '/static/favicon-t2-v2.png', '/favicon.ico'):
        response = client.get(path)
        assert response.status_code == 200
        assert len(response.data) < 10000
        assert response.headers['Cache-Control'].startswith('public, max-age=')
        cached = client.get(path, headers={'If-None-Match': response.headers['ETag']})
        assert cached.status_code == 304
        assert cached.headers['Cache-Control'] == response.headers['Cache-Control']
    assert client.get('/favicon.ico').data[:4] == b'\x00\x00\x01\x00'


def test_private_pages_do_not_inherit_asset_cache_policy():
    response = app.test_client().get('/login')
    assert 'public' not in response.headers['Cache-Control']
    assert b'logo-t2-v2.webp' in response.data
    assert b'/static/logot2.png' not in response.data
