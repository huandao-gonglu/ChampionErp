import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { listAccountUsers, setAccountUserOneBoundQuota, type AccountUser } from '@/api/accounts'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import AccountUsersView from '../AccountUsersView.vue'

vi.mock('@/api/accounts', () => ({
  listAccountUsers: vi.fn(),
  createAccountUser: vi.fn(),
  resetAccountUserPassword: vi.fn(),
  setAccountUserStatus: vi.fn(),
  setAccountUserOneBoundQuota: vi.fn(),
}))

const admin: AccountUser = {
  id: 'admin-erp', email: 'admin@example.test', display_name: '初始管理员', role: 'admin', status: 'enabled', created_at: 0, updated_at: 0,
  onebound_quota: { configured: true, authorized: false, member_id: '', account_name: '', balance: null, shared_user_count: 0 },
}
const buyer: AccountUser = {
  id: 'user-b', email: 'b@example.test', display_name: '采购员乙', role: 'user', status: 'enabled', created_at: 0, updated_at: 0,
  onebound_quota: { configured: true, authorized: true, member_id: 'member-b', account_name: '1688买家乙', balance: 185, shared_user_count: 2 },
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(listAccountUsers).mockResolvedValue({
    items: [admin, buyer],
    page: 1, total: 2, limit: 30,
  })
})

function mountView() {
  return mount(AccountUsersView, {
    global: { stubs: { RouterLink: true, PageHeader: true, Teleport: true } },
  })
}

it('用户管理展示账号、万邦买家额度与账号管理操作', async () => {
  const wrapper = mountView()
  await flushPromises()
  const [adminRow, buyerRow] = wrapper.findAll('tbody tr')
  expect(vi.mocked(listAccountUsers).mock.calls).toEqual([[{ page: 1, q: '' }]])
  expect(wrapper.findAll('th').map(header => header.text())).toEqual(['显示名称', '登录邮箱', '角色', '状态', '万邦额度', '操作'])
  expect(adminRow.text()).toContain('管理员')
  expect(adminRow.text()).toContain('未授权')
  expect(adminRow.findAll('button')).toHaveLength(0)
  expect(buyerRow.text()).toContain('185 次')
  expect(buyerRow.text()).toContain('1688买家乙')
  expect(buyerRow.text()).toContain('2 人共用')
  expect(buyerRow.findAll('button').map(button => button.text())).toEqual(['调整额度', '禁用', '重置密码'])
  expect(wrapper.find('select').exists()).toBe(false)
  wrapper.unmount()
})

it('管理员输入目标余额调整万邦额度并处理 409 冲突，遮罩拖出不关闭、正常遮罩点击关闭', async () => {
  vi.mocked(setAccountUserOneBoundQuota).mockResolvedValueOnce({
    ok: true, user_id: 'user-b', member_id: 'member-b', account_name: '1688买家乙',
    shared_user_count: 2, changed: true, previous_balance: 185, delta: 315, balance: 500,
  })
  const wrapper = mountView()
  await flushPromises()

  // 1) 打开弹窗，验证内容区拖出不关闭、正常遮罩点击关闭。
  await wrapper.findAll('button').find(button => button.text() === '调整额度')!.trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('当前共有 2 个 ERP 账号授权了同一个 1688 买家')
  const dialog = wrapper.get('dialog')
  const pointer = { isPrimary: true, button: 0, pointerId: 1 }
  await wrapper.get('[data-testid="quota-target-input"]').trigger('pointerdown', pointer)
  await dialog.trigger('pointerup', pointer)
  expect(wrapper.findComponent(WorkspaceDialog).props('open')).toBe(true)

  await dialog.trigger('pointerdown', pointer)
  await dialog.trigger('pointerup', pointer)
  await flushPromises()
  expect(wrapper.findComponent(WorkspaceDialog).props('open')).toBe(false)

  // 2) 重新打开并提交目标余额 500 + 备注。
  await wrapper.findAll('button').find(button => button.text() === '调整额度')!.trigger('click')
  await flushPromises()
  await wrapper.get('[data-testid="quota-target-input"]').setValue('500')
  await wrapper.get('[data-testid="quota-note-input"]').setValue('客服补发')
  expect(wrapper.text()).toContain('+315')
  vi.mocked(listAccountUsers).mockResolvedValueOnce({
    items: [admin, { ...buyer, onebound_quota: { ...buyer.onebound_quota!, balance: 500 } }],
    page: 1, total: 2, limit: 30,
  })
  await wrapper.get('dialog form').trigger('submit')
  await flushPromises()
  expect(setAccountUserOneBoundQuota).toHaveBeenCalledWith({
    user_id: 'user-b', target_balance: 500, expected_balance: 185, note: '客服补发',
  })
  expect(wrapper.findComponent(WorkspaceDialog).props('open')).toBe(false)
  expect(wrapper.text()).toContain('已将买家 1688买家乙 的万邦余额调整为 500 次（+315）')
  expect(wrapper.text()).toContain('500 次')

  // 3) 409 余额并发变更冲突：保留弹窗并刷新最新余额。
  await wrapper.findAll('button').find(button => button.text() === '调整额度')!.trigger('click')
  await flushPromises()
  await wrapper.get('[data-testid="quota-target-input"]').setValue('100')
  const conflict = Object.assign(new Error('该买家当前余额已变更为 520 次，请刷新后重新确认。'), { status: 409 })
  vi.mocked(setAccountUserOneBoundQuota).mockRejectedValueOnce(conflict)
  vi.mocked(listAccountUsers).mockResolvedValueOnce({
    items: [admin, { ...buyer, onebound_quota: { ...buyer.onebound_quota!, balance: 520 } }],
    page: 1, total: 2, limit: 30,
  })
  await wrapper.get('dialog form').trigger('submit')
  await flushPromises()
  expect(wrapper.findComponent(WorkspaceDialog).props('open')).toBe(true)
  expect(wrapper.get('dialog [role="alert"]').text()).toContain('520 次')
  expect(wrapper.get('dialog').text()).toContain('当前余额：520 次')
  wrapper.unmount()
})
