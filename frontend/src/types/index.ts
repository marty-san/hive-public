export interface Agent {
  id: string;
  name: string;
  expertise_domain: string;
  system_prompt: string;
  communication_style?: string;
  model?: string;
  participation_criteria?: Record<string, any>;
  extra_data?: Record<string, any>;
  created_at: string;
  updated_at: string;
}

export interface AgentCreate {
  name: string;
  expertise_domain: string;
  system_prompt: string;
  communication_style?: string;
  model?: string;
  participation_criteria?: Record<string, any>;
  extra_data?: Record<string, any>;
}

export interface Conversation {
  id: string;
  title?: string;
  status: 'active' | 'paused' | 'completed' | 'archived';
  mode: 'interactive' | 'autonomous';
  max_autonomous_turns: number;
  requires_human_for_decisions: boolean;
  extra_data?: Record<string, any>;
  created_at: string;
  updated_at: string;
  participant_ids: string[];
}

export interface ConversationCreate {
  title?: string;
  max_autonomous_turns?: number;
  requires_human_for_decisions?: boolean;
  extra_data?: Record<string, any>;
  initial_participants?: string[];
}

export interface Message {
  id: string;
  conversation_id: string;
  sender_type: 'human' | 'agent' | 'system';
  sender_id?: string;
  sender_name?: string;
  content: string;
  created_at: string;
  requires_human_decision: boolean;
  decision_resolved: boolean;
  extra_data?: Record<string, any>;
}

export interface MessageCreate {
  conversation_id: string;
  content: string;
  sender_type?: 'human' | 'agent' | 'system';
  sender_id?: string;
  attachments?: any[];
  extra_data?: Record<string, any>;
}

export interface HealthResponse {
  status: string;
  version: string;
  database: string;
}

export interface WhiteboardEntry {
  id: string;
  key: string;
  entry_type: 'goal' | 'decision' | 'constraint' | 'open_question' | 'strategy';
  value: string;
  last_author_name: string;
  last_author_type: 'agent' | 'human';
  updated_at: string;
  created_at: string;
}

export interface WhiteboardLogEntry {
  id: string;
  entry_key: string;
  entry_type: string;
  action: 'set' | 'remove';
  author_name: string;
  author_type: string;
  old_value: string | null;
  new_value: string | null;
  reason: string;
  created_at: string;
}
