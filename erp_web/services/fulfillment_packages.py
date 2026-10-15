"""将已校验的商品分配转换为跨境巴士的实际国内包裹，不读写业务状态。"""


def domestic_waybill_key(tracking_number: str) -> str:
    return tracking_number.strip().upper()


def group_domestic_packages(items: list[dict]) -> list[dict]:
    """同一运单只发送一条包裹，数量仍为商品总件数，商品明细逐条保留。"""
    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(domestic_waybill_key(item['logistics_order']), []).append(item)

    packages = []
    for waybill, lines in groups.items():
        package = {**lines[0], 'logistics_order': waybill}
        if len(lines) > 1:
            package['num'] = sum(line['num'] for line in lines)
            package['img'] = next((line['img'] for line in lines if line['img']), '')
            descriptions = []
            for line in lines:
                description = f"{line['describe']} · 数量：{line['num']} 件"
                if line.get('from_platform'):
                    platform = {1: '1688', 2: '淘宝', 3: '拼多多'}[line['from_platform']]
                    description += f" · 采购平台：{platform} · 采购单号：{line['from_order']}"
                if line['img']:
                    description += f" · 商品原图：{line['img']}"
                descriptions.append(description)
            package['describe'] = '\n'.join(descriptions)
            # 协议只有单值来源字段；混合来源保留在明细中，不伪造拼接采购单号。
            for field in ('from_platform', 'from_order'):
                if any(line.get(field) != lines[0].get(field) for line in lines):
                    package.pop(field, None)
            if 'from_platform' not in package:
                package.pop('from_order', None)
        packages.append(package)
    return packages
