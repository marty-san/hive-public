import { useEffect, useRef, useCallback } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useDebugStore } from '@/stores/debugStore';
import type { WhiteboardEntry } from '@/types';

export interface ProposalVoteRequest {
  proposal_id: string;
  proposal_type: string;
  proposer_name: string;
  target_agent_name: string;
  rationale: string;
  agent_votes: Record<string, string>;
  timeout_seconds: number;
}

export function useWebSocket(
  conversationId: string | undefined,
  onDiscussionComplete?: () => void,
  onAgentTyping?: (agentName: string) => void,
  onInterruptAcknowledged?: () => void,
  onProposalVoteRequested?: (data: ProposalVoteRequest) => void,
  onProposalResolved?: (proposalId: string) => void,
  onStatusUpdate?: (text: string) => void,
  onWhiteboardUpdated?: (entries: WhiteboardEntry[], change: object) => void,
) {
  const ws = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmounted = useRef(false);

  // Store all callbacks and store functions in refs so they're always current
  // without causing connect() to be recreated on every render.
  const { addMessage, fetchMessages, fetchConversation } = useConversationStore();
  const { addDebugEvent } = useDebugStore();
  const addMessageRef = useRef(addMessage);
  const fetchMessagesRef = useRef(fetchMessages);
  const fetchConversationRef = useRef(fetchConversation);
  const addDebugEventRef = useRef(addDebugEvent);
  const onDiscussionCompleteRef = useRef(onDiscussionComplete);
  const onAgentTypingRef = useRef(onAgentTyping);
  const onInterruptAcknowledgedRef = useRef(onInterruptAcknowledged);
  const onProposalVoteRequestedRef = useRef(onProposalVoteRequested);
  const onProposalResolvedRef = useRef(onProposalResolved);
  const onStatusUpdateRef = useRef(onStatusUpdate);
  const onWhiteboardUpdatedRef = useRef(onWhiteboardUpdated);

  // Keep refs current on every render (no re-renders triggered)
  addMessageRef.current = addMessage;
  fetchMessagesRef.current = fetchMessages;
  fetchConversationRef.current = fetchConversation;
  addDebugEventRef.current = addDebugEvent;
  onDiscussionCompleteRef.current = onDiscussionComplete;
  onAgentTypingRef.current = onAgentTyping;
  onInterruptAcknowledgedRef.current = onInterruptAcknowledged;
  onProposalVoteRequestedRef.current = onProposalVoteRequested;
  onProposalResolvedRef.current = onProposalResolved;
  onStatusUpdateRef.current = onStatusUpdate;
  onWhiteboardUpdatedRef.current = onWhiteboardUpdated;

  // connect() only depends on conversationId so the effect only re-runs when
  // the conversation changes — not on every store/callback reference churn.
  const connect = useCallback(() => {
    if (!conversationId || unmounted.current) return;

    // Cancel any pending reconnect timer before opening a new connection.
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }

    // Close any existing connection without triggering the auto-reconnect handler.
    // This ensures at most one live connection exists at any time.
    if (ws.current) {
      ws.current.onopen = null;
      ws.current.onmessage = null;
      ws.current.onerror = null;
      ws.current.onclose = null;
      if (ws.current.readyState < WebSocket.CLOSING) {
        ws.current.close();
      }
      ws.current = null;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.hostname}:8000/ws/conversations/${conversationId}`;

    console.log('Connecting to WebSocket:', wsUrl);
    const socket = new WebSocket(wsUrl);
    ws.current = socket;

    socket.onopen = () => {
      console.log('WebSocket connected');
      if (conversationId) {
        fetchMessagesRef.current(conversationId);
      }
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log('WebSocket message:', data);

        if (data.type === 'message.new') {
          addMessageRef.current(data.message);
        } else if (data.type === 'agent.typing') {
          console.log(`${data.agent_name} is typing...`);
          if (data.agent_name) {
            onAgentTypingRef.current?.(data.agent_name);
            onStatusUpdateRef.current?.(`${data.agent_name} is responding...`);
          }
        } else if (data.type === 'interrupt.acknowledged') {
          console.log('Interrupt acknowledged by backend');
          onInterruptAcknowledgedRef.current?.();
        } else if (data.type === 'proposal.vote_requested') {
          console.log('Proposal vote requested:', data);
          onProposalVoteRequestedRef.current?.(data as ProposalVoteRequest);
        } else if (data.type === 'proposal.resolved') {
          console.log('Proposal resolved:', data);
          onProposalResolvedRef.current?.(data.proposal_id);
          // Re-fetch conversation to update participant pills and Manage Agents dialog
          if (conversationId) {
            fetchConversationRef.current(conversationId);
          }
        } else if (data.type === 'whiteboard.updated') {
          console.log('Whiteboard updated:', data.change);
          onWhiteboardUpdatedRef.current?.(data.entries as WhiteboardEntry[], data.change);
        } else if (data.type === 'debug.event') {
          addDebugEventRef.current({
            timestamp: data.timestamp || new Date().toISOString(),
            event_type: data.event_type,
            conversation_id: data.conversation_id,
            data: data.data,
          });
          console.log('Debug event:', data.event_type, data.data);

          if (data.event_type === 'discussion_complete') {
            console.log('Discussion complete - clearing agentsResponding');
            onDiscussionCompleteRef.current?.();
            onStatusUpdateRef.current?.('');
          } else if (data.event_type === 'bidding_started') {
            onStatusUpdateRef.current?.('Deciding who responds...');
          } else if (data.event_type === 'memory_retrieval') {
            const agentName = data.data?.agent_name;
            onStatusUpdateRef.current?.(agentName ? `${agentName} is retrieving memories...` : 'Retrieving memories...');
          }
        }
      } catch (error) {
        console.error('Error parsing WebSocket message:', error);
      }
    };

    socket.onerror = (error) => {
      console.error('WebSocket error:', error);
    };

    socket.onclose = () => {
      console.log('WebSocket disconnected');
      if (!unmounted.current && conversationId) {
        reconnectTimer.current = setTimeout(connect, 3000);
      }
    };
  }, [conversationId]); // only conversationId — everything else goes through refs

  useEffect(() => {
    unmounted.current = false;
    connect();

    return () => {
      unmounted.current = true;
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current);
        reconnectTimer.current = null;
      }
      if (ws.current) {
        console.log('Closing WebSocket connection');
        ws.current.onopen = null;
        ws.current.onmessage = null;
        ws.current.onerror = null;
        ws.current.onclose = null;
        ws.current.close();
        ws.current = null;
      }
    };
  }, [connect]);

  return {
    connected: ws.current?.readyState === WebSocket.OPEN,
  };
}
