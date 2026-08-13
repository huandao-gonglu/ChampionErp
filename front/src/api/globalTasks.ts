import { apiClient } from './client'
import type {
  GlobalTaskCancelRequest,
  GlobalTaskInputRequest,
  GlobalTaskPublishConfirmRequest,
  GlobalTaskResponse,
  GlobalTaskStartRequest,
  GlobalTaskStateRequest,
} from '@/types/globalTasks'

const GLOBAL_TASK_START_PATH = '/api/global-task-start'
const GLOBAL_TASK_STATE_PATH = '/api/global-task-state'
const GLOBAL_TASK_INPUT_PATH = '/api/global-task-input'
const GLOBAL_TASK_PUBLISH_CONFIRM_PATH = '/api/global-task-publish-confirm'
const GLOBAL_TASK_CANCEL_PATH = '/api/global-task-cancel'

async function postGlobalTask(
  path: string,
  payload: GlobalTaskStartRequest
    | GlobalTaskStateRequest
    | GlobalTaskInputRequest
    | GlobalTaskPublishConfirmRequest
    | GlobalTaskCancelRequest,
): Promise<GlobalTaskResponse> {
  const response = await apiClient.post<GlobalTaskResponse>(path, payload)
  return response.data
}

export function startGlobalTask(payload: GlobalTaskStartRequest): Promise<GlobalTaskResponse> {
  return postGlobalTask(GLOBAL_TASK_START_PATH, payload)
}

export function fetchGlobalTaskState(taskId: string): Promise<GlobalTaskResponse> {
  return postGlobalTask(GLOBAL_TASK_STATE_PATH, { task_id: taskId })
}

export function submitGlobalTaskInput(payload: GlobalTaskInputRequest): Promise<GlobalTaskResponse> {
  return postGlobalTask(GLOBAL_TASK_INPUT_PATH, payload)
}

export function confirmGlobalTaskPublish(taskId: string): Promise<GlobalTaskResponse> {
  return postGlobalTask(GLOBAL_TASK_PUBLISH_CONFIRM_PATH, { task_id: taskId })
}

export function cancelGlobalTask(taskId: string): Promise<GlobalTaskResponse> {
  return postGlobalTask(GLOBAL_TASK_CANCEL_PATH, { task_id: taskId })
}
