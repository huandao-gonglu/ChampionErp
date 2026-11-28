<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  DEER_BASE_CYCLE_HZ,
  IDLE_DEER_POSE,
  sampleDeerPose,
  type DeerGait,
  type DeerPose,
  type Leg3Angles,
} from '@/utils/aiDeerMotion'

const props = withDefaults(defineProps<{
  gait: DeerGait
  pace: number
  size?: 'sm' | 'md'
  question?: boolean
}>(), {
  gait: 'trot',
  pace: 1,
  size: 'sm',
  question: false,
})

const sizeClass = computed(() => (props.size === 'md' ? 'h-6 w-9' : 'h-5 w-7'))

// 从 front/desktop/assets/icon.png (256×256) 直接矢量化提取的鹿头、鹿角、耳与眼部轮廓。
const ICON_HEAD_OUTLINE =
  'M71 37 L72 60 L74 66 L81 75 L83 75 L80 67 L81 46 L90 41 L89 66 L95 82 L101 86 L106 87 '
  + 'L103 81 L103 66 L110 60 L111 78 L118 92 L125 96 L129 96 L140 86 L140 68 L148 72 L148 90 '
  + 'L135 103 L136 107 L143 107 L144 102 L150 99 L159 88 L160 77 L166 71 L167 90 L164 94 '
  + 'L173 91 L175 73 L182 78 L182 88 L179 96 L155 107 L154 111 L164 117 L168 125 L193 141 '
  + 'L192 152 L183 162 L163 162 L156 165 L150 173 L147 183 L137 183 L143 166 L135 167 L128 158 '
  + 'L126 158 L124 176 L117 176 L121 143 L126 143 L137 158 L163 154 L182 154 L184 149 L181 149 '
  + 'L177 145 L177 139 L160 129 L159 123 L146 115 L133 115 L124 120 L119 120 L112 115 L98 113 '
  + 'L99 118 L104 123 L116 125 L118 130 L102 159 L93 155 L105 134 L105 131 L99 129 L91 120 '
  + 'L88 103 L116 108 L123 112 L123 109 L118 103 L81 87 L70 77 L63 61 L63 43 Z'
const ICON_HEAD_EYE = 'M139 124 L149 125 L157 135 L150 132 L141 132 L136 126 Z'
const ICON_HEAD_MASK =
  'M116 111 L148 112 L164 123 L188 142 L185 158 L161 159 L144 182 L94 178 L102 156 L108 130 L92 119 L92 108 Z'

// 方案 B++ · 雄壮公鹿版（厚实胸廓 + 高耸肩峰 + 饱满后臀 + 健壮三段四肢 + 0.63x 雄鹿头）。
const STAG_STROKE = 5.4
const SHOULDER_STROKE = STAG_STROKE * 0.85
const HIND_MUSCLE_STROKE = Number((STAG_STROKE * 0.72).toFixed(1))
const PACE_DAMPING = 6
const GAIT_TRANSITION_SEC = 0.22

const reducedMotion = ref(false)
// 归一化步态周期相位 t ∈ [0, 1)。
const cyclePhase = ref(0)
const smoothedPace = ref(props.gait === 'idle' ? 0 : props.pace)
const activeGait = ref<DeerGait>(props.gait)
const transitionBlend = ref(1)
let transitionFromPose: DeerPose | null = null

let frameId: number | undefined
let lastTimestamp: number | undefined
let motionMediaQuery: MediaQueryList | null = null

function lerp(from: number, to: number, t: number): number {
  return from + (to - from) * t
}

function blendLeg3(from: Leg3Angles, to: Leg3Angles, t: number): Leg3Angles {
  return {
    u: lerp(from.u, to.u, t),
    m: lerp(from.m, to.m, t),
    f: lerp(from.f, to.f, t),
  }
}

function blendPose(from: DeerPose, to: DeerPose, t: number): DeerPose {
  return {
    bodyY: lerp(from.bodyY, to.bodyY, t),
    pitch: lerp(from.pitch, to.pitch, t),
    flex: lerp(from.flex, to.flex, t),
    headPit: lerp(from.headPit, to.headPit, t),
    tail: lerp(from.tail, to.tail, t),
    frontNear: blendLeg3(from.frontNear, to.frontNear, t),
    frontFar: blendLeg3(from.frontFar, to.frontFar, t),
    hindNear: blendLeg3(from.hindNear, to.hindNear, t),
    hindFar: blendLeg3(from.hindFar, to.hindFar, t),
    strikes: to.strikes,
  }
}

function updateReducedMotion() {
  reducedMotion.value = Boolean(motionMediaQuery?.matches)
}

function stopLoop() {
  if (frameId !== undefined && typeof cancelAnimationFrame === 'function') {
    cancelAnimationFrame(frameId)
  }
  frameId = undefined
  lastTimestamp = undefined
}

function tick(timestamp: number) {
  if (lastTimestamp === undefined) lastTimestamp = timestamp
  const dt = Math.min(0.05, Math.max(0, (timestamp - lastTimestamp) / 1000))
  lastTimestamp = timestamp

  const targetPace = props.gait === 'idle' ? 0 : Math.max(0, props.pace)
  const blend = 1 - Math.exp(-dt * PACE_DAMPING)
  smoothedPace.value += (targetPace - smoothedPace.value) * blend
  cyclePhase.value = (cyclePhase.value + dt * DEER_BASE_CYCLE_HZ * smoothedPace.value) % 1
  if (transitionBlend.value < 1) {
    transitionBlend.value = Math.min(1, transitionBlend.value + dt / GAIT_TRANSITION_SEC)
    if (transitionBlend.value >= 1) transitionFromPose = null
  }

  if (!reducedMotion.value && props.gait !== 'idle') {
    frameId = requestAnimationFrame(tick)
  } else {
    stopLoop()
  }
}

function syncLoop() {
  if (reducedMotion.value || props.gait === 'idle') {
    stopLoop()
    smoothedPace.value = 0
    activeGait.value = props.gait
    transitionFromPose = null
    transitionBlend.value = 1
    return
  }
  if (activeGait.value !== props.gait) {
    transitionFromPose = pose.value
    activeGait.value = props.gait
    transitionBlend.value = 0
  }
  if (smoothedPace.value <= 0.05) {
    smoothedPace.value = Math.max(0.35, props.pace)
  }
  if (frameId === undefined && typeof requestAnimationFrame === 'function') {
    lastTimestamp = undefined
    frameId = requestAnimationFrame(tick)
  }
}

onMounted(() => {
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    motionMediaQuery = window.matchMedia('(prefers-reduced-motion: reduce)') ?? null
    updateReducedMotion()
    motionMediaQuery?.addEventListener?.('change', updateReducedMotion)
  }
  syncLoop()
})

watch([() => props.gait, () => props.pace, reducedMotion], () => {
  syncLoop()
})

onBeforeUnmount(() => {
  stopLoop()
  motionMediaQuery?.removeEventListener?.('change', updateReducedMotion)
})

const pose = computed<DeerPose>(() => {
  if (reducedMotion.value || props.gait === 'idle') return IDLE_DEER_POSE
  const target = sampleDeerPose(cyclePhase.value, activeGait.value, smoothedPace.value)
  if (transitionFromPose && transitionBlend.value < 1) {
    const eased = 1 - Math.pow(1 - transitionBlend.value, 2)
    return blendPose(transitionFromPose, target, eased)
  }
  return target
})

// 方案 B++ 雄壮公鹿动态屈伸脊柱（高鬐甲、宽厚背脊、深厚前胸与饱满后臀）。
const spine = computed(() => {
  const fl = pose.value.flex
  const shX = fl * 6.0
  const hpX = -fl * 6.5
  const archY = fl < 0 ? fl * 8.0 : fl * 2.2
  const croupDrop = fl < 0 ? -fl * 4.0 : -fl * 1.5

  const wX = 207 + shX
  const wY = 88
  const b1X = 183 + shX * 0.55
  const b1Y = 92 + archY * 0.5
  const b2X = 142 + hpX * 0.4
  const b2Y = 92 + archY
  const rX = 107 + hpX
  const rY = 88 + croupDrop * 0.6
  const tX = 90 + hpX
  const tY = 96 + croupDrop
  const hBackX = 84 + hpX
  const hBackY = 113 + croupDrop

  const chTopX = 236 + shX
  const chTopY = 104
  const chFwdX = 242 + shX
  const chFwdY = 122
  const chBotX = 229 + shX
  const chBotY = 139
  const bel1X = 206 + shX * 0.8
  const bel1Y = 141 + archY * 0.2
  const bel2X = 184 + shX * 0.4
  const bel2Y = 138 + archY * 0.35
  const bel3X = 154 + hpX * 0.3
  const bel3Y = 133 + archY * 0.65
  const groinX = 125 + hpX
  const groinY = 125 + croupDrop * 0.5 + archY * 0.3

  const f = (n: number) => n.toFixed(1)
  return {
    shX,
    hpX,
    croupDrop,
    headPivotX: 222 + shX,
    frontNearPivot: [219 + shX, 125] as const,
    frontFarPivot: [210 + shX, 124] as const,
    hindNearPivot: [105 + hpX, 113 + croupDrop] as const,
    hindFarPivot: [98 + hpX, 112 + croupDrop] as const,
    backD: `M${f(wX)} ${f(wY)} L${f(b1X)} ${f(b1Y)} L${f(b2X)} ${f(b2Y)} L${f(rX)} ${f(rY)} L${f(tX)} ${f(tY)} L${f(hBackX)} ${f(hBackY)}`,
    chestD: `M${f(chTopX)} ${f(chTopY)} L${f(chFwdX)} ${f(chFwdY)} L${f(chBotX)} ${f(chBotY)}`,
    bellyD: `M${f(bel1X)} ${f(bel1Y)} L${f(bel2X)} ${f(bel2Y)} L${f(bel3X)} ${f(bel3Y)} L${f(groinX)} ${f(groinY)}`,
    maskD:
      `M${f(wX)} ${f(wY)} L${f(b1X)} ${f(b1Y)} L${f(b2X)} ${f(b2Y)} `
      + `L${f(rX)} ${f(rY)} L${f(tX)} ${f(tY)} L${f(hBackX)} ${f(hBackY)} `
      + `L${f(102 + hpX)} ${f(128 + croupDrop)} L${f(groinX)} ${f(groinY)} `
      + `L${f(bel3X)} ${f(bel3Y)} L${f(bel2X)} ${f(bel2Y)} L${f(bel1X)} ${f(bel1Y)} `
      + `L${f(chBotX)} ${f(chBotY)} L${f(chFwdX)} ${f(chFwdY)} L${f(chTopX)} ${f(chTopY)} L${f(224 + shX)} 91 Z`,
    shoulderCrease: `M${f(208 + shX)} 101 L${f(196 + shX)} 119 L${f(203 + shX)} 132`,
  }
})

// 蹄尖真实触地瞬间产生的极简几何尘线（随时间向后飘散消隐）。
const dustLines = computed(() => pose.value.strikes.map((st, idx) => {
  const p = st.age / 0.24
  const alpha = Math.max(0, (1 - p) * 0.58)
  const dx = p * 34
  const len = 18 + p * 12
  return {
    key: idx,
    x1: (st.x - dx - len).toFixed(1),
    x2: (st.x - dx).toFixed(1),
    opacity: alpha.toFixed(2),
  }
}))

const colorClass = computed(() => {
  if (props.gait === 'idle') return 'text-amber-600 dark:text-amber-400'
  if (props.gait === 'slow') return 'text-slate-400 dark:text-accent-400'
  return 'text-slate-900 dark:text-accent-100'
})

const bodyTransform = computed(() => (
  `translate(0 ${(pose.value.bodyY + 21).toFixed(1)}) rotate(${pose.value.pitch.toFixed(1)} 164 118)`
))
const tailTransform = computed(() => (
  `translate(${spine.value.hpX.toFixed(1)} ${spine.value.croupDrop.toFixed(1)}) rotate(${pose.value.tail.toFixed(1)} 90 96)`
))
const headTransform = computed(() => (
  `rotate(${pose.value.headPit.toFixed(1)} ${spine.value.headPivotX.toFixed(1)} 96) translate(${spine.value.shX.toFixed(1)} 0)`
))

function legTransform(pivot: readonly [number, number], angle: number): string {
  return `translate(${pivot[0].toFixed(1)} ${pivot[1].toFixed(1)}) rotate(${angle.toFixed(1)})`
}
function jointTransform(length: number, angle: number): string {
  return `translate(0 ${length}) rotate(${angle.toFixed(1)})`
}
</script>

<template>
  <span
    class="inline-flex shrink-0 items-center justify-center transition-colors duration-300"
    :class="[sizeClass, colorClass]"
    aria-hidden="true"
    data-testid="ai-running-deer"
    :data-gait="gait"
    :data-pace="pace.toFixed(2)"
  >
    <svg
      viewBox="0 0 320 256"
      class="overflow-visible"
      :class="sizeClass"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <!-- 蹄尖触地瞬间的极简几何尘线 -->
      <line
        v-for="dust in dustLines"
        :key="dust.key"
        :x1="dust.x1"
        y1="242"
        :x2="dust.x2"
        y2="242"
        stroke="currentColor"
        stroke-width="4.6"
        :opacity="dust.opacity"
      />

      <g :transform="bodyTransform">
        <!-- 远侧健壮 3 段后腿 -->
        <g opacity="0.36" :transform="legTransform(spine.hindFarPivot, pose.hindFar.u)">
          <path d="M-17.5 -5 L17.5 -3 L5.3 45 L-5.0 45 Z" class="fill-white dark:fill-dark-900" />
          <path
            d="M-17.5 1 L-5.0 45 M17.5 6 L5.3 45"
            stroke="currentColor"
            :stroke-width="STAG_STROKE"
            stroke-linecap="square"
          />
          <g :transform="jointTransform(44, pose.hindFar.m)">
            <path d="M-3.7 0 L3.8 0 L3.0 37 L-3.0 37 Z" fill="currentColor" />
            <g :transform="jointTransform(36, pose.hindFar.f)">
              <path d="M-3.0 0 L3.1 0 L2.7 10 L7.4 15.5 L-3.1 15.5 Z" fill="currentColor" />
            </g>
          </g>
        </g>

        <!-- 远侧健壮 3 段前腿 -->
        <g opacity="0.36" :transform="legTransform(spine.frontFarPivot, pose.frontFar.u)">
          <path d="M-11 0 L11 0 L4.8 39 L-4.5 39 Z" class="fill-white dark:fill-dark-900" />
          <path
            d="M-11 9 L-4.5 39 M11 4 L4.8 39"
            stroke="currentColor"
            :stroke-width="STAG_STROKE"
            stroke-linecap="square"
          />
          <g :transform="jointTransform(38, pose.frontFar.m)">
            <path d="M-3.5 0 L3.6 0 L2.9 35 L-2.9 35 Z" fill="currentColor" />
            <g :transform="jointTransform(34, pose.frontFar.f)">
              <path d="M-2.9 0 L3.0 0 L2.7 10 L7.4 15.5 L-3.1 15.5 Z" fill="currentColor" />
            </g>
          </g>
        </g>

        <!-- 短尾（联动髋部屈伸） -->
        <g :transform="tailTransform">
          <polygon points="92,94 72,86 83,103" fill="currentColor" />
        </g>

        <!-- 镂空躯干遮罩 -->
        <path :d="spine.maskD" class="fill-white dark:fill-dark-900" />

        <!-- 近侧健壮 3 段后腿（含后臀肌理折线） -->
        <g :transform="legTransform(spine.hindNearPivot, pose.hindNear.u)">
          <path d="M-17.5 -5 L17.5 -3 L5.3 45 L-5.0 45 Z" class="fill-white dark:fill-dark-900" />
          <path
            d="M-17.5 1 L-5.0 45 M17.5 6 L5.3 45"
            stroke="currentColor"
            :stroke-width="STAG_STROKE"
            stroke-linecap="square"
          />
          <path
            d="M-4 4 L4 18 L-1 28"
            stroke="currentColor"
            :stroke-width="HIND_MUSCLE_STROKE"
            stroke-linecap="square"
            opacity="0.85"
          />
          <g :transform="jointTransform(44, pose.hindNear.m)">
            <path d="M-3.7 0 L3.8 0 L3.0 37 L-3.0 37 Z" fill="currentColor" />
            <g :transform="jointTransform(36, pose.hindNear.f)">
              <path d="M-3.0 0 L3.1 0 L2.7 10 L7.4 15.5 L-3.1 15.5 Z" fill="currentColor" />
            </g>
          </g>
        </g>

        <!-- 近侧健壮 3 段前腿 -->
        <g :transform="legTransform(spine.frontNearPivot, pose.frontNear.u)">
          <path d="M-11 0 L11 0 L4.8 39 L-4.5 39 Z" class="fill-white dark:fill-dark-900" />
          <path
            d="M-11 9 L-4.5 39 M11 4 L4.8 39"
            stroke="currentColor"
            :stroke-width="STAG_STROKE"
            stroke-linecap="square"
          />
          <g :transform="jointTransform(38, pose.frontNear.m)">
            <path d="M-3.5 0 L3.6 0 L2.9 35 L-2.9 35 Z" fill="currentColor" />
            <g :transform="jointTransform(34, pose.frontNear.f)">
              <path d="M-2.9 0 L3.0 0 L2.7 10 L7.4 15.5 L-3.1 15.5 Z" fill="currentColor" />
            </g>
          </g>
        </g>

        <!-- 雄壮公鹿动态躯干描边与雕塑感肩胛折线 -->
        <path
          :d="spine.backD"
          stroke="currentColor"
          :stroke-width="STAG_STROKE"
          stroke-linejoin="miter"
          stroke-miterlimit="6"
          stroke-linecap="square"
        />
        <path
          :d="spine.chestD"
          stroke="currentColor"
          :stroke-width="STAG_STROKE"
          stroke-linejoin="miter"
          stroke-miterlimit="6"
          stroke-linecap="square"
        />
        <path
          :d="spine.bellyD"
          stroke="currentColor"
          :stroke-width="STAG_STROKE"
          stroke-linejoin="miter"
          stroke-miterlimit="6"
          stroke-linecap="square"
        />
        <path
          :d="spine.shoulderCrease"
          stroke="currentColor"
          :stroke-width="SHOULDER_STROKE"
          stroke-linejoin="miter"
          stroke-miterlimit="6"
          stroke-linecap="square"
        />

        <!-- 0.63x 雄鹿头（视平线稳定） -->
        <g :transform="headTransform">
          <g transform="translate(146 -11) scale(0.63)">
            <path :d="ICON_HEAD_MASK" class="fill-white dark:fill-dark-900" />
            <path :d="ICON_HEAD_OUTLINE" fill="currentColor" />
            <path :d="ICON_HEAD_EYE" fill="currentColor" />
          </g>
        </g>
      </g>

      <!-- 等待确认/回复时浮现在鹿头右上方的小气泡问号 -->
      <g
        v-if="question"
        transform="translate(284 18)"
        data-testid="ai-deer-question"
      >
        <g class="ai-deer-question-bubble">
          <path
            d="M 0 -50 C 30 -50, 52 -29, 52 0 C 52 28, 30 48, 0 48 C -9 48, -17 46, -24 42 L -46 54 L -36 32 C -46 23, -52 12, -52 0 C -52 -29, -30 -50, 0 -50 Z"
            fill="currentColor"
          />
          <path
            d="M -12 -17 C -12 -27, -4 -31, 3 -31 C 12 -31, 17 -26, 17 -17 C 17 -9, 9 -5, 4 -2 C 1 0, 0 3, 0 6"
            class="stroke-white dark:stroke-dark-900"
            stroke-width="12"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
          <circle cx="0" cy="26" r="6.2" class="fill-white dark:fill-dark-900" />
        </g>
      </g>
    </svg>
  </span>
</template>

<style scoped>
@keyframes ai-deer-question-float {
  0%,
  100% {
    transform: translate(0px, 0px) scale(1);
  }
  50% {
    transform: translate(0px, -8px) scale(1.04);
  }
}

.ai-deer-question-bubble {
  transform-box: fill-box;
  transform-origin: left bottom;
  animation: ai-deer-question-float 2.5s ease-in-out infinite;
}

@media (prefers-reduced-motion: reduce) {
  .ai-deer-question-bubble {
    animation: none;
  }
}
</style>
