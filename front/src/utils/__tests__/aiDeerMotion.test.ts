import { describe, expect, it } from 'vitest'
import {
  computeWindowCharsPerSecond,
  DEER_STALL_THRESHOLD_MS,
  IDLE_DEER_POSE,
  measureTurnActivity,
  resolveDeerMotion,
  sampleDeerPose,
} from '../aiDeerMotion'

describe('AI 对话奔跑小鹿步态与速率推导', () => {
  it('非活动或等待用户输入/确认时保持静止站立', () => {
    expect(resolveDeerMotion({
      live: false,
      waitingForUser: false,
      hasActiveTool: false,
      hasExternalWork: false,
      streamingVisibleText: true,
      charsPerSecond: 80,
      silentMs: 0,
      recentToolTick: false,
    })).toEqual({ gait: 'idle', pace: 0 })

    expect(resolveDeerMotion({
      live: true,
      waitingForUser: true,
      hasActiveTool: false,
      hasExternalWork: false,
      streamingVisibleText: false,
      charsPerSecond: 0,
      silentMs: 10000,
      recentToolTick: false,
    })).toEqual({ gait: 'idle', pace: 0 })
  })

  it('流式正文高速输出切入疾驰，低速思考或工具执行保持稳健小跑不假死', () => {
    const fast = resolveDeerMotion({
      live: true,
      waitingForUser: false,
      hasActiveTool: false,
      hasExternalWork: false,
      streamingVisibleText: true,
      charsPerSecond: 90,
      silentMs: 100,
      recentToolTick: false,
    })
    expect(fast.gait).toBe('gallop')
    expect(fast.pace).toBeGreaterThan(1.5)
    expect(fast.pace).toBeLessThanOrEqual(1.95)

    // 工具长时间执行时没有字符流，也应保持小跑而非判定停滞。
    expect(resolveDeerMotion({
      live: true,
      waitingForUser: false,
      hasActiveTool: true,
      hasExternalWork: false,
      streamingVisibleText: false,
      charsPerSecond: 0,
      silentMs: DEER_STALL_THRESHOLD_MS + 5000,
      recentToolTick: false,
    })).toEqual({ gait: 'trot', pace: 1 })

    expect(resolveDeerMotion({
      live: true,
      waitingForUser: false,
      hasActiveTool: true,
      hasExternalWork: false,
      streamingVisibleText: false,
      charsPerSecond: 0,
      silentMs: 500,
      recentToolTick: true,
    })).toEqual({ gait: 'trot', pace: 1.15 })
  })

  it('无工具运行且长时间无响应时平滑降速为缓步', () => {
    expect(resolveDeerMotion({
      live: true,
      waitingForUser: false,
      hasActiveTool: false,
      hasExternalWork: false,
      streamingVisibleText: false,
      charsPerSecond: 0,
      silentMs: DEER_STALL_THRESHOLD_MS,
      recentToolTick: false,
    })).toEqual({ gait: 'slow', pace: 0.46 })
  })

  it('滑动窗口只统计最近活跃样本并对极短时间做下限平滑', () => {
    const cps = computeWindowCharsPerSecond([
      { at: 1000, chars: 100 },
      { at: 2950, chars: 30 },
    ], 3000, 1500)
    // 1000ms 样本已过期，仅保留 2950ms 的 30 字，按 600ms 最短跨度折算为 50 字/秒。
    expect(cps).toBe(50)
  })

  it('统计回合活跃度时同时感知隐藏思考、可见正文与工具进度', () => {
    const result = measureTurnActivity(
      [
        { type: 'reasoning', text: '正在分析类目', state: 'streaming' },
        { type: 'text', text: '<think>内部分析</think>可见答复', state: 'streaming' },
      ],
      {
        call1: {
          tool_call_id: 'call1',
          tool_name: 'draft_changes_apply',
          started: 5,
          completed: 3,
          failed: 0,
          active: 2,
          tools: [],
        },
      },
    )
    expect(result.streamingVisibleText).toBe(true)
    expect(result.charCount).toBe('正在分析类目'.length + '<think>内部分析</think>可见答复'.length)
    expect(result.progressCount).toBe(8)
  })

  it('方案 B+ 三段腿骨骼与脊柱屈伸按步态区分，静止返回标准站姿', () => {
    expect(sampleDeerPose(0.25, 'idle', 0)).toBe(IDLE_DEER_POSE)
    const slow = sampleDeerPose(0, 'slow', 0.46)
    const gallopStretch = sampleDeerPose(0, 'gallop', 1.55)
    const gallopGather = sampleDeerPose(0.375, 'gallop', 1.55)
    const gallopStrike = sampleDeerPose(0.28, 'gallop', 1.55)
    // 疾驰腾空时前腿幅度与脊柱舒展显著大于四拍缓步，且弓背阶段脊柱明显收缩为负值。
    expect(Math.abs(gallopStretch.frontNear.u)).toBeGreaterThan(Math.abs(slow.frontNear.u))
    expect(gallopStretch.flex).toBeGreaterThan(0.5)
    expect(gallopGather.flex).toBeLessThan(-0.5)
    expect(gallopStrike.strikes.length).toBeGreaterThan(0)
    expect(slow.strikes).toHaveLength(0)
  })
})
