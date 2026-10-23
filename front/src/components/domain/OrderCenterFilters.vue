<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useOrderNotificationsStore } from '@/stores/orderNotifications'
import { emptyOrderCenterFilters, orderPlatformNames } from '@/types/orders'
import type { OrderCenterFilters } from '@/types/orders'

const store = useOrderNotificationsStore()
const searchText = ref(store.query)
const draft = reactive({ ...emptyOrderCenterFilters(), ...store.filters })
const appliedCount = computed(() => Object.entries(store.filters).filter(([key, value]) => key !== 'time_zone' && !!value).length)

function search() {
  store.query = searchText.value.trim()
  store.filters = Object.fromEntries(Object.entries(draft)
    .filter(([, value]) => value.trim())
    .map(([key, value]) => [key, value.trim()])) as OrderCenterFilters
  void store.filter(store.platform, store.state)
}
function reset() {
  searchText.value = ''
  Object.assign(draft, emptyOrderCenterFilters())
  delete draft.time_zone
  store.query = ''
  store.filters = {}
  void store.filter('', '')
}
</script>

<template>
  <form class="order-center-filters" aria-label="订单搜索与筛选" @submit.prevent="search">
    <div class="search-row">
      <input
        v-model="searchText" class="order-input keyword" type="search"
        aria-label="搜索订单号、采购单号、运单号、报单编号、SKU、商品或备注"
        placeholder="订单号 / 采购单号 / 运单号 / SKU / 备注"
      />
      <button class="order-button order-primary" type="submit">搜索</button>
      <button class="order-button" type="button" @click="reset">清空筛选</button>
      <select
        class="order-input platform" aria-label="平台筛选" :value="store.platform"
        @change="store.filter(($event.target as HTMLSelectElement).value, store.state)"
      >
        <option value="">全部平台</option>
        <option v-for="(label, key) in orderPlatformNames" :key="key" :value="key">{{ label }}</option>
      </select>
      <span class="order-muted result-count">{{ store.page.total }} 个订单<span v-if="appliedCount"> · 已筛选 {{ appliedCount }} 项</span></span>
    </div>
    <div class="filter-grid common-filters">
      <label>采购进度
        <select v-model="draft.procurement_status" class="order-input" aria-label="采购进度筛选">
          <option value="">全部采购进度</option><option value="unpurchased">未登记采购</option>
          <option value="partial">部分登记</option><option value="purchased">已登记采购</option><option value="unknown">采购待核对</option>
        </select>
      </label>
      <label>报单进度
        <select v-model="draft.declaration_status" class="order-input" aria-label="报单进度筛选">
          <option value="">全部报单进度</option><option value="unsubmitted">未报单</option><option value="submitting">提交中</option>
          <option value="unknown">结果待核实</option><option value="created">已报单</option><option value="error">报单异常</option><option value="cancelled">报单已取消</option>
        </select>
      </label>
      <label>发货截止日期
        <select v-model="draft.deadline_status" class="order-input" aria-label="发货截止日期筛选">
          <option value="">全部发货日期</option><option value="today">今天需发货</option>
          <option value="tomorrow">明天需发货</option><option value="overdue">已超时未发货</option>
        </select>
      </label>
    </div>
    <details class="advanced-filters">
      <summary>更多筛选</summary>
      <div class="filter-grid">
        <label>仓库进度
          <select v-model="draft.warehouse_status" class="order-input" aria-label="仓库进度筛选">
            <option value="">全部仓库进度</option><option value="FULFILLMENT_CREATED">预报已创建</option>
            <option value="WAITING_DOMESTIC_SHIPMENT">等待国内到货</option><option value="WAREHOUSE_RECEIVED">仓库已收货</option>
            <option value="PACKING">打包中</option><option value="SHIPPED">仓库已发货</option>
            <option value="COMPLETED">履约状态待核对</option><option value="EXCEPTION">仓库异常</option><option value="CANCELLED">已取消</option>
          </select>
        </label>
        <label>国内运单
          <select v-model="draft.has_waybill" class="order-input" aria-label="国内运单筛选">
            <option value="">不限有无运单</option><option value="yes">有国内运单</option><option value="no">无国内运单</option>
          </select>
        </label>
        <label>国内物流状态
          <select v-model="draft.domestic_status" class="order-input" aria-label="国内物流状态筛选">
            <option value="">全部物流状态</option><option value="waiting">等待发货或揽收</option>
            <option value="in_transit">有包裹运输中</option><option value="signed">全部包裹已签收</option>
            <option value="unsigned">有包裹未签收</option><option value="unknown">有包裹状态待查询</option>
          </select>
        </label>
        <label>国内快递公司<input v-model="draft.domestic_carrier" class="order-input" aria-label="国内快递公司筛选" placeholder="例如：圆通" maxlength="200" /></label>
        <label>收货仓库<input v-model="draft.receiving_warehouse" class="order-input" aria-label="收货仓库筛选" placeholder="仓库或分区名称" maxlength="200" /></label>
        <label>平台物流商<input v-model="draft.platform_carrier" class="order-input" aria-label="平台物流商筛选" placeholder="例如：CEL、UNI" maxlength="200" /></label>
        <label>异常类型
          <select v-model="draft.exception_type" class="order-input" aria-label="异常类型筛选">
            <option value="">不限异常类型</option><option value="any">有异常记录</option><option value="purchase">采购或物流查询异常</option>
            <option value="label">面单获取异常</option><option value="declaration">报单异常或待核实</option><option value="warehouse">仓库异常</option>
          </select>
        </label>
        <label>截止日期从<input v-model="draft.deadline_from" class="order-input" type="date" aria-label="发货截止开始日期" :max="draft.deadline_to || undefined" /></label>
        <label>截止日期至<input v-model="draft.deadline_to" class="order-input" type="date" aria-label="发货截止结束日期" :min="draft.deadline_from || undefined" /></label>
      </div>
      <p class="order-muted">物流和仓库进度以最后同步的记录为准。</p>
      <button class="order-button" type="submit">应用筛选</button>
    </details>
  </form>
</template>

<style scoped>
.order-center-filters { padding: 12px; margin-bottom: 12px; border-radius: 7px; background: var(--order-surface); }
.search-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.keyword { flex: 1 1 300px; min-width: 180px; }
.platform { width: 150px; }
.result-count { margin-left: auto; white-space: nowrap; }
.filter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.filter-grid label { display: flex; flex-direction: column; gap: 4px; color: var(--order-muted); font-size: 12px; }
.filter-grid .order-input { width: 100%; min-width: 0; color: var(--order-text); }
.common-filters { margin-top: 12px; max-width: 750px; }
.advanced-filters { margin-top: 12px; border-top: 1px solid var(--order-border); padding-top: 10px; }
.advanced-filters summary { width: fit-content; cursor: pointer; color: var(--order-link-color); }
.advanced-filters .filter-grid { margin-top: 12px; }
.advanced-filters p { margin: 12px 0 8px; }
</style>
