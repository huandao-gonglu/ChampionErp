import { flushPromises, mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import { listAccountUsers } from '@/api/accounts'
import AccountUsersView from '../AccountUsersView.vue'

vi.mock('@/api/accounts', () => ({
  listAccountUsers: vi.fn(),
  createAccountUser: vi.fn(),
  resetAccountUserPassword: vi.fn(),
  setAccountUserStatus: vi.fn(),
}))

it('用户管理展示账号和账号管理操作', async () => {
  const buyer = { id: 'user-b', email: 'b@example.test', display_name: '采购员乙', role: 'user', status: 'enabled', created_at: 0, updated_at: 0 } as const
  const admin = { ...buyer, id: 'admin-erp', email: 'admin@example.test', display_name: '初始管理员', role: 'admin' } as const
  vi.mocked(listAccountUsers).mockResolvedValue({
    items: [admin, buyer],
    page: 1, total: 2, limit: 30,
  })
  const wrapper = mount(AccountUsersView, { global: { stubs: ['RouterLink', 'PageHeader'] } })
  await flushPromises()
  const [adminRow, buyerRow] = wrapper.findAll('tbody tr')
  expect(vi.mocked(listAccountUsers).mock.calls).toEqual([[{ page: 1, q: '' }]])
  expect(wrapper.findAll('th').map(header => header.text())).toEqual(['显示名称', '登录邮箱', '角色', '状态', '操作'])
  expect(adminRow.text()).toContain('管理员')
  expect(adminRow.findAll('button')).toHaveLength(0)
  expect(buyerRow.findAll('button').map(button => button.text())).toEqual(['禁用', '重置密码'])
  expect(wrapper.find('select').exists()).toBe(false)
  wrapper.unmount()
})
