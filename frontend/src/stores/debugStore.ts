import { create } from 'zustand';

export interface DebugEvent {
  id: string;
  timestamp: string;
  event_type: 'agent_selection' | 'memory_retrieval' | 'response_generated' | 'api_error' | string;
  conversation_id: string;
  data: any;
}

interface DebugStore {
  events: DebugEvent[];
  enabled: boolean;
  addDebugEvent: (event: Omit<DebugEvent, 'id'>) => void;
  clearDebugEvents: () => void;
  toggleDebug: () => void;
}

export const useDebugStore = create<DebugStore>((set) => ({
  events: [],
  enabled: false,

  addDebugEvent: (event) =>
    set((state) => ({
      events: [
        ...state.events,
        {
          ...event,
          id: `${Date.now()}-${Math.random()}`,
        },
      ].slice(-100), // Keep last 100 events
    })),

  clearDebugEvents: () => set({ events: [] }),

  toggleDebug: () => set((state) => ({ enabled: !state.enabled })),
}));
