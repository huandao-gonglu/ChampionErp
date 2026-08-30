import {
  claimProducts as claimProductsApi,
  deleteDraft as deleteDraftApi,
  deleteProducts as deleteProductsApi,
  fetchDraftsIndex,
  fetchProductsIndex,
  generateCopy as generateCopyApi,
  generateCopyBatch,
  generateImagePrompts,
  imagePoolAction,
  imageEdit as imageEditApi,
  imageTranslate as imageTranslateApi,
  loadDraft as loadDraftApi,
  loadProduct as loadProductApi,
  saveDraft as saveDraftApi,
  saveImagePool,
  saveProduct as saveProductApi,
  uploadImages,
} from '@/api/workflow/catalog'
import type { ImageEditOptions, ImageTranslateOptions } from '@/api/workflow/catalog'
import { assignUpc as assignUpcApi } from '@/api/workflow/settings'
import {  marketplaces } from '@/constants/initialState'
import { listingLanguageValue } from '@/constants/locales'
import type {

  DraftDetail,
  DraftIndexItem,
  Marketplace,
  MarketplaceTargetSite,
  Product,
  ProductIndexItem,
} from '@/types/workflow'
import {

  readFileAsDataUrl,
  type WorkflowRuntime,
} from '../orchestration/runtime'

type WorkflowCatalogActionsPort = Pick<
  WorkflowRuntime,
  | 'product'
  | 'productsIndex'
  | 'draftsIndex'
  | 'selectedProductIds'
  | 'currentDraft'
  | 'currentDraftProductContext'
  | 'imagePrompt'
  | 'collectForm'
  | 'fillFormFromState'
  | 'pricingResult'
  | 'categoryResults'
  | 'categoryRecommendations'
  | 'categoryAutoMatchProductName'
  | 'categoryPrecheck'
  | 'precheck'
  | 'precheckResults'
  | 'payloadPreview'
  | 'copyGenerating'
  | 'activeMarketplace'
  | 'appConfig'
  | 'loading'
  | 'addLog'
  | 'setError'
  | 'currentStage'
  | 'mergeTargetDetails'
  | 'persistActiveTargetListingFields'
  | 'invalidateCategoryAttributeLoad'
  | 'configuredTargetsForLanguage'
  | 'configuredSelectedTargets'
  | 'targetPlatforms'
  | 'syncActivePublishTarget'
  | 'draftDetailFromProduct'
  | 'applyMutationIndexes'
  | 'restorePrecheckFromProduct'
  | 'restoreCategoryFromProduct'
  | 'syncCollectDiagnosticsFromProduct'
  | 'syncPricingInputFromProduct'
  | 'syncDraftPackageDimensionsFromPricingInput'
>

type DraftTargetSelectionSource = Pick<DraftDetail, 'draftId' | 'language' | 'targetSites'>

export function createWorkflowCatalogActions(runtime: WorkflowCatalogActionsPort) {
  const {
    product, productsIndex, draftsIndex, selectedProductIds, currentDraft,
    currentDraftProductContext, imagePrompt, collectForm, fillFormFromState, pricingResult,
    categoryResults, categoryRecommendations, categoryAutoMatchProductName, categoryPrecheck, precheck, precheckResults,
    payloadPreview, copyGenerating, activeMarketplace, appConfig, loading,
    addLog, setError, currentStage, mergeTargetDetails, persistActiveTargetListingFields,
    invalidateCategoryAttributeLoad, configuredTargetsForLanguage, configuredSelectedTargets, targetPlatforms, syncActivePublishTarget,
    draftDetailFromProduct, applyMutationIndexes, restorePrecheckFromProduct, restoreCategoryFromProduct, syncCollectDiagnosticsFromProduct,
    syncPricingInputFromProduct, syncDraftPackageDimensionsFromPricingInput,
  } = runtime

  async function refreshProductsIndex() {
    loading.value = true
    setError('')
    try {
      productsIndex.value = await fetchProductsIndex()
      addLog(`商品库已刷新：${productsIndex.value.length} 条。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '刷新商品库失败')
    } finally {
      loading.value = false
    }
  }

  async function refreshDraftsIndex(scope = 'active') {
    loading.value = true
    setError('')
    try {
      draftsIndex.value = await fetchDraftsIndex(scope)
      addLog(`草稿箱已刷新：${draftsIndex.value.length} 条。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '刷新草稿箱失败')
    } finally {
      loading.value = false
    }
  }

  async function loadProduct(item: ProductIndexItem) {
    loading.value = true
    setError('')
    try {
      const result = await loadProductApi(item.productId, item.productFilePath)
      product.value = result.product
      restoreCategoryFromProduct()
      restorePrecheckFromProduct()
      applyMutationIndexes(result)
      fillFormFromState(appConfig.value)
      syncCollectDiagnosticsFromProduct('已加载商品库商品。')
      syncPricingInputFromProduct()
      addLog(`已加载商品：${product.value.source.title || product.value.name || item.productId}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '加载商品失败')
    } finally {
      loading.value = false
    }
  }

  async function loadDraft(item: DraftIndexItem) {
    invalidateCategoryAttributeLoad()
    loading.value = true
    setError('')
    try {
      const result = await loadDraftApi(item.draftId)
      currentDraft.value = result.draft
      currentDraftProductContext.value = result.productContext
      activeMarketplace.value = result.draft.platform
      categoryRecommendations.value = {}
      categoryAutoMatchProductName.value = ''
      syncActivePublishTarget(undefined, true)
      categoryResults.value = []
      precheck.value = null
      payloadPreview.value = null
      applyMutationIndexes(result)
      syncPricingInputFromProduct()
      addLog(`已加载草稿：${item.title || item.productTitle || item.draftId}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '加载草稿失败')
    } finally {
      loading.value = false
    }
  }

  async function loadDraftForPricing(itemOrDraftId: DraftIndexItem | string) {
    const draftId = typeof itemOrDraftId === 'string' ? itemOrDraftId : itemOrDraftId.draftId
    if (!String(draftId || '').trim()) {
      setError('草稿缺少 ID，无法进入核价。')
      return false
    }
    invalidateCategoryAttributeLoad()
    loading.value = true
    setError('')
    try {
      const result = await loadDraftApi(String(draftId))
      currentDraft.value = result.draft
      currentDraftProductContext.value = result.productContext
      activeMarketplace.value = result.draft.platform
      syncActivePublishTarget(undefined, true)
      pricingResult.value = null
      applyMutationIndexes(result)
      syncPricingInputFromProduct()
      currentStage.value = 5
      addLog(`已进入草稿核价：${result.draft.title || result.productContext.title || result.draft.draftId}`)
      return true
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '加载核价草稿失败')
      return false
    } finally {
      loading.value = false
    }
  }

  async function updateDraftTargets(item: DraftTargetSelectionSource, targets: MarketplaceTargetSite[]) {
    const draftId = String(item.draftId || '').trim()
    if (!draftId) {
      setError('草稿缺少 ID，无法保存站点。')
      return
    }
    const expectedLanguage = String(targets[0]?.language || item.language || '').trim()
    if (!expectedLanguage) {
      setError('请先选择草稿语言。')
      return
    }
    const configuredTargets = configuredTargetsForLanguage(expectedLanguage)
    if (!configuredTargets.length) {
      setError('当前语言没有配置可发布站点。')
      return
    }
    const validTargets = configuredSelectedTargets(expectedLanguage, targets)
    if (!validTargets.length) {
      setError('请选择来自市场配置且与草稿语言匹配的站点。')
      return
    }
    const primaryTarget = validTargets[0]
    const platforms = targetPlatforms(validTargets)
    const previousDraftsIndex = draftsIndex.value
    draftsIndex.value = draftsIndex.value.map((draft) => (
      draft.draftId === draftId
        ? { ...draft, platform: primaryTarget.platform, site: primaryTarget.site, language: primaryTarget.language, platforms, targetSites: validTargets }
        : draft
    ))
    loading.value = true
    setError('')
    try {
      const loaded = currentDraft.value.draftId === draftId
        ? { draft: currentDraft.value, productContext: currentDraftProductContext.value }
        : await loadDraftApi(draftId)
      const draftToSave: DraftDetail = {
        ...loaded.draft,
        platform: primaryTarget.platform,
        platforms,
        targetSites: mergeTargetDetails(validTargets, loaded.draft.targetSites || [], loaded.draft),
        site: primaryTarget.site,
        language: primaryTarget.language,
      }
      const result = await saveDraftApi(draftToSave)
      if (currentDraft.value.draftId === draftId) {
        currentDraft.value = result.draft
        currentDraftProductContext.value = result.productContext
        activeMarketplace.value = result.draft.platform
        syncActivePublishTarget(primaryTarget, true)
      }
      applyMutationIndexes(result)
      addLog(`草稿目标市场已更新：${validTargets.map((target) => `${target.platform} · ${target.site}`).join('、')}。`)
    } catch (exc) {
      draftsIndex.value = previousDraftsIndex
      setError(exc instanceof Error ? exc.message : '更新草稿站点失败')
    } finally {
      loading.value = false
    }
  }

  async function updateDraftLanguage(item: DraftTargetSelectionSource, language: string) {
    const selectedLanguage = String(language || '').trim()
    if (!selectedLanguage) {
      setError('请选择草稿语言。')
      return
    }
    const matchingTargets = configuredTargetsForLanguage(selectedLanguage)
    if (!matchingTargets.length) {
      setError('当前语言没有配置可发布站点。')
      return
    }
    const existingTargets = configuredSelectedTargets(selectedLanguage, item.targetSites || [])
    await updateDraftTargets(item, existingTargets.length ? existingTargets : matchingTargets.slice(0, 1))
  }

  async function deleteDraftsByIds(draftIds: string[], items: DraftIndexItem[] = []) {
    const ids = Array.from(new Set(draftIds.map((id) => String(id || '').trim()).filter(Boolean)))
    if (!ids.length) {
      setError('请先选择要删除的草稿。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await deleteDraftApi(ids.length === 1 ? ids[0] : ids)
      applyMutationIndexes(result)
      const deletedIds = result.deletedDraftIds?.length
        ? result.deletedDraftIds
        : result.deletedIds?.length
          ? result.deletedIds
          : result.deletedDraftId
            ? [result.deletedDraftId]
            : []
      const affectedProductIds = result.affectedProductIds?.length
        ? result.affectedProductIds
        : Array.from(new Set(items.filter((item) => deletedIds.includes(item.draftId)).map((item) => item.productId).filter(Boolean)))
      const currentProductId = product.value.productId
      if (result.product.productId && result.product.productId === product.value.productId) {
        product.value = result.product
        restoreCategoryFromProduct()
        restorePrecheckFromProduct()
        syncPricingInputFromProduct()
      } else if (currentProductId && affectedProductIds.includes(currentProductId)) {
        try {
          const refreshed = await loadProductApi(currentProductId)
          product.value = refreshed.product
          applyMutationIndexes(refreshed)
          restoreCategoryFromProduct()
          restorePrecheckFromProduct()
          syncPricingInputFromProduct()
        } catch (refreshExc) {
          addLog(`当前商品刷新失败：${refreshExc instanceof Error ? refreshExc.message : '未知错误'}`)
        }
      }
      const deletedCount = result.deleted ?? deletedIds.length
      const fallbackTitle = items[0]?.title || items[0]?.productTitle || ids[0]
      addLog(result.message || (deletedCount === 1 ? `已删除草稿：${fallbackTitle}` : `已删除 ${deletedCount} 个草稿。`))
      if (result.missingIds?.length) addLog(`未找到草稿：${result.missingIds.join('、')}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '删除草稿失败')
    } finally {
      loading.value = false
    }
  }

  async function deleteDraft(item: DraftIndexItem) {
    await deleteDraftsByIds([item.draftId], [item])
  }

  async function deleteDrafts(items: DraftIndexItem[]) {
    await deleteDraftsByIds(items.map((item) => item.draftId), items)
  }

  async function deleteProductsByIds(productIds: string[]) {
    const ids = Array.from(new Set(productIds.map((id) => String(id || '').trim()).filter(Boolean)))
    if (!ids.length) {
      setError('请先选择要删除的商品。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await deleteProductsApi(ids)
      productsIndex.value = result.productsIndex
      selectedProductIds.value = selectedProductIds.value.filter((id) => !result.deletedIds.includes(id))
      if (result.product && result.deletedIds.includes(product.value.productId)) {
        product.value = result.product
        syncCollectDiagnosticsFromProduct('当前商品已删除。')
        syncPricingInputFromProduct()
      }
      addLog(result.message || `已删除 ${result.deleted} 个商品。`)
      if (result.missingIds.length) addLog(`未找到商品：${result.missingIds.join('、')}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '删除商品失败')
    } finally {
      loading.value = false
    }
  }

  async function deleteProduct(item: ProductIndexItem) {
    await deleteProductsByIds([item.productId])
  }

  async function deleteSelectedProducts() {
    await deleteProductsByIds(selectedProductIds.value)
  }

  function toggleProductSelection(productId: string, checked?: boolean) {
    const exists = selectedProductIds.value.includes(productId)
    const shouldAdd = checked ?? !exists
    if (shouldAdd && !exists) selectedProductIds.value.push(productId)
    if (!shouldAdd) selectedProductIds.value = selectedProductIds.value.filter((id) => id !== productId)
  }

  function selectAllProducts(checked: boolean, productIds?: string[]) {
    const targetIds = (productIds?.length ? productIds : productsIndex.value.map((item) => item.productId)).filter(Boolean)
    if (!checked) {
      selectedProductIds.value = selectedProductIds.value.filter((id) => !targetIds.includes(id))
      return
    }
    selectedProductIds.value = Array.from(new Set([...selectedProductIds.value, ...targetIds]))
  }

  function normalizeClaimPlatforms(values: Marketplace[]) {
    return marketplaces.filter((platform) => values.includes(platform))
  }

  function setClaimPlatforms(values: Marketplace[]) {
    const selected = normalizeClaimPlatforms(values)
    collectForm.value.selectedClaimPlatforms = selected.length ? selected : [activeMarketplace.value]
  }

  async function claimSelectedProducts() {
    const ids = selectedProductIds.value.length
      ? selectedProductIds.value
      : product.value.productId
        ? [product.value.productId]
        : productsIndex.value.map((item) => item.productId).filter(Boolean)
    if (!ids.length) {
      setError('请先选择商品。')
      return false
    }
    loading.value = true
    setError('')
    try {
      const platform = activeMarketplace.value
      await claimProductsApi(ids, platform)
      productsIndex.value = await fetchProductsIndex()
      draftsIndex.value = await fetchDraftsIndex()
      addLog(`已推送 ${ids.length} 个商品到草稿箱。`)
      return true
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '推到草稿箱失败')
      return false
    } finally {
      loading.value = false
    }
  }

  async function claimCurrentProduct() {
    const id = product.value.productId
    if (!id) {
      setError('请先从商品库加载一个商品。')
      return false
    }
    loading.value = true
    setError('')
    try {
      const platform = activeMarketplace.value
      await claimProductsApi([id], platform)
      const loaded = await loadProductApi(id, '')
      product.value = loaded.product
      productsIndex.value = await fetchProductsIndex()
      draftsIndex.value = await fetchDraftsIndex()
      addLog('已推到草稿箱。')
      return true
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '推到草稿箱失败')
      return false
    } finally {
      loading.value = false
    }
  }

  async function generateCopyForSelectedProducts() {
    const ids = selectedProductIds.value.length ? selectedProductIds.value : product.value.productId ? [product.value.productId] : []
    if (!ids.length) {
      setError('请先选择要批量生成文案的商品。')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await generateCopyBatch(ids, activeMarketplace.value)
      productsIndex.value = await fetchProductsIndex()
      addLog(`批量 AI 文案完成：${ids.length} 个商品，平台 ${activeMarketplace.value}。${String(result.message || '')}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '批量生成文案失败')
    } finally {
      loading.value = false
    }
  }

  async function enqueueSelectedProducts() {
    setError('发布入队已改为草稿目标级操作，请从草稿箱进入对应草稿并完成预检后入队。')
  }

  async function uploadReferenceImages(files: File[]) {
    if (!files.length) return
    loading.value = true
    setError('')
    try {
      const uploads = await Promise.all(
        files.map(async (file) => ({
          filename: file.name,
          data_url: await readFileAsDataUrl(file),
        })),
      )
      const result = await uploadImages(product.value, uploads)
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      syncCollectDiagnosticsFromProduct('参考图片已上传。')
      addLog(`已上传 ${files.length} 张参考图片。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '上传参考图片失败')
    } finally {
      loading.value = false
    }
  }

  async function clearSourceImages() {
    loading.value = true
    setError('')
    try {
      const nextProduct: Product = { ...product.value, source: { ...product.value.source, imagePool: [] } }
      const result = await saveProductApi(nextProduct)
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      syncCollectDiagnosticsFromProduct('已清除参考图片。')
      addLog('已清除参考图片并保存商品。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '清除参考图片失败')
    } finally {
      loading.value = false
    }
  }

  async function saveCurrentImagePool() {
    loading.value = true
    setError('')
    try {
      const result = await saveImagePool(product.value, product.value.source.imagePool)
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      addLog('图片池变更已保存。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存图片池失败')
    } finally {
      loading.value = false
    }
  }

  async function setMainImage(imageId: string) {
    loading.value = true
    setError('')
    try {
      const result = await imagePoolAction(product.value, 'set_main', { image_id: imageId })
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      addLog(`已设置主图：${imageId}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '设置主图失败')
    } finally {
      loading.value = false
    }
  }

  async function deleteImages(imageIds: string[]) {
    if (!imageIds.length) return
    loading.value = true
    setError('')
    try {
      const result = await imagePoolAction(product.value, 'delete', { image_ids: imageIds })
      product.value = result.product
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      addLog(`已删除 ${imageIds.length} 张图片。`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '删除图片失败')
    } finally {
      loading.value = false
    }
  }

  async function editImagesWithPrompt(prompt: string, options: ImageEditOptions = {}) {
    const userPrompt = String(prompt || '').trim()
    if (!userPrompt) {
      setError('请输入图生图提示词')
      return
    }
    loading.value = true
    setError('')
    try {
      const result = await imageEditApi(product.value, activeMarketplace.value, userPrompt, options)
      product.value = result.product
      if (result.draft) currentDraft.value = result.draft
      if (result.productContext) currentDraftProductContext.value = result.productContext
      applyMutationIndexes(result)
      currentStage.value = 3
      const generatedCount = Number(result.raw?.generated_count || 0)
      addLog(`图生图完成${generatedCount ? `：新增 ${generatedCount} 张图片` : ''}。${result.message ? `提示：${result.message}` : ''}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '图生图失败')
    } finally {
      loading.value = false
    }
  }

  async function saveCurrentProduct() {
    loading.value = true
    setError('')
    try {
      const result = await saveProductApi(product.value)
      product.value = result.product
      applyMutationIndexes(result)
      syncPricingInputFromProduct()
      addLog('商品草稿已保存。')
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存商品失败')
    } finally {
      loading.value = false
    }
  }

  async function saveCurrentDraft() {
    loading.value = true
    setError('')
    try {
      syncDraftPackageDimensionsFromPricingInput()
      persistActiveTargetListingFields(categoryPrecheck.value ? { categoryPrecheck: categoryPrecheck.value.raw || categoryPrecheck.value } : {})
      const result = await saveDraftApi(currentDraft.value)
      currentDraft.value = result.draft
      currentDraftProductContext.value = result.productContext
      activeMarketplace.value = result.draft.platform
      syncActivePublishTarget()
      precheck.value = null
      precheckResults.value = {}
      payloadPreview.value = null
      applyMutationIndexes(result)
      addLog(result.message || `草稿已保存：${result.draft.title || result.draft.draftId}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '保存草稿失败')
    } finally {
      loading.value = false
    }
  }

  async function assignUpc() {
    loading.value = true
    setError('')
    try {
      const result = await assignUpcApi()
      const assignedUpc = String(result.raw?.upc || result.product.upc || '')
      if (result.product.productId || result.product.name || result.product.source.title) {
        product.value = result.product
      } else if (assignedUpc) {
        product.value.upc = assignedUpc
      }
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      addLog(`UPC 已分配：${assignedUpc || product.value.upc || '已写入商品'}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '分配 UPC 失败')
    } finally {
      loading.value = false
    }
  }

  async function generateCopy(useCurrentDraft = false) {
    loading.value = true
    copyGenerating.value = true
    setError('')
    try {
      const draftProductId = useCurrentDraft ? currentDraft.value.productId || currentDraftProductContext.value.productId : ''
      const productForCopy = draftProductId && draftProductId !== product.value.productId
        ? { ...product.value, productId: draftProductId }
        : product.value
      const result = await generateCopyApi(
        productForCopy,
        activeMarketplace.value,
        useCurrentDraft
          ? {
              draftId: currentDraft.value.draftId,
              language: currentDraft.value.language,
              mode: 'rewrite',
            }
          : {},
      )
      product.value = result.product
      if (result.draft) {
        currentDraft.value = result.draft
        currentDraftProductContext.value = result.productContext || currentDraftProductContext.value
        activeMarketplace.value = result.draft.platform
        product.value.drafts[result.draft.platform] = result.draft
        syncActivePublishTarget(undefined, true)
      } else if (useCurrentDraft && result.product.productId) {
        const generatedDraft = draftDetailFromProduct(activeMarketplace.value, result.product)
        currentDraft.value = {
          ...currentDraft.value,
          ...generatedDraft,
          draftId: generatedDraft.draftId || currentDraft.value.draftId,
          productId: generatedDraft.productId || draftProductId || currentDraft.value.productId,
        }
      }
      applyMutationIndexes(result)
      currentStage.value = 2
      addLog(`${activeMarketplace.value} 文案已生成。${result.warning ? `提示：${result.warning}` : ''}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '生成文案失败')
    } finally {
      copyGenerating.value = false
      loading.value = false
    }
  }

  async function generateImagePromptPack(targetLanguage?: string) {
    loading.value = true
    setError('')
    try {
      const language = String(targetLanguage || product.value.drafts[activeMarketplace.value]?.language || listingLanguageValue(activeMarketplace.value)).trim()
      imagePrompt.value = await generateImagePrompts(product.value, activeMarketplace.value, language)
      addLog(`生图提示词已生成。${language ? `目标语言：${language}` : ''}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '生成图片提示词失败')
    } finally {
      loading.value = false
    }
  }

  async function translateImages(targetLanguage?: string, options: ImageTranslateOptions = {}) {
    loading.value = true
    setError('')
    try {
      const language = String(targetLanguage || product.value.drafts[activeMarketplace.value]?.language || listingLanguageValue(activeMarketplace.value)).trim()
      const result = await imageTranslateApi(product.value, activeMarketplace.value, language, options)
      product.value = result.product
      if (result.draft) currentDraft.value = result.draft
      if (result.productContext) currentDraftProductContext.value = result.productContext
      if (result.productsIndex.length) productsIndex.value = result.productsIndex
      if (result.draftsIndex) draftsIndex.value = result.draftsIndex
      currentStage.value = 3
      addLog(`图片翻译/重绘完成：${language}。${result.message ? `提示：${result.message}` : ''}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '图片翻译/重绘失败')
    } finally {
      loading.value = false
    }
  }


  return {
    refreshProductsIndex, refreshDraftsIndex, loadProduct, loadDraft, loadDraftForPricing, updateDraftTargets,
    updateDraftLanguage, deleteDraft, deleteDrafts, deleteProduct, deleteSelectedProducts, toggleProductSelection,
    selectAllProducts, setClaimPlatforms, claimSelectedProducts, claimCurrentProduct, generateCopyForSelectedProducts, enqueueSelectedProducts,
    uploadReferenceImages, clearSourceImages, saveCurrentImagePool, setMainImage, deleteImages, editImagesWithPrompt,
    saveCurrentProduct, saveCurrentDraft, assignUpc, generateCopy, generateImagePromptPack, translateImages,
  }
}
