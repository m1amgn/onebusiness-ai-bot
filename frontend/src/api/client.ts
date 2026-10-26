import type { ZodType } from 'zod';

import { zApiError, zRealtimeTicket, zTaskPage, zTaskView } from './index';
import type { ChangeTaskStatusData, DeleteTaskData, ListTasksData } from './index';

export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
    this.name = 'ApiRequestError';
  }
}

type ApiClientOptions = {
  token: string;
  fetchImpl?: typeof fetch;
};

export const createApiClient = ({ token, fetchImpl = fetch }: ApiClientOptions) => {
  const requestError = (status: number, detail: string): ApiRequestError =>
    new ApiRequestError(status, token ? detail.replaceAll(token, '[redacted]') : detail);

  const send = async (url: string, init: RequestInit): Promise<Response> => {
    let response: Response;
    try {
      const headers = new Headers(init.headers);
      headers.set('Authorization', `Bearer ${token}`);
      response = await fetchImpl(url, { ...init, headers });
    } catch {
      throw requestError(0, 'Could not reach the server.');
    }

    if (response.ok) return response;

    const body: unknown = await response.json().catch(() => null);
    const error = zApiError.safeParse(body);
    throw requestError(response.status, error.success ? error.data.detail : 'The request failed.');
  };

  const request = async <T>(url: string, init: RequestInit, schema: ZodType<T>): Promise<T> => {
    const response = await send(url, init);
    try {
      const body: unknown = await response.json();
      return schema.parse(body);
    } catch {
      throw requestError(0, 'The server returned an invalid response.');
    }
  };

  const listTasks = (query: NonNullable<ListTasksData['query']>, signal?: AbortSignal) => {
    const parameters = new URLSearchParams();
    if (query.limit !== undefined) parameters.set('limit', String(query.limit));
    if (query.cursor != null) parameters.set('cursor', query.cursor);
    if (query.status != null) parameters.set('status', query.status);
    const search = parameters.toString();

    return request(`/api/tasks${search ? `?${search}` : ''}`, { method: 'GET', signal }, zTaskPage);
  };

  const changeStatus = (
    id: ChangeTaskStatusData['path']['task_id'],
    status: ChangeTaskStatusData['body']['status'],
    signal?: AbortSignal,
  ) =>
    request(
      `/api/tasks/${String(id)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
        signal,
      },
      zTaskView,
    );

  const deleteTask = async (
    id: DeleteTaskData['path']['task_id'],
    signal?: AbortSignal,
  ): Promise<void> => {
    await send(`/api/tasks/${String(id)}`, { method: 'DELETE', signal });
  };

  const issueTicket = (signal?: AbortSignal) => {
    const timeout = AbortSignal.timeout(10_000);
    const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
    return request('/api/realtime/tickets', { method: 'POST', signal: combined }, zRealtimeTicket);
  };

  return { listTasks, changeStatus, deleteTask, issueTicket };
};

export type ApiClient = ReturnType<typeof createApiClient>;
