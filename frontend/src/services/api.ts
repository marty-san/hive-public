import axios from 'axios';
import type {
  Agent,
  AgentCreate,
  Conversation,
  ConversationCreate,
  Message,
  MessageCreate,
  HealthResponse,
  WhiteboardEntry,
  WhiteboardLogEntry,
} from '@/types';

const api = axios.create({
  baseURL: '/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

// Health
export const healthApi = {
  check: () => api.get<HealthResponse>('/health'),
};

// Agents
export const agentsApi = {
  list: () => api.get<Agent[]>('/agents'),
  get: (id: string) => api.get<Agent>(`/agents/${id}`),
  create: (data: AgentCreate) => api.post<Agent>('/agents', data),
  update: (id: string, data: Partial<AgentCreate>) =>
    api.put<Agent>(`/agents/${id}`, data),
  delete: (id: string) => api.delete(`/agents/${id}`),
};

// Conversations
export const conversationsApi = {
  list: (status?: string) => {
    const params = status ? { status } : {};
    return api.get<Conversation[]>('/conversations', { params });
  },
  get: (id: string) => api.get<Conversation>(`/conversations/${id}`),
  create: (data: ConversationCreate) =>
    api.post<Conversation>('/conversations', data),
  update: (id: string, data: Partial<ConversationCreate>) =>
    api.put<Conversation>(`/conversations/${id}`, data),
  delete: (id: string, deleteMemories: boolean = false) =>
    api.delete(`/conversations/${id}`, {
      params: { delete_memories: deleteMemories }
    }),
  addParticipant: (conversationId: string, agentId: string) =>
    api.post(`/conversations/${conversationId}/participants`, { agent_id: agentId }),
  removeParticipant: (conversationId: string, agentId: string) =>
    api.delete(`/conversations/${conversationId}/participants/${agentId}`),
};

// Messages
export const messagesApi = {
  list: (conversationId: string, limit = 10000, offset = 0) =>
    api.get<Message[]>(`/conversations/${conversationId}/messages`, {
      params: { limit, offset },
    }),
  get: (conversationId: string, messageId: string) =>
    api.get<Message>(`/conversations/${conversationId}/messages/${messageId}`),
  create: (conversationId: string, data: Omit<MessageCreate, 'conversation_id'>) =>
    api.post<Message>(`/conversations/${conversationId}/messages`, {
      ...data,
      conversation_id: conversationId,
    }),
};

// Conversation settings & proposals
export const conversationSettingsApi = {
  get: (conversationId: string) =>
    api.get<{ human_votes_on_proposals: boolean }>(`/conversations/${conversationId}/settings`),
  update: (conversationId: string, data: { human_votes_on_proposals: boolean }) =>
    api.put<{ human_votes_on_proposals: boolean }>(`/conversations/${conversationId}/settings`, data),
  voteOnProposal: (conversationId: string, proposalId: string, vote: 'approve' | 'reject') =>
    api.post(`/conversations/${conversationId}/proposals/${proposalId}/vote`, { vote }),
};

// Chat
export const chatApi = {
  triggerAgent: (conversationId: string, agentId: string) =>
    api.post<Message>(`/conversations/${conversationId}/trigger-agent`, {
      agent_id: agentId,
    }),
  triggerMultiAgent: (conversationId: string) =>
    api.post(`/conversations/${conversationId}/trigger-multi-agent`),
  startAutonomous: (conversationId: string) =>
    api.post(`/conversations/${conversationId}/start-autonomous`),
  pauseConversation: (conversationId: string) =>
    api.post(`/conversations/${conversationId}/pause`),
  interruptDiscussion: (conversationId: string) =>
    api.post(`/conversations/${conversationId}/interrupt`),
  rewindConversation: (conversationId: string, messageId: string) =>
    api.post(`/conversations/${conversationId}/rewind`, { message_id: messageId }),
  summarize: (conversationId: string) =>
    api.post<{ summary: string; message: Message }>(`/conversations/${conversationId}/summarize`),
};

// Whiteboard
export const whiteboardApi = {
  get: (conversationId: string) =>
    api.get<WhiteboardEntry[]>(`/conversations/${conversationId}/whiteboard`),
  getHistory: (conversationId: string, key?: string) =>
    api.get<WhiteboardLogEntry[]>(
      `/conversations/${conversationId}/whiteboard/history`,
      { params: key ? { key } : {} }
    ),
  set: (
    conversationId: string,
    key: string,
    body: { entry_type: string; value: string; reason: string }
  ) =>
    api.put<WhiteboardEntry[]>(
      `/conversations/${conversationId}/whiteboard/${key}`,
      body
    ),
  remove: (conversationId: string, key: string, reason: string) =>
    api.delete<WhiteboardEntry[]>(
      `/conversations/${conversationId}/whiteboard/${key}`,
      { data: { reason } }
    ),
};

export default api;
