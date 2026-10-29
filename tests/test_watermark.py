"""水印合成、设置入口及多平台发布副本的回归。"""
import base64
import io
from urllib.parse import urlsplit

from PIL import Image, ImageChops
import pytest

from erp_web.context import get_context
from erp_web.http_route_units import auth_config_routes
from erp_web.schemas.config import WatermarkSettingsRequest
from erp_web.schemas.image_hosting import ImageHostingError
from erp_web.services.image_content import image_content
from erp_web.services.watermark_service import apply_watermark, decode_image_upload, preview_watermark, validate_watermark
from tests.image_hosting_test_utils import FakeS3, configure
from tests.test_image_hosting_routes import _Handler


def image_bytes(color='white', size=(320, 240), mode='RGB'):
    stream = io.BytesIO()
    Image.new(mode, size, color).save(stream, format='PNG')
    return stream.getvalue()


def settings(**updates):
    return WatermarkSettingsRequest.model_validate({'text': '品牌水印 Brand', **updates}).model_dump()


def data_url(data):
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


def product(tmp_path, platform='ozon'):
    main, sku = tmp_path / 'main.png', tmp_path / 'sku.png'
    main.write_bytes(image_bytes())
    sku.write_bytes(image_bytes('#cbd5e1'))
    return {'name': '水印商品', 'sku_items': [{'id': 'sku-1', 'image_asset_id': 'sku'}],
            'source': {'image_pool': [{'id': 'main', 'path': str(main)}, {'id': 'sku', 'path': str(sku)},
                                     {'id': 'unused', 'path': str(tmp_path / 'unused.png')}]},
            'drafts': {platform: {'images': [{'asset_id': 'main', 'role': 'main', 'order': 0}],
                                 'sku_items': [{'sku_id': 'sku-1', 'selected': True}]}}}


def post(path, body):
    handler = _Handler(path, body)
    assert auth_config_routes.handle_post(handler, urlsplit(path))
    return handler.response


def test_settings_partial_updates_upload_validation_and_preview_do_not_save(tmp_path):
    get_context().products.save_product(product(tmp_path))
    config = get_context().config
    assert post('/api/system-settings', {'watermark': settings()})[1] == 200
    config.save_system_settings({'orders_auto_sync_interval_hours': 12})
    assert config.system_settings()['watermark'] == settings()
    before = config.load_app_config()
    result, status = post('/api/system-settings/watermark-preview', {'watermark': settings(text='预览')})
    assert status == 200 and result['preview_url'].startswith('data:image/png;base64,')
    assert config.load_app_config() == before
    for watermark in (settings(image_data_url='broken'),
                      {**settings(), 'opacity': 101}, {**settings(), 'density': 0}):
        result, status = post('/api/system-settings', {'watermark': watermark})
        assert status == 400 and result['error']
        assert config.load_app_config() == before
    logo = data_url(image_bytes((255, 0, 0, 128), (40, 20), 'RGBA'))
    result, status = post('/api/system-settings', {'watermark': settings(image_data_url=logo)})
    assert status == 200 and result['orders_auto_sync_interval_hours'] == 12
    assert result['watermark']['image_data_url'] == logo


def test_text_chinese_tiling_preview_and_empty_passthrough():
    raw = image_bytes()
    config = settings()
    output = apply_watermark(raw, config)
    assert output != raw
    before, after = Image.open(io.BytesIO(raw)), Image.open(io.BytesIO(output)).convert('RGB')
    bounds = ImageChops.difference(before, after).getbbox()
    assert bounds and bounds[0] < 100 and bounds[1] < 80 and bounds[2] > 220 and bounds[3] > 160
    assert decode_image_upload(preview_watermark(config, raw)) == output
    assert apply_watermark(raw, settings(text='')) == raw
    assert apply_watermark(raw, config) == output


def test_transparent_logo_opacity_and_small_images():
    logo = data_url(image_bytes((255, 0, 0, 128), (20, 20), 'RGBA'))
    config = settings(text='', image_data_url=logo, opacity=50)
    output = Image.open(io.BytesIO(apply_watermark(image_bytes(), config)))
    assert output.getpixel((160, 120))[:3] == (255, 191, 191)
    assert apply_watermark(image_bytes(size=(1, 1)), config)


def test_density_size_and_combined_content_change_rendered_output():
    raw = image_bytes(size=(900, 600))
    def pixels(**values):
        return Image.open(io.BytesIO(apply_watermark(raw, settings(**values)))).convert('RGB')
    sparse, dense = pixels(density=1), pixels(density=10)
    def values(image):
        data = image.tobytes()
        return zip(data[0::3], data[1::3], data[2::3])
    def changed_count(image):
        return sum(pixel != (255, 255, 255) for pixel in values(image))
    assert changed_count(dense) > changed_count(sparse) * 2
    assert pixels(size_percent=40).tobytes() != pixels(size_percent=15).tobytes()
    logo = data_url(image_bytes((255, 0, 0, 255), (40, 20), 'RGBA'))
    both = pixels(image_data_url=logo)
    assert both.tobytes() != pixels().tobytes()
    assert both.tobytes() != pixels(text='', image_data_url=logo).tobytes()
    assert any(r > g + 10 and r > b + 10 for r, g, b in values(both))
    assert any(r == g == b and r < 255 for r, g, b in values(both))


def test_previous_config_keeps_both_contents_and_drops_retired_fields():
    from erp_web import app_config
    config = app_config.default_app_config()
    config['system_settings']['watermark'] = {
        'enabled': False, 'kind': 'text', 'position': 'bottom_right', 'margin_percent': 3,
        'text': '旧文字', 'image_data_url': data_url(image_bytes()), 'opacity': 25, 'size_percent': 30, 'color': '#737373',
    }
    current = app_config.normalize_app_config(config)['system_settings']['watermark']
    assert current['text'] == '旧文字' and current['image_data_url']
    assert not set(current) & {'enabled', 'kind', 'position', 'margin_percent'}
    assert current['density'] == 5
    assert apply_watermark(image_bytes(), current) != image_bytes()


def test_bad_and_oversized_uploads_are_rejected():
    for value in ('data:image/svg+xml;base64,AAAA', 'data:image/png;base64,!', data_url(b'not an image')):
        with pytest.raises(ValueError):
            decode_image_upload(value)
    with pytest.raises(ValueError, match='大小限制'):
        decode_image_upload(data_url(b'a' * 30), maximum=20)
    with pytest.raises(ValueError, match='400 万'):
        validate_watermark(settings(image_data_url=data_url(image_bytes(size=(2001, 2000)))))


def test_every_selected_image_is_watermarked_reused_and_reverted(tmp_path, monkeypatch):
    context = get_context()
    configure(context)
    remote = FakeS3(monkeypatch)
    context.config.save_system_settings({'watermark': settings()})
    original = product(tmp_path)
    prepared = context.image_delivery.prepare_product(original, 'ozon')
    assert len(remote.uploads) == 2
    assert not context.image_delivery.inspect_product(prepared, 'ozon')
    assert all(item.get('watermark_fingerprint') for item in prepared['source']['image_pool'][:2])
    assert prepared['source']['image_pool'][2].get('content_sha256', '') == ''
    assert (tmp_path / 'main.png').read_bytes() == image_bytes()
    assert context.image_delivery.prepare_product(prepared, 'ozon') == prepared
    assert len(remote.uploads) == 2
    context.config.save_system_settings({'watermark': settings(text='新水印')})
    assert {issue.code for issue in context.image_delivery.inspect_product(prepared, 'ozon')} == {'IMAGE_DELIVERY_STALE'}
    changed = context.image_delivery.prepare_product(prepared, 'ozon')
    assert len(remote.uploads) == 4
    context.config.save_system_settings({'watermark': settings(text='')})
    assert context.image_delivery.inspect_product(changed, 'ozon')
    restored = context.image_delivery.prepare_product(changed, 'ozon')
    assert not context.image_delivery.inspect_product(restored, 'ozon')
    assert restored['source']['image_pool'][0]['content_sha256'] == image_content(image_bytes())[0]
    assert not original['source']['image_pool'][0].get('url')


def test_setting_changes_during_upload_reject_delivery(tmp_path, monkeypatch):
    context = get_context()
    configure(context)
    context.config.save_system_settings({'watermark': settings()})
    remote = FakeS3(monkeypatch)
    remote.on_put = lambda: context.config.save_system_settings({'watermark': settings(text='已变更')})
    with pytest.raises(ImageHostingError, match='设置已变化'):
        context.image_delivery.prepare_product(product(tmp_path), 'ozon')


def test_external_source_reuses_original_url_and_never_downloads_marked_copy(monkeypatch):
    from erp_web.services import watermark_assets
    from erp_web.services.external_request_manager import BufferedResponse
    context = get_context()
    configure(context)
    remote = FakeS3(monkeypatch)
    context.config.save_system_settings({'watermark': settings()})
    urls = []
    def download(request, **kwargs):
        urls.append(request.full_url)
        return BufferedResponse(image_bytes(), headers={'Content-Type': 'image/png'}, status=200, url=request.full_url)
    monkeypatch.setattr(watermark_assets, 'managed_urlopen', download)
    original_url = 'https://supplier.example.test/original.png'
    original = {'source': {'image_pool': [{'id': 'a', 'url': original_url}]}, 'drafts': {'ozon': {'images': [{'asset_id': 'a'}]}}}
    prepared = context.image_delivery.prepare_product(original, 'ozon')
    assert not context.image_delivery.inspect_product(prepared, 'ozon')
    again = context.image_delivery.prepare_product(prepared, 'ozon')
    assert prepared == again and len(remote.uploads) == 1
    assert urls == [original_url, original_url]
    context.config.save_system_settings({'watermark': settings(text='')})
    restored = context.image_delivery.prepare_product(prepared, 'ozon')
    assert urls[-1] == original_url
    assert restored['source']['image_pool'][0]['content_sha256'] == image_content(image_bytes())[0]


def test_watermark_delivery_persists_source_and_output_identities(tmp_path, monkeypatch):
    from erp_web.runtime_units.image_delivery_persistence import persist_delivered_image_pool
    context = get_context()
    configure(context)
    FakeS3(monkeypatch)
    context.config.save_system_settings({'watermark': settings()})
    saved = context.products.save_product(product(tmp_path))
    prepared = context.image_delivery.prepare_product(saved, 'ozon')
    persisted = persist_delivered_image_pool(prepared, saved, 'ozon')
    assert not context.image_delivery.inspect_product(persisted, 'ozon')
    loaded = context.products.load_product_from_index(saved['product_id'], '')
    assert loaded['source']['image_pool'][0]['watermark_fingerprint']
    assert loaded['source']['image_pool'][0]['delivery_source_sha256'] == image_content(image_bytes())[0]


def test_mercado_uploads_all_selected_images_and_invalidates_old_picture_ids(tmp_path, monkeypatch):
    from erp_web.runtime_units import publish_mercadolibre
    context = get_context()
    context.config.save_system_settings({'watermark': settings()})
    uploads = []
    def upload(path, token):
        uploads.append(path.read_bytes())
        return {'id': f'picture-{len(uploads)}'}
    monkeypatch.setattr(publish_mercadolibre.publisher, 'upload_mercadolibre_picture', upload)
    original = context.products.save_product(product(tmp_path, 'mercadolibre'))
    first = publish_mercadolibre.ensure_mercadolibre_pictures_uploaded(original, 'test')
    assert first['ok'] and len(uploads) == 2
    assert uploads[0] == apply_watermark(image_bytes(), settings())
    assert not context.image_delivery.inspect_mercado_watermarks(first['product'])
    again = publish_mercadolibre.ensure_mercadolibre_pictures_uploaded(first['product'], 'test')
    assert again['ok'] and len(uploads) == 2
    context.config.save_system_settings({'watermark': settings(text='')})
    assert context.image_delivery.inspect_mercado_watermarks(again['product'])
    assert not context.image_delivery.inspect_mercado_watermarks(again['product'], stage='source')
    reverted = publish_mercadolibre.ensure_mercadolibre_pictures_uploaded(again['product'], 'test')
    assert reverted['ok'] and len(uploads) == 4
    assert uploads[2] == image_bytes()
    assert not context.image_delivery.inspect_mercado_watermarks(reverted['product'])


@pytest.mark.parametrize('kind', ['JPEG', 'WEBP'])
def test_output_preserves_photo_format_and_corrects_exif_orientation(kind):
    image = Image.new('RGB', (120, 80), '#abcdef')
    exif = Image.Exif()
    exif[274] = 6
    source = io.BytesIO()
    image.save(source, format=kind, exif=exif)
    result = apply_watermark(source.getvalue(), settings())
    with Image.open(io.BytesIO(result)) as rendered:
        assert rendered.format == kind
        assert rendered.size == (80, 120)
        assert not rendered.getexif().get(274)
    preview = preview_watermark(settings(), source.getvalue())
    assert preview.startswith('data:image/' + ('jpeg' if kind == 'JPEG' else 'webp') + ';base64,')


def test_online_mercado_new_assets_use_same_renderer_and_cleanup(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from erp_web.services.online_product_images import OnlineProductImages
    context = get_context()
    context.config.save_system_settings({'watermark': settings()})
    path = tmp_path / 'online.png'
    path.write_bytes(image_bytes())
    images = OnlineProductImages(context)
    monkeypatch.setattr(images, 'validate', lambda *_: ({}, {}, {'a': {'id': 'a', 'path': str(path)}}))
    uploaded_paths = []
    def prepare(pictures, assets):
        upload_path = type(path)(assets['a']['path'])
        uploaded_paths.append(upload_path)
        assert upload_path.read_bytes() == apply_watermark(image_bytes(), settings())
        return [pictures[0], {'id': 'new'}]
    listing = SimpleNamespace(platform='mercadolibre')
    result = images.prepare(listing, [{'id': 'existing'}, {'asset_id': 'a'}], SimpleNamespace(prepare_pictures=prepare))
    assert result == [{'id': 'existing'}, {'id': 'new'}]
    assert path.read_bytes() == image_bytes()
    assert not uploaded_paths[0].exists()


def test_persistence_rechecks_watermark_setting_under_config_lock(tmp_path, monkeypatch):
    from erp_web.runtime_units.image_delivery_persistence import persist_delivered_image_pool
    context = get_context()
    configure(context)
    FakeS3(monkeypatch)
    context.config.save_system_settings({'watermark': settings()})
    original = context.products.save_product(product(tmp_path))
    prepared = context.image_delivery.prepare_product(original, 'ozon')
    context.config.save_system_settings({'watermark': settings(text='')})
    with pytest.raises(ImageHostingError, match='水印设置已变化'):
        persist_delivered_image_pool(prepared, original, 'ozon')
    current = context.products.load_product_from_index(original['product_id'], '')
    assert not current['source']['image_pool'][0].get('watermark_fingerprint')


def test_draft_preview_empty_state_and_selected_sku_fallback(tmp_path):
    context = get_context()
    response, status = post('/api/system-settings/watermark-preview', {'watermark': settings()})
    assert status == 200 and not response['preview_url'] and response['message']
    original = product(tmp_path)
    original['drafts']['ozon']['images'] = []
    original['drafts']['ozon']['title'] = 'SKU 底图草稿'
    saved = context.products.save_product(original)
    before = context.db.load_product_model(saved['product_id'])
    response, status = post('/api/system-settings/watermark-preview', {'watermark': settings()})
    assert status == 200 and response['preview_url']
    assert decode_image_upload(response['preview_url']) == apply_watermark((tmp_path / 'sku.png').read_bytes(), settings())
    assert context.db.load_product_model(saved['product_id']) == before
    assert (tmp_path / 'sku.png').read_bytes() == image_bytes('#cbd5e1')
