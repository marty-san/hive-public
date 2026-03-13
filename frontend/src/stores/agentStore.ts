import { create } from 'zustand';
import type { Agent } from '@/types';
import { agentsApi } from '@/services/api';

interface AgentStore {
  agents: Agent[];
  loading: boolean;
  error: string | null;
  fetchAgents: () => Promise<void>;
  createAgent: (agent: Omit<Agent, 'id' | 'created_at' | 'updated_at'>) => Promise<Agent>;
  updateAgent: (id: string, updates: Partial<Agent>) => Promise<void>;
  deleteAgent: (id: string) => Promise<void>;
}

export const useAgentStore = create<AgentStore>((set, get) => ({
  agents: [],
  loading: false,
  error: null,

  fetchAgents: async () => {
    set({ loading: true, error: null });
    try {
      const response = await agentsApi.list();
      set({ agents: response.data, loading: false });
    } catch (error: any) {
      set({ error: error.message, loading: false });
    }
  },

  createAgent: async (agentData) => {
    set({ loading: true, error: null });
    try {
      const response = await agentsApi.create(agentData);
      set((state) => ({
        agents: [...state.agents, response.data],
        loading: false,
      }));
      return response.data;
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  updateAgent: async (id, updates) => {
    set({ loading: true, error: null });
    try {
      const response = await agentsApi.update(id, updates);
      set((state) => ({
        agents: state.agents.map((a) => (a.id === id ? response.data : a)),
        loading: false,
      }));
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },

  deleteAgent: async (id) => {
    set({ loading: true, error: null });
    try {
      await agentsApi.delete(id);
      set((state) => ({
        agents: state.agents.filter((a) => a.id !== id),
        loading: false,
      }));
    } catch (error: any) {
      set({ error: error.message, loading: false });
      throw error;
    }
  },
}));
