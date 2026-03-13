import { create } from 'zustand';
import type { WhiteboardEntry } from '@/types';
import { whiteboardApi } from '@/services/api';

interface WhiteboardStore {
  entries: Record<string, WhiteboardEntry[]>; // keyed by conversationId

  fetchEntries: (conversationId: string) => Promise<void>;
  setEntry: (
    conversationId: string,
    key: string,
    body: { entry_type: string; value: string; reason: string }
  ) => Promise<void>;
  removeEntry: (conversationId: string, key: string, reason: string) => Promise<void>;
  applyUpdate: (conversationId: string, entries: WhiteboardEntry[]) => void;
}

export const useWhiteboardStore = create<WhiteboardStore>((set) => ({
  entries: {},

  fetchEntries: async (conversationId) => {
    try {
      const response = await whiteboardApi.get(conversationId);
      set((state) => ({
        entries: { ...state.entries, [conversationId]: response.data },
      }));
    } catch (error) {
      console.error('Failed to fetch whiteboard entries:', error);
    }
  },

  setEntry: async (conversationId, key, body) => {
    const response = await whiteboardApi.set(conversationId, key, body);
    set((state) => ({
      entries: { ...state.entries, [conversationId]: response.data },
    }));
  },

  removeEntry: async (conversationId, key, reason) => {
    const response = await whiteboardApi.remove(conversationId, key, reason);
    set((state) => ({
      entries: { ...state.entries, [conversationId]: response.data },
    }));
  },

  applyUpdate: (conversationId, entries) => {
    set((state) => ({
      entries: { ...state.entries, [conversationId]: entries },
    }));
  },
}));
