<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { orderPlatformNames, orderStateNames, ORDER_PAGE_SIZE } from '@/types/orders'
import type { OrdersPage } from '@/types/orders'
import {
  amountLabel,
  deadline,
  money,
  procurementNames,
  procurementTone,
  stateTone,
} from './orderPresentation'
const props = defineProps<{ page: OrdersPage; offset: number; now: number; error: string }>()
defineEmits<{ page: [offset: number] }>()
const route = useRoute()
const currentPage = computed(() => Math.floor(props.offset / ORDER_PAGE_SIZE) + 1)
const pageCount = computed(() => Math.max(1, Math.ceil(props.page.total / ORDER_PAGE_SIZE)))
</script>
<template>
  <div class="order-table-card">
    <div class="order-table-scroll">
      <table class="order-table">
        <colgroup>
          <col style="width: 32%" />
          <col style="width: 12%" />
          <col style="width: 13%" />
          <col style="width: 12%" />
          <col style="width: 10%" />
          <col style="width: 14%" />
          <col style="width: 7%" />
        </colgroup>
        <thead>
          <tr>
            <th>订单 / 商品</th>
            <th>平台 / 履约</th>
            <th class="order-numeric">金额</th>
            <th>平台状态</th>
            <th>采购进度</th>
            <th>发货日期 / 截止</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="order in page.items" :key="order.id">
            <td>
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
            </td>
            <td>
              <p>{{ orderPlatformNames[order.platform] }}</p>
              <p class="order-muted">{{ order.fulfillment || '未提供' }}</p>
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
              >{{ orderStateNames[order.state] }}</span>
            </td>
            <td>
              <span class="order-badge" :data-tone="procurementTone(order.procurement_status)">{{
                procurementNames[order.procurement_status || 'unknown']
              }}</span>
            </td>
            <td>
              <template v-if="order.state === 'pending_shipment'">
                <p
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
          class="order-link"
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
  border-radius: 7px;
  overflow: hidden;
  background: var(--order-surface);
}
.order-table-scroll {
  overflow-x: auto;
}
.order-table {
  width: 100%;
  min-width: 1000px;
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
  height: 60px;
  border-bottom: 1px solid var(--order-border);
}
.order-table td {
  padding: 8px 16px;
}
.order-table p {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.order-item-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}
.order-item-meta span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.order-title {
  margin-top: 3px;
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
  width: 100%;
}
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
