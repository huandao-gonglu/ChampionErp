import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ImageHostingSettingsPanel from '../ImageHostingSettingsPanel.vue'
import WorkspaceDialog from '@/components/shared/WorkspaceDialog.vue'
import * as api from '@/api/imageHosting'
import type { ImageHostingConfig, ImageHostingTestResult } from '@/types/imageHosting'

vi.mock('@/api/imageHosting', () => ({
  loadImageHosting: vi.fn(), saveImageHosting: vi.fn(), testImageHosting: vi.fn(),
  setImageHostingDefault: vi.fn(), deleteImageHosting: vi.fn(),
}))

const saved: ImageHostingConfig = {
  default_profile_id: 'images-main',
  profiles: [{
    id: 'images-main', name: '商品图片', type: 's3_compatible',
    endpoint_url: 'https://s3.example.test', region: 'auto', bucket: 'images',
    key_prefix: 'products', public_base_url: 'https://images.example.test', addressing_style: 'path',
    access_key_id_configured: true, secret_access_key_configured: true,
  }],
}
const tested: ImageHostingTestResult = {
  profile_id: 'images-main', config_version: 'version', checked_at: '2026-10-01T00:00:00Z',
  upload_ok: true, upload_attempted: true, public_access_ok: false, public_access_attempted: true, cleanup_status: 'retained',
  storage_key: 'products/hosting-tests/example.png', public_url: 'https://images.example.test/test.png',
  error_code: 'IMAGE_PUBLIC_ACCESS_FAILED', message: '公开读取失败', cleanup_message: '没有删除权限',
}

function button(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((item) => item.text() === text)!
}
function pointer(element: Element, type: string) {
  const event = new Event(type, { bubbles: true })
  Object.defineProperties(event, {
    pointerId: { value: 1 }, isPrimary: { value: true }, button: { value: 0 },
  })
  element.dispatchEvent(event)
}
async function panel() {
  const wrapper = mount(ImageHostingSettingsPanel, { global: { stubs: { Teleport: true } } })
  await flushPromises()
  return wrapper
}

describe('图片托管设置的凭据、测试和弹窗交互', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.loadImageHosting).mockResolvedValue(structuredClone(saved))
    vi.mocked(api.saveImageHosting).mockResolvedValue({ ok: true, id: 'images-main', image_hosting: saved })
    vi.mocked(api.testImageHosting).mockResolvedValue(tested)
    vi.mocked(api.setImageHostingDefault).mockResolvedValue({ ...saved, default_profile_id: '' })
    vi.mocked(api.deleteImageHosting).mockResolvedValue({ default_profile_id: '', profiles: [] })
  })
  afterEach(() => { document.body.innerHTML = '' })

  it('留空保留原凭据，输入新凭据替换，保存后移除表单秘密', async () => {
    const wrapper = await panel()
    expect(wrapper.text()).toContain('Access Key：已配置')
    await button(wrapper, '编辑 / 测试').trigger('click')
    expect(wrapper.findAll('input[type="checkbox"]')).toHaveLength(0)
    expect(wrapper.text()).toContain('已配置，留空保留')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.saveImageHosting).toHaveBeenCalledWith(expect.objectContaining({ id: 'images-main', clear_secrets: [] }))
    expect(vi.mocked(api.saveImageHosting).mock.calls[0][0]).not.toHaveProperty('access_key_id')
    expect(vi.mocked(api.saveImageHosting).mock.calls[0][0]).not.toHaveProperty('secret_access_key')

    await button(wrapper, '编辑 / 测试').trigger('click')
    await wrapper.findAll('input[type="password"]')[0].setValue('new-access-key')
    await wrapper.findAll('input[type="password"]')[1].setValue('new-secret')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.saveImageHosting).toHaveBeenLastCalledWith(expect.objectContaining({ clear_secrets: [], access_key_id: 'new-access-key', secret_access_key: 'new-secret' }))
    expect(wrapper.findAll('input[type="password"]').every((item) => (item.element as HTMLInputElement).value === '')).toBe(true)
    wrapper.unmount()
  })

  it('高级设置移除两项凭据，取消可恢复输入，保存前不写入', async () => {
    vi.mocked(api.loadImageHosting).mockResolvedValue({ ...structuredClone(saved), default_profile_id: '' })
    const wrapper = await panel()
    await button(wrapper, '编辑 / 测试').trigger('click')
    const inputs = () => wrapper.findAll('input[type="password"]')
    await inputs()[0].setValue('replacement-access-key')
    await inputs()[1].setValue('replacement-secret')
    expect(wrapper.get('details').text()).toContain('移除已保存凭据')
    await button(wrapper, '移除已保存凭据').trigger('click')
    expect(api.saveImageHosting).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('点击“保存配置”后将移除')
    expect(wrapper.text()).toContain('此配置将无法上传图片')
    expect(inputs().every((item) => (item.element as HTMLInputElement).disabled)).toBe(true)
    expect(button(wrapper, '测试上传与公开读取').attributes('disabled')).toBeDefined()

    await button(wrapper, '取消移除').trigger('click')
    expect((inputs()[0].element as HTMLInputElement).value).toBe('replacement-access-key')
    expect((inputs()[1].element as HTMLInputElement).value).toBe('replacement-secret')
    expect(inputs().every((item) => !(item.element as HTMLInputElement).disabled)).toBe(true)
    expect(api.saveImageHosting).not.toHaveBeenCalled()

    await button(wrapper, '移除已保存凭据').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.saveImageHosting).toHaveBeenCalledTimes(1)
    const payload = vi.mocked(api.saveImageHosting).mock.calls[0][0]
    expect(payload.clear_secrets).toEqual(['access_key_id', 'secret_access_key'])
    expect(payload).not.toHaveProperty('access_key_id')
    expect(payload).not.toHaveProperty('secret_access_key')
    expect(api.testImageHosting).not.toHaveBeenCalled()
    expect(wrapper.findAll('input[type="password"]').every((item) => (item.element as HTMLInputElement).value === '')).toBe(true)
    wrapper.unmount()
  })

  it('默认配置先解除才能移除凭据，新配置不提供移除已保存凭据的操作', async () => {
    const wrapper = await panel()
    await button(wrapper, '编辑 / 测试').trigger('click')
    expect(button(wrapper, '移除已保存凭据').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('请先在列表中解除默认')
    await button(wrapper, '移除已保存凭据').trigger('click')
    expect(wrapper.text()).not.toContain('待移除已保存的访问凭据')
    await button(wrapper, '关闭').trigger('click')
    await button(wrapper, '新增配置').trigger('click')
    expect(wrapper.findAll('button').some((item) => item.text() === '移除已保存凭据')).toBe(false)
    wrapper.unmount()
  })

  it('测试使用未保存表单，分开展示上传、匿名读取和残留对象；修改后隐藏旧结果', async () => {
    const wrapper = await panel()
    await button(wrapper, '编辑 / 测试').trigger('click')
    const bucket = wrapper.get('input[placeholder="product-images"]')
    await bucket.setValue('other-images')
    await wrapper.findAll('input[type="password"]')[0].setValue('unsaved-key')
    await button(wrapper, '测试上传与公开读取').trigger('click')
    await flushPromises()
    expect(api.testImageHosting).toHaveBeenCalledWith(expect.objectContaining({ bucket: 'other-images', access_key_id: 'unsaved-key' }))
    expect(api.saveImageHosting).not.toHaveBeenCalled()
    expect(wrapper.get('[role="status"]').text()).toContain('上传：成功')
    expect(wrapper.get('[role="status"]').text()).toContain('匿名读取：失败')
    expect(wrapper.get('[role="status"]').text()).toContain(tested.storage_key)
    await bucket.setValue('changed-images')
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('默认配置先解除才能删除，删除只调用配置端点', async () => {
    const wrapper = await panel()
    expect(button(wrapper, '删除').attributes('disabled')).toBeDefined()
    await button(wrapper, '解除默认').trigger('click')
    await flushPromises()
    expect(api.setImageHostingDefault).toHaveBeenCalledWith('')
    await button(wrapper, '删除').trigger('click')
    await flushPromises()
    expect(api.deleteImageHosting).toHaveBeenCalledWith('images-main')
    expect(api.testImageHosting).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('尚未配置图片托管')
    wrapper.unmount()
  })

  it('发送前拦截时显示原因及未执行状态，不提示残留对象', async () => {
    vi.mocked(api.testImageHosting).mockResolvedValueOnce({
      ...tested, upload_ok: false, upload_attempted: false, public_access_attempted: false,
      cleanup_status: 'not_needed', error_code: 'IMAGE_DNS_NONPUBLIC',
      message: '域名解析到 Fake-IP，已在发送前拦截', cleanup_message: '未产生测试对象，无需清理',
    })
    const wrapper = await panel()
    await button(wrapper, '编辑 / 测试').trigger('click')
    await button(wrapper, '测试上传与公开读取').trigger('click')
    await flushPromises()
    const text = wrapper.get('[role="status"]').text()
    expect(text).toContain('上传：未发送')
    expect(text).toContain('匿名读取：未执行')
    expect(text).toContain('Fake-IP')
    expect(text).toContain('无需清理')
    expect(text).not.toContain(tested.storage_key)
    wrapper.unmount()
  })

  it('内部按下后拖出不关闭，正常遮罩手势关闭；测试中不能关闭或重复提交', async () => {
    const wrapper = await panel()
    await button(wrapper, '编辑 / 测试').trigger('click')
    await flushPromises()
    const dialog = wrapper.get('dialog')
    pointer(wrapper.get('form input').element, 'pointerdown')
    pointer(dialog.element, 'pointerup')
    await flushPromises()
    expect(wrapper.findComponent(WorkspaceDialog).props('open')).toBe(true)
    pointer(dialog.element, 'pointerdown')
    pointer(dialog.element, 'pointerup')
    await flushPromises()
    expect(wrapper.findComponent(WorkspaceDialog).props('open')).toBe(false)

    await button(wrapper, '编辑 / 测试').trigger('click')
    let finish!: (value: ImageHostingTestResult) => void
    vi.mocked(api.testImageHosting).mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    await button(wrapper, '测试上传与公开读取').trigger('click')
    pointer(dialog.element, 'pointerdown')
    pointer(dialog.element, 'pointerup')
    await button(wrapper, '关闭').trigger('click')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.findComponent(WorkspaceDialog).props('open')).toBe(true)
    expect(wrapper.get('fieldset').attributes('disabled')).toBeDefined()
    expect(api.testImageHosting).toHaveBeenCalledOnce()
    expect(api.saveImageHosting).not.toHaveBeenCalled()
    finish(tested)
    await flushPromises()
    await button(wrapper, '关闭').trigger('click')
    expect(wrapper.findComponent(WorkspaceDialog).props('open')).toBe(false)
    wrapper.unmount()
  })
})
