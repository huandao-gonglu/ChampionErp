import type { AiUiPart, CodeProgressSnapshot } from '@/types/aiWork'
import { visibleAssistantText } from './aiThinkingPresentation'

export type DeerGait = 'idle' | 'slow' | 'trot' | 'gallop'

export interface ActivitySample {
  at: number
  chars: number
}

export interface DeerMotionState {
  gait: DeerGait
  /** 目标步频倍率（以对角小跑 1.35Hz 为 1.0 基准）：0 为静止，0.46 为四拍缓步，1.0 为对角小跑，1.44–1.85 为飞跃疾驰。 */
  pace: number
}

export interface DeerMotionInput {
  live: boolean
  waitingForUser: boolean
  hasActiveTool: boolean
  hasExternalWork: boolean
  streamingVisibleText: boolean
  charsPerSecond: number
  silentMs: number
  recentToolTick: boolean
}

export const DEER_ACTIVITY_WINDOW_MS = 1500
export const DEER_STALL_THRESHOLD_MS = 6500
/** 基准步态频率（Hz）：1.0x 对应 BIO_TROT.cycleHz = 1.35，0.46x ≈ 0.62Hz，1.44x ≈ 1.95Hz。 */
export const DEER_BASE_CYCLE_HZ = 1.35

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** 汇总当前回合的流式文本、隐藏思考与工具执行进展，用于区分“活跃输出”“工具执行”与“停滞”。 */
export function measureTurnActivity(
  parts: AiUiPart[],
  codeProgress?: Record<string, CodeProgressSnapshot>,
): { charCount: number; progressCount: number; streamingVisibleText: boolean } {
  let charCount = 0
  let progressCount = 0
  let streamingVisibleText = false

  for (const part of parts) {
    if (part.type === 'text') {
      const text = typeof part.text === 'string' ? part.text : ''
      charCount += text.length
      if ('state' in part && part.state === 'streaming' && visibleAssistantText(text, true).trim()) {
        streamingVisibleText = true
      }
    } else if (part.type === 'reasoning') {
      const text = typeof part.text === 'string' ? part.text : ''
      charCount += text.length
    } else if (part.type === 'dynamic-tool' || part.type.startsWith('tool-')) {
      progressCount += 1
      const toolPart = part as { state?: string; input?: unknown; output?: unknown }
      if (toolPart.state === 'output-available' || toolPart.state === 'output-error' || toolPart.state === 'output-denied') {
        progressCount += 2
      }
      if (typeof toolPart.input === 'string') {
        charCount += toolPart.input.length
      } else if (toolPart.input && typeof toolPart.input === 'object') {
        const record = toolPart.input as Record<string, unknown>
        if (typeof record.code === 'string') charCount += record.code.length
      }
    }
  }

  if (codeProgress) {
    for (const snapshot of Object.values(codeProgress)) {
      progressCount += (snapshot.started ?? 0) + (snapshot.completed ?? 0) + (snapshot.failed ?? 0)
    }
  }

  return { charCount, progressCount, streamingVisibleText }
}

/** 计算滑动时间窗内的平滑字符速率（字/秒）。 */
export function computeWindowCharsPerSecond(samples: ActivitySample[], now: number, windowMs = DEER_ACTIVITY_WINDOW_MS): number {
  const cutoff = now - windowMs
  let total = 0
  let earliest = now
  for (const sample of samples) {
    if (sample.at < cutoff) continue
    total += sample.chars
    if (sample.at < earliest) earliest = sample.at
  }
  if (total <= 0) return 0
  // 最短按 600ms 分母折算，避免刚收到单个分包时瞬时速率冲顶。
  const spanMs = clamp(now - earliest, 600, windowMs)
  return (total * 1000) / spanMs
}

/** 按 AI 实际阶段与平滑速率推导小鹿步态和目标速度，避免思考/工具期假死或按分包抖动。 */
export function resolveDeerMotion(input: DeerMotionInput): DeerMotionState {
  if (!input.live || input.waitingForUser) {
    return { gait: 'idle', pace: 0 }
  }

  if (input.charsPerSecond >= 16) {
    const ratio = clamp((input.charsPerSecond - 16) / 104, 0, 1)
    const pace = Number((1.44 + ratio * 0.41).toFixed(2))
    return { gait: 'gallop', pace }
  }

  if (input.charsPerSecond > 0) {
    const ratio = clamp(input.charsPerSecond / 16, 0, 1)
    const pace = Number((0.95 + ratio * 0.3).toFixed(2))
    return { gait: 'trot', pace }
  }

  if (input.hasActiveTool || input.hasExternalWork) {
    return { gait: 'trot', pace: input.recentToolTick ? 1.15 : 1 }
  }

  if (input.silentMs >= DEER_STALL_THRESHOLD_MS) {
    return { gait: 'slow', pace: 0.46 }
  }

  return { gait: 'trot', pace: 1 }
}

export interface Leg3Angles {
  /** 肩角/髋角 */
  u: number
  /** 腕膝/飞节角 */
  m: number
  /** 球节蹄腕角 */
  f: number
}

export interface HoofStrike {
  x: number
  age: number
}

export interface DeerPose {
  bodyY: number
  pitch: number
  /** 脊柱屈伸：+1 为腾空舒展拉长，-1 为弓背收拢。 */
  flex: number
  headPit: number
  tail: number
  frontNear: Leg3Angles
  frontFar: Leg3Angles
  hindNear: Leg3Angles
  hindFar: Leg3Angles
  strikes: HoofStrike[]
}

interface Leg3Keyframes {
  u: readonly number[]
  m: readonly number[]
  f: readonly number[]
}

interface BioGaitConfig {
  bodyY: readonly number[]
  pitch: readonly number[]
  flex: readonly number[]
  headPit: readonly number[]
  tail: readonly number[]
  front: Leg3Keyframes
  hind: Leg3Keyframes
  frontFarOffset: number
  hindFarOffset: number
  hindPhaseShift?: number
  strikes: readonly { t: number; x: number }[]
}

// 周期性 Catmull-Rom 样条插值，保证 8 帧步态首尾平滑衔接、无棱角顿挫。
function catmullPeriodic(arr: readonly number[], tNorm: number): number {
  const n = arr.length
  const u = (((tNorm % 1) + 1) % 1) * n
  const i1 = Math.floor(u) % n
  const f = u - Math.floor(u)
  const p0 = arr[(i1 - 1 + n) % n]
  const p1 = arr[i1]
  const p2 = arr[(i1 + 1) % n]
  const p3 = arr[(i1 + 2) % n]
  const f2 = f * f
  const f3 = f2 * f
  return 0.5 * (
    (2 * p1)
    + (-p0 + p2) * f
    + (2 * p0 - 5 * p1 + 4 * p2 - p3) * f2
    + (-p0 + 3 * p1 - 3 * p2 + p3) * f3
  )
}

function sampleLeg3(keys: Leg3Keyframes, tNorm: number): Leg3Angles {
  return {
    u: catmullPeriodic(keys.u, tNorm),
    m: catmullPeriodic(keys.m, tNorm),
    f: catmullPeriodic(keys.f, tNorm),
  }
}

// 方案 B+：真实鹿生物力学 8 关键帧表（旋转式疾驰 / 弹性对角小跑 / 四拍稳步慢行）。
const BIO_GAITS: Record<Exclude<DeerGait, 'idle'>, BioGaitConfig> = {
  gallop: {
    // 8 阶段：0=腾空最高点 1=下落伸展 2=前蹄着地缓冲 3=弓背收拢 4=后蹄着地蓄力 5=蹬地起跳 6=跃起升空 7=展背入空
    bodyY: [-14, -6, 4, 0, 5, -2, -10, -14],
    pitch: [-2, 6, 7, 1, -6, -11, -9, -5],
    flex: [1.0, 0.5, -0.4, -1.0, -0.7, 0.2, 0.8, 1.0],
    headPit: [1, -5, -6, -1, 5, 9, 7, 4],
    tail: [18, 10, -4, -12, -8, 5, 15, 19],
    front: {
      u: [-54, -40, -16, 12, 32, 16, -22, -48],
      m: [14, 4, 4, 8, 34, 94, 82, 42],
      f: [24, -4, -22, -16, 28, 28, 22, 22],
    },
    hind: {
      u: [54, 30, -10, -28, -16, 14, 42, 58],
      m: [-14, -4, 18, 10, 16, -8, -14, -16],
      f: [18, 24, 18, -4, -18, -20, -6, 14],
    },
    frontFarOffset: -0.09,
    hindFarOffset: 0.10,
    strikes: [
      { t: 0.25, x: 224 },
      { t: 0.34, x: 212 },
      { t: 0.50, x: 132 },
      { t: 0.41, x: 142 },
    ],
  },
  trot: {
    bodyY: [-4, 2, -2, -6, -4, 2, -2, -6],
    pitch: [-1, 2, 1, -1, -1, 2, 1, -1],
    flex: [0.2, -0.1, 0.1, 0.2, 0.2, -0.1, 0.1, 0.2],
    headPit: [0, -2, -1, 1, 0, -2, -1, 1],
    tail: [6, 2, 5, 8, 6, 2, 5, 8],
    front: {
      u: [-28, -12, 10, 26, 18, -6, -24, -32],
      m: [12, 4, 6, 24, 66, 54, 28, 14],
      f: [8, -14, -12, 18, 24, 18, 10, 8],
    },
    hind: {
      u: [32, 16, -8, -20, -10, 8, 26, 34],
      m: [-16, -8, 14, 8, -4, -14, -18, -18],
      f: [12, 14, 6, -12, -16, -12, -4, 10],
    },
    frontFarOffset: 0.5,
    hindFarOffset: 0.5,
    strikes: [
      { t: 0.14, x: 218 },
      { t: 0.64, x: 112 },
    ],
  },
  slow: {
    bodyY: [0, 1.5, 0.5, 0, 0, 1.5, 0.5, 0],
    pitch: [1, 2, 1, 0.5, 1, 2, 1, 0.5],
    flex: [0.05, -0.08, 0.02, 0.08, 0.05, -0.08, 0.02, 0.08],
    headPit: [5, 7, 6, 4.5, 5, 7, 6, 4.5],
    tail: [-4, -6, -4, -2, -4, -6, -4, -2],
    front: {
      u: [-14, -6, 4, 12, 14, 4, -8, -16],
      m: [6, 4, 5, 14, 34, 28, 14, 8],
      f: [2, -8, -6, 8, 14, 10, 4, 2],
    },
    hind: {
      u: [22, 12, 0, -8, -2, 8, 18, 24],
      m: [-16, -10, 6, 4, -8, -14, -18, -18],
      f: [6, 8, 2, -8, -10, -8, -2, 4],
    },
    frontFarOffset: 0.5,
    hindFarOffset: 0.5,
    hindPhaseShift: 0.25,
    strikes: [],
  },
}

export const IDLE_DEER_POSE: DeerPose = {
  bodyY: 0,
  pitch: 0,
  flex: 0,
  headPit: -1,
  tail: 0,
  frontNear: { u: -5, m: 6, f: -2 },
  frontFar: { u: 8, m: 4, f: -6 },
  hindNear: { u: 17, m: -19, f: -6 },
  hindFar: { u: 6, m: -13, f: -4 },
  strikes: [],
}

/** 根据归一化步态相位 t ∈ [0, 1) 与步态名称采样方案 B+ 全身姿态（含三段腿、动态脊柱屈伸与触地轻尘）。 */
export function sampleDeerPose(t: number, gait: DeerGait, pace: number): DeerPose {
  if (gait === 'idle' || pace <= 0.01) return IDLE_DEER_POSE
  const cfg = BIO_GAITS[gait]
  const hShift = cfg.hindPhaseShift ?? 0
  const tNorm = ((t % 1) + 1) % 1
  const strikes = cfg.strikes
    .map(st => {
      let age = tNorm - st.t
      if (age < 0) age += 1
      return { x: st.x, age }
    })
    .filter(st => st.age < 0.24)

  return {
    bodyY: catmullPeriodic(cfg.bodyY, tNorm),
    pitch: catmullPeriodic(cfg.pitch, tNorm),
    flex: catmullPeriodic(cfg.flex, tNorm),
    headPit: catmullPeriodic(cfg.headPit, tNorm),
    tail: catmullPeriodic(cfg.tail, tNorm),
    frontNear: sampleLeg3(cfg.front, tNorm),
    frontFar: sampleLeg3(cfg.front, tNorm + cfg.frontFarOffset),
    hindNear: sampleLeg3(cfg.hind, tNorm + hShift),
    hindFar: sampleLeg3(cfg.hind, tNorm + hShift + cfg.hindFarOffset),
    strikes,
  }
}
