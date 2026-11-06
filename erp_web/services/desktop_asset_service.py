"""本机素材交接；复用图片服务建立后端持久引用。"""
import base64
from io import BytesIO
from PIL import Image
from . import image_service


def save_collected_asset(app_dir, asset):
    encoded = asset.get('data', '')
    if not isinstance(encoded, str) or len(encoded) > 28 * 1024 * 1024:
        raise ValueError('采集图片超过 20 MB 上限。')
    raw = base64.b64decode(encoded, validate=True)
    with Image.open(BytesIO(raw)) as image:
        mime = Image.MIME.get(image.format, '')
        image.verify()
    if mime not in {'image/png', 'image/jpeg', 'image/webp'}:
        raise ValueError('采集图片格式无效。')
    items = image_service.upload_images(app_dir, [{'data_url': f'data:{mime};base64,{encoded}', 'name': 'collected'}])
    if not items:
        raise ValueError('采集图片未保存。')
    return {**items[0], 'url': asset['url']}
