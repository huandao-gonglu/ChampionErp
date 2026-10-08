<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { orderPlatformNames, ORDER_PAGE_SIZE } from '@/types/orders'
import type { OrdersPage } from '@/types/orders'
import OrderThumbnail from './OrderThumbnail.vue'
import {
  amountLabel,
  deadline,
  money,
  platformStatusLabel,
  platformStatusNote,
  orderProcessingProgress,
  stateTone,
} from './orderPresentation'
const props = defineProps<{ page: OrdersPage; offset: number; now: number; error: string }>()
defineEmits<{ page: [offset: number] }>()
const route = useRoute()
const currentPage = computed(() => Math.floor(props.offset / ORDER_PAGE_SIZE) + 1)
const rows = computed(() => props.page.items.map(order => ({ order, progress: orderProcessingProgress(order) })))
const pageCount = computed(() => Math.max(1, Math.ceil(props.page.total / ORDER_PAGE_SIZE)))
</script>
<template>
  <div class="order-table-card">
    <div class="order-table-scroll">
      <table class="order-table">
        <colgroup>
          <col />
          <col style="width: 104px" />
          <col style="width: 104px" />
          <col style="width: 156px" />
          <col style="width: 216px" />
          <col style="width: 144px" />
          <col style="width: 80px" />
        </colgroup>
        <thead>
          <tr>
            <th>订单 / 商品</th>
            <th>平台 / 履约</th>
            <th class="order-numeric">金额</th>
            <th>平台状态</th>
            <th>ERP 处理进度</th>
            <th>发货日期 / 截止</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="{ order, progress } in rows" :key="order.id">
            <td>
              <div class="order-product-cell">
                <OrderThumbnail :src="order.items[0]?.image_url" :title="order.items[0]?.title || order.title || '商品'" />
                <div class="order-product-copy">
                  <div class="order-item-meta">
                    <span>{{ order.order_id }}</span><span class="order-muted" :title="order.items.map((item) => item.sku).join(' / ')">{{
                      order.items.length > 1
                        ? `${order.items.length} 个 SKU`
                        : order.items[0]?.sku || 'SKU 未提供'
                    }}
                      ·
                      {{ order.items.reduce((n, item) => n + item.quantity, 0) }}
                      件</span>
                  </div>
                  <p class="order-title" :title="order.title">
                    {{ order.title || '暂无商品标题' }}
                  </p>
                </div>
              </div>
            </td>
            <td>
              <p class="order-cell-primary">{{ orderPlatformNames[order.platform] }}</p>
              <p class="order-muted">{{ order.delivery?.fulfillment_model || order.fulfillment || '未提供' }}</p>
            </td>
            <td class="order-numeric" :title="amountLabel(order, order.platform)">
              <p class="order-money">{{ money(order.amount) }}</p>
              <p class="order-muted">{{ order.currency }}</p>
            </td>
            <td>
              <span
                class="order-badge"
                :data-tone="stateTone(order.state)"
                :title="`${order.status} ${order.shipping_status}`"
              >{{ platformStatusLabel(order) }}</span>
              <p class="order-muted" :title="platformStatusNote(order)">来自平台同步</p>
            </td>
            <td>
              <span class="order-badge" :data-tone="progress.tone" :title="progress.note">{{ progress.label }}</span>
              <p class="order-muted" :title="progress.source">{{ progress.source }}</p>
            </td>
            <td>
              <template v-if="order.state === 'pending_shipment'">
                <p
                  class="order-cell-primary"
                  :class="{
                    'order-urgent': deadline(order.shipment_deadline, now).urgent,
                  }"
                >
                  {{ deadline(order.shipment_deadline, now).text }}
                </p>
                <p class="order-muted">
                  {{ deadline(order.shipment_deadline, now).note }}
                </p>
              </template><span v-else class="order-muted">—</span>
            </td>
            <td>
              <RouterLink
                class="order-link"
                :aria-label="`查看订单 ${order.order_id}`"
                :to="{
                  path: '/',
                  query: { ...route.query, tab: 'orders', order: order.id },
                }"
              >
                详情
              </RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="!page.items.length" class="order-empty order-muted">
        {{ error ? '订单读取失败，请刷新重试。' : '没有符合条件的订单，可调整筛选或同步订单。' }}
      </p>
    </div>
    <footer class="order-row order-pagination">
      <span class="order-muted">显示 {{ page.total ? offset + 1 : 0 }}–{{ offset + page.items.length }} 条，共
        {{ page.total }} 个订单</span>
      <div class="order-actions">
        <span class="order-muted">{{ currentPage }} / {{ pageCount }}</span><button
          class="order-button"
          :disabled="offset === 0"
          @click="$emit('page', Math.max(0, offset - ORDER_PAGE_SIZE))"
        >
          上一页
        </button><button
          class="order-button"
          :disabled="offset + ORDER_PAGE_SIZE >= page.total"
          @click="$emit('page', offset + ORDER_PAGE_SIZE)"
        >
          下一页
        </button>
      </div>
    </footer>
  </div>
</template>
<style scoped>
.order-table-card {
  min-width: 0;
  border-radius: 7px;
  overflow: hidden;
  background: var(--order-surface);
}
.order-table-scroll {
  overflow-x: auto;
}
.order-table {
  width: 100%;
  min-width: 1040px;
  border-collapse: collapse;
  table-layout: fixed;
  text-align: left;
}
.order-table th {
  height: 40px;
  padding: 0 16px;
  color: var(--order-muted);
  font-size: 12px;
  font-weight: 500;
  background: var(--order-soft);
}
.order-table tbody tr {
  border-bottom: 1px solid var(--order-border);
}
.order-table td {
  padding: 14px 16px;
  vertical-align: middle;
}
.order-table p {
  margin: 0;
  line-height: 20px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.order-item-meta {
  display: flex;
  gap: 12px;
  line-height: 26px;
  font-size: 12px;
}
.order-product-cell { display: flex; align-items: center; gap: 10px; }
.order-product-copy { min-width: 0; flex: 1; }
.order-item-meta span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.order-title {
  font-weight: 500;
}
.order-numeric {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.order-money {
  font-weight: 600;
}
.order-table .order-badge {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  line-height: 20px;
  vertical-align: top;
}
.order-table td > p.order-muted { margin-top: 6px; min-height: 18px; font-size: 12px; line-height: 18px; }
.order-table p.order-cell-primary, .order-table p.order-money { line-height: 26px; }
.order-table p.order-title { margin-top: 6px; font-size: 12px; line-height: 18px; }
.order-table .order-link { display: inline-flex; align-items: center; min-height: 32px; }
.order-item-meta > span:first-child { flex-shrink: 0; font-size: 14px; font-weight: 500; }
.order-urgent {
  color: #b45309;
}
.order-pagination {
  min-height: 46px;
  padding: 7px 16px;
}
.order-pagination .order-button {
  min-height: 30px;
  padding: 4px 12px;
}
.order-empty {
  padding: 64px 16px;
  text-align: center;
}
</style>
