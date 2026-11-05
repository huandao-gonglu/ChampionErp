"""在线商品操作范围只取自已核验的刊登关系。"""


def yandex_campaign_id(listing, scope):
    if scope.startswith('campaign:'):
        campaign = scope.removeprefix('campaign:')
        if campaign and any(market.id == campaign for market in listing.markets):
            return campaign
        raise ValueError('所选仓库的店铺不属于该商品，请重新读取详情')
    if scope in ('', 'global', 'campaign'):
        # 已持久化的旧任务使用授权店铺范围，继续按原目标执行。
        return listing.account_id.split(':', 1)[1]
    raise ValueError('请选择有效的仓库店铺范围')
