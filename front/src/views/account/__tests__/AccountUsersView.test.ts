import { flushPromises, mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import { bindUserOneBound, fetchUserOneBoundBinding, listAccountUsers, listOneBoundBindings } from '@/api/accounts'
import AccountUsersView from '../AccountUsersView.vue'

vi.mock('@/api/accounts', () => ({
  bindUserOneBound: vi.fn(),
  fetchUserOneBoundBinding: vi.fn(),
  listAccountUsers: vi.fn(),
  listOneBoundBindings: vi.fn(),
  createAccountUser: vi.fn(),
  resetAccountUserPassword: vi.fn(),
  setAccountUserStatus: vi.fn(),
}))

it.each(['admin', 'user'] as const)('为选中的 %s 账号保存万邦授权', async (role) => {
  const buyer = { id: 'user-b', email: 'b@example.test', display_name: '采购员乙', role: 'user', status: 'enabled', created_at: 0, updated_at: 0 } as const
  const admin = { ...buyer, id: 'admin-erp', email: 'admin@example.test', display_name: '初始管理员', role: 'admin' } as const
  vi.mocked(listAccountUsers).mockResolvedValue({
    items: [admin, buyer],
    page: 1, total: 2, limit: 30,
  })
  vi.mocked(fetchUserOneBoundBinding).mockResolvedValue({ binding_id: '', account_name: '', state: 'unbound' })
  vi.mocked(listOneBoundBindings).mockImplementation(async page => ({
    ok: true, page, page_count: 2, total: 2,
    items: page === 1
      ? [{ id: 'expired', nick: '失效买家', status: '0', expires_at: '', selectable: false }]
      : [{ id: 'directory-b', nick: '万邦买家乙', status: '1', expires_at: '', selectable: true }],
  }))
  vi.mocked(bindUserOneBound).mockResolvedValue({ binding_id: 'directory-b', account_name: '万邦买家乙', state: 'pending' })
  const wrapper = mount(AccountUsersView, { global: { stubs: ['RouterLink', 'PageHeader'] } })
  await flushPromises()
  const adminRow = wrapper.findAll('tbody tr')[0]!
  expect(adminRow.text()).toContain('管理员')
  expect(adminRow.findAll('button')).toHaveLength(0)
  expect(wrapper.findAll('th').map(header => header.text())).toContain('万邦')
  const row = role === 'admin' ? adminRow : wrapper.findAll('tbody tr')[1]!
  const selected = role === 'admin' ? admin : buyer
  expect(fetchUserOneBoundBinding).toHaveBeenCalledWith(selected.id)
  expect(row.get('select').text()).toContain('万邦买家乙')
  expect(row.get('select').text()).not.toContain('失效买家')
  await row.get('select').setValue('directory-b')
  await flushPromises()
  expect(bindUserOneBound).toHaveBeenLastCalledWith(selected.id, 'directory-b', 2)
  expect(row.text()).toContain('待核验')
  expect(row.get('select').element.value).toBe('directory-b')
  await row.get('button[type="button"]').trigger('click')
  await flushPromises()
  expect(bindUserOneBound).toHaveBeenLastCalledWith(selected.id, 'directory-b', 2)
  const otherRow = wrapper.findAll('tbody tr')[role === 'admin' ? 1 : 0]!
  expect(otherRow.get('select').element.value).toBe('')
  expect(otherRow.text()).not.toContain('待核验')
  wrapper.unmount()
})
