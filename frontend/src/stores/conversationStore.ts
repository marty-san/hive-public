import { create } from 'zustand';
import type { Conversation, Message, ConversationCreate } from '@/types';
import { conversationsApi, messagesApi, chatApi } from '@/services/api';

interface ConversationStore {
  conversations: Conversation[];
  currentConversation: Conversation | null;
  messages: Record<string, Message[]>;
  loading: boolean;
  error: string | null;

  fetchConversations: () => Promise<void>;
  fetchConversation: (id: string) => Promise<void>;
  createConversation: (data: ConversationCreate) => Promise<Conversation>;
  updateConversation: (id: string, updates: Partial<Conversation>) => Promise<void>;
  deleteConversation: (id: string, deleteMemories?: boolean) => Promise<void>;

  addParticipant: (conversationId: string, agentId: string) => Promise<void>;
  removeParticipant: (conversationId: string, agentId: string) => Promise<void>;

  fetchMessages: (conversationId: string) => Promise<void>;
  sendMessage: (conversationId: string, content: string) => Promise<void>;
  addMessage: (message: Message) => void;

  setCurrentConversation: (conversation: Conversation | null) => void;
  triggerAgentResponse: (conversationId: string, agentId: string) => Promise<void>;
  triggerMultiAgentResponse: (conversationId: string) => Promise<void>;
  startAutonomousMode: (conversationId: string) => Promise<void>;
  pauseConversation: (conversationId: string) => Promise<void>;
  interruptDiscussion: (conversationId: string) => Promise<void>;
  rewindConversation: (conversationId: string, messageId: string) => Promise<void>;
  renameConversation: (id: string, title: string) => Promise<void>;
}

export const useConversationStore = create<ConversationStore>((set, get) => ({
  conversations: [],
  currentConversation: null,
  messages: {},
  loading: false,
  error: null,

  fetchConversations: async () => {
    set({ loading: true, error: null });
    try {
      const response = await conversationsApi.list();
      set({ conversations: response.data, loading: false });
    } catch (error: any) {
      set({ error: error.message, loading: false });
    }
  },

  fetchConversation: async (id) => {
    set({ loading: true, error: null });
    try {
      const response = await conversationsApi.get(id);
      set({ currentConversation: response.data, loading: false });
    } catch (error: any) {
      set({ error: error.message, loading: false });
    }
  },

  createConversation: async (data) => {
    set({ loading: true, error: null });
    try {
      const response = await conversationsApi.create(data);
      set((state) => ({
        conversations: [response.data, ...state.conversations],
        loading: false,
      }));
      return response.data;
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  updateConversation: async (id, updates) => {
    set({ loading: true, error: null });
    try {
      const response = await conversationsApi.update(id, updates);
      set((state) => ({
        conversations: state.conversations.map((c) =>
          c.id === id ? response.data : c
        ),
        currentConversation:
          state.currentConversation?.id === id
            ? response.data
            : state.currentConversation,
        loading: false,
      }));
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  deleteConversation: async (id, deleteMemories = false) => {
    set({ loading: true, error: null });
    try {
      await conversationsApi.delete(id, deleteMemories);
      set((state) => ({
        conversations: state.conversations.filter((c) => c.id !== id),
        currentConversation:
          state.currentConversation?.id === id ? null : state.currentConversation,
        loading: false,
      }));
      return true;
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  renameConversation: async (id: string, title: string) => {
    set({ loading: true, error: null });
    try {
      const response = await conversationsApi.update(id, { title });
      set((state) => ({
        conversations: state.conversations.map((c) =>
          c.id === id ? response.data : c
        ),
        currentConversation:
          state.currentConversation?.id === id
            ? response.data
            : state.currentConversation,
        loading: false,
      }));
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  addParticipant: async (conversationId, agentId) => {
    set({ loading: true, error: null });
    try {
      await conversationsApi.addParticipant(conversationId, agentId);
      // Refetch conversation to get updated participant list
      await get().fetchConversation(conversationId);
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  removeParticipant: async (conversationId, agentId) => {
    set({ loading: true, error: null });
    try {
      await conversationsApi.removeParticipant(conversationId, agentId);
      // Refetch conversation to get updated participant list
      await get().fetchConversation(conversationId);
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  fetchMessages: async (conversationId) => {
    set({ loading: true, error: null });
    try {
      const response = await messagesApi.list(conversationId);
      set((state) => ({
        messages: {
          ...state.messages,
          [conversationId]: response.data,
        },
        loading: false,
      }));
    } catch (error: any) {
      set({ error: error.message, loading: false });
    }
  },

  sendMessage: async (conversationId, content) => {
    set({ loading: true, error: null });
    try {
      const response = await messagesApi.create(conversationId, {
        content,
        sender_type: 'human',
      });
      // Use addMessage to prevent duplicates when WebSocket also sends it
      get().addMessage(response.data);
      set({ loading: false });
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  addMessage: (message) => {
    set((state) => {
      const existingMessages = state.messages[message.conversation_id] || [];

      // Check if message already exists (prevent duplicates)
      if (existingMessages.some(m => m.id === message.id)) {
        return state; // No change
      }

      return {
        messages: {
          ...state.messages,
          [message.conversation_id]: [...existingMessages, message],
        },
      };
    });
  },

  setCurrentConversation: (conversation) => {
    set({ currentConversation: conversation });
  },

  triggerAgentResponse: async (conversationId, agentId) => {
    set({ loading: true, error: null });
    try {
      const response = await chatApi.triggerAgent(conversationId, agentId);
      // Message will be added via WebSocket, but add it here too for immediate feedback
      set((state) => ({
        messages: {
          ...state.messages,
          [conversationId]: [
            ...(state.messages[conversationId] || []),
            response.data,
          ],
        },
        loading: false,
      }));
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  triggerMultiAgentResponse: async (conversationId) => {
    set({ loading: true, error: null });
    try {
      await chatApi.triggerMultiAgent(conversationId);
      // Messages will be added via WebSocket
      set({ loading: false });
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  startAutonomousMode: async (conversationId) => {
    set({ loading: true, error: null });
    try {
      await chatApi.startAutonomous(conversationId);
      // Update conversation mode
      set((state) => ({
        currentConversation: state.currentConversation
          ? { ...state.currentConversation, mode: 'autonomous' }
          : null,
        loading: false,
      }));
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  pauseConversation: async (conversationId) => {
    set({ loading: true, error: null });
    try {
      await chatApi.pauseConversation(conversationId);
      set((state) => ({
        currentConversation: state.currentConversation
          ? { ...state.currentConversation, mode: 'interactive' }
          : null,
        loading: false,
      }));
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  interruptDiscussion: async (conversationId) => {
    set({ loading: true, error: null });
    try {
      await chatApi.interruptDiscussion(conversationId);
      set({ loading: false });
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  rewindConversation: async (conversationId, messageId) => {
    set({ loading: true, error: null });
    try {
      await chatApi.rewindConversation(conversationId, messageId);

      // Remove messages after the target message in local state
      set((state) => {
        const currentMessages = state.messages[conversationId] || [];
        const targetMessageIndex = currentMessages.findIndex(m => m.id === messageId);

        if (targetMessageIndex === -1) {
          return { loading: false }; // Target message not found
        }

        // Keep only messages up to and including the target message
        const messagesAfterRewind = currentMessages.slice(0, targetMessageIndex + 1);

        return {
          messages: {
            ...state.messages,
            [conversationId]: messagesAfterRewind,
          },
          loading: false,
        };
      });
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },
}));
