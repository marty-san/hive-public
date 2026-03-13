import { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useVirtualizer } from '@tanstack/react-virtual';
import { useConversationStore } from '@/stores/conversationStore';
import { useAgentStore } from '@/stores/agentStore';
import { useDebugStore } from '@/stores/debugStore';
import { useWhiteboardStore } from '@/stores/whiteboardStore';
import { useWebSocket } from '@/hooks/useWebSocket';
import { Button } from '@/components/ui/button';
import { ManageParticipantsDialog } from '@/components/ManageParticipantsDialog';
import { DeleteConversationDialog } from '@/components/DeleteConversationDialog';
import { DebugConsole } from '@/components/DebugConsole';
import { MessageItem } from '@/components/MessageItem';
import { WhiteboardPanel } from '@/components/WhiteboardPanel';
import { MentionInput, type MentionInputHandle } from '@/components/MentionInput';
import { Plus, Send, Users, Edit2, Trash2, Check, X, Bug, Bot, FileText, Loader2, Vote, StickyNote } from 'lucide-react';
import { chatApi, conversationSettingsApi } from '@/services/api';
import type { ProposalVoteRequest } from '@/hooks/useWebSocket';
import type { WhiteboardEntry } from '@/types';
import { CommandPalette, type PaletteCommand } from '@/components/CommandPalette';
import ReactMarkdown from 'react-markdown';

// Derive a consistent background color from an agent name
function agentColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue}, 60%, 42%)`;
}

function agentInitials(name: string): string {
  return name.split(/\s+/).map(w => w[0] ?? '').join('').toUpperCase().slice(0, 2);
}

// Limit number of messages rendered for performance
// Set to 0 to disable limit and let virtualization handle all messages (recommended)
// Virtual scrolling is now enabled, so this can be set to 0 for best performance
const MESSAGE_RENDER_LIMIT = 0;

export function Chat() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const {
    currentConversation,
    fetchConversation,
    fetchMessages,
    sendMessage,
    triggerMultiAgentResponse,
    interruptDiscussion,
    rewindConversation,
    deleteConversation,
    renameConversation,
    createConversation,
    addMessage,
    loading,
  } = useConversationStore();

  // Use a specific selector for messages to ensure re-renders
  const messages = useConversationStore((state) => state.messages);

  const { agents, fetchAgents } = useAgentStore();
  const { enabled: debugEnabled, toggleDebug } = useDebugStore();
  const { fetchEntries, applyUpdate } = useWhiteboardStore();

  // WebSocket connection for real-time updates
  // Handle discussion complete event from WebSocket
  const handleDiscussionComplete = useCallback(() => {
    setAgentsResponding(false);
    setRespondingAgent(null);
    setInterruptPending(false);
    setStatusText('');
    if (agentTimeoutRef.current) {
      clearTimeout(agentTimeoutRef.current);
      agentTimeoutRef.current = null;
    }
  }, []);

  const handleAgentTyping = useCallback((agentName: string) => {
    setAgentsResponding(true);
    setRespondingAgent(agentName);
  }, []);

  const handleStatusUpdate = useCallback((text: string) => {
    setStatusText(text);
  }, []);

  // Called the instant the backend sets the interrupt flag (one WebSocket round-trip)
  const handleInterruptAcknowledged = useCallback(() => {
    setInterruptPending(true);
    setRespondingAgent(null);
    setStatusText('');
  }, []);

  const handleProposalVoteRequested = useCallback((data: ProposalVoteRequest) => {
    setActiveProposal(data);
  }, []);

  const handleProposalResolved = useCallback((_proposalId: string) => {
    setActiveProposal(null);
    setProposalVoting(false);
    // Refresh agent list in case a new agent was auto-created by the proposal
    fetchAgents();
  }, [fetchAgents]);

  const handleWhiteboardUpdated = useCallback((entries: WhiteboardEntry[], _change: object) => {
    if (conversationId) {
      applyUpdate(conversationId, entries);
    }
  }, [conversationId, applyUpdate]);

  const { connected } = useWebSocket(
    conversationId,
    handleDiscussionComplete,
    handleAgentTyping,
    handleInterruptAcknowledged,
    handleProposalVoteRequested,
    handleProposalResolved,
    handleStatusUpdate,
    handleWhiteboardUpdated,
  );

  const [messageInput, setMessageInput] = useState('');
  const [sendingMessage, setSendingMessage] = useState(false);
  const [showAddParticipant, setShowAddParticipant] = useState(false);
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [editedTitle, setEditedTitle] = useState('');
  const [showWhiteboard, setShowWhiteboard] = useState(false);
  const [agentsResponding, setAgentsResponding] = useState(false);
  const [respondingAgent, setRespondingAgent] = useState<string | null>(null);
  const [statusText, setStatusText] = useState('');
  const [interruptPending, setInterruptPending] = useState(false);
  const [summary, setSummary] = useState<string | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [activeProposal, setActiveProposal] = useState<ProposalVoteRequest | null>(null);
  const [proposalVoting, setProposalVoting] = useState(false);
  const [humanVotesEnabled, setHumanVotesEnabled] = useState(false);
  const [humanVotesLoading, setHumanVotesLoading] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const parentRef = useRef<HTMLDivElement>(null);
  const mentionInputRef = useRef<MentionInputHandle>(null);
  const agentTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const initialScrollDoneRef = useRef(false);

  // Fetch agents on mount
  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  // Fetch conversation and messages when conversationId changes
  useEffect(() => {
    if (conversationId) {
      fetchConversation(conversationId);
      fetchMessages(conversationId);
      fetchEntries(conversationId);
      // No polling needed - WebSocket provides real-time updates
    }
  }, [conversationId, fetchConversation, fetchMessages, fetchEntries]);

  // Fetch conversation settings (including human votes toggle)
  useEffect(() => {
    if (!conversationId) return;
    conversationSettingsApi.get(conversationId)
      .then(r => setHumanVotesEnabled(r.data.human_votes_on_proposals))
      .catch(() => {});
  }, [conversationId]);

  // Reset agentsResponding and scroll state when conversation changes
  useEffect(() => {
    // Clear any pending timeout
    if (agentTimeoutRef.current) {
      clearTimeout(agentTimeoutRef.current);
      agentTimeoutRef.current = null;
    }
    setAgentsResponding(false);
    setRespondingAgent(null);
    setStatusText('');
    setInterruptPending(false);
    setSummary(null);
    setActiveProposal(null);
    setProposalVoting(false);
    initialScrollDoneRef.current = false;
  }, [conversationId]);

  // Memoize current messages to prevent recalculation on every render
  // Apply message limit for performance (can be disabled with 0 when using virtualization)
  const currentMessages = useMemo(() => {
    const allMessages = messages[conversationId] || [];
    if (MESSAGE_RENDER_LIMIT > 0 && allMessages.length > MESSAGE_RENDER_LIMIT) {
      return allMessages.slice(-MESSAGE_RENDER_LIMIT);
    }
    return allMessages;
  }, [messages, conversationId]);

  // Set up virtualizer for efficient rendering of large message lists
  const virtualizer = useVirtualizer({
    count: currentMessages.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 100, // Estimated height of each message in pixels
    overscan: 5, // Render 5 extra items above/below viewport
  });

  // Scroll to bottom on initial load; after that, only scroll if already near the bottom.
  useEffect(() => {
    if (currentMessages.length > 0 && parentRef.current) {
      requestAnimationFrame(() => {
        const el = parentRef.current;
        if (!el) return;
        if (!initialScrollDoneRef.current) {
          // First load for this conversation — always go to bottom
          el.scrollTop = el.scrollHeight;
          initialScrollDoneRef.current = true;
        } else {
          // Subsequent messages — only scroll if already near the bottom
          const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
          if (distanceFromBottom < 150) {
            el.scrollTop = el.scrollHeight;
          }
        }
      });
    }
  }, [currentMessages.length]);

  // Global Cmd+K / Ctrl+K — always available, even while agents are responding
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setPaletteOpen(v => !v);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const handleCreateConversation = async () => {
    const newConv = await createConversation({
      title: 'New Conversation',
      initial_participants: [],
    });
    navigate(`/chat/${newConv.id}`);
  };

  // Define participantAgents before it's used in callbacks
  const participantAgents = useMemo(() =>
    agents.filter(a => currentConversation?.participant_ids.includes(a.id)),
    [agents, currentConversation?.participant_ids]
  );

  const handleSendMessage = useCallback(async (e?: React.FormEvent) => {
    e?.preventDefault();
    const content = mentionInputRef.current?.getValue().trim() ?? '';
    if (!conversationId || !content || sendingMessage || agentsResponding) return;

    setSendingMessage(true);
    try {
      await sendMessage(conversationId, content);
      mentionInputRef.current?.clear();
      if (parentRef.current) {
        parentRef.current.scrollTop = parentRef.current.scrollHeight;
      }

      // Auto-trigger relevant agents after sending message
      if (participantAgents.length > 0) {
        try {
          // Clear any existing timeout
          if (agentTimeoutRef.current) {
            clearTimeout(agentTimeoutRef.current);
          }

          setAgentsResponding(true);
          await triggerMultiAgentResponse(conversationId);

          // Safety timeout: auto-reset after 2 minutes if still stuck
          // This only triggers if something goes wrong (e.g., backend crash)
          // Normal discussions end via Stop button or conversation change
          agentTimeoutRef.current = setTimeout(() => {
            setAgentsResponding(false);
            setRespondingAgent(null);
            agentTimeoutRef.current = null;
          }, 120000); // 2 minutes
        } catch (error) {
          console.error('Failed to trigger agents:', error);
          setAgentsResponding(false);
          setRespondingAgent(null);
          if (agentTimeoutRef.current) {
            clearTimeout(agentTimeoutRef.current);
            agentTimeoutRef.current = null;
          }
        }
      }
    } catch (error) {
      console.error('Failed to send message:', error);
    } finally {
      setSendingMessage(false);
    }
  }, [conversationId, sendingMessage, agentsResponding, sendMessage, participantAgents, triggerMultiAgentResponse]);


  const handleStartEdit = useCallback(() => {
    setEditedTitle(currentConversation?.title || '');
    setIsEditingTitle(true);
  }, [currentConversation?.title]);

  const handleSaveTitle = useCallback(async () => {
    if (!conversationId || !editedTitle.trim()) return;
    try {
      await renameConversation(conversationId, editedTitle.trim());
      setIsEditingTitle(false);
    } catch (error) {
      console.error('Failed to rename:', error);
    }
  }, [conversationId, editedTitle, renameConversation]);

  const handleCancelEdit = useCallback(() => {
    setIsEditingTitle(false);
    setEditedTitle('');
  }, []);

  const handleDelete = useCallback(() => {
    if (!conversationId) return;
    setDeleteDialogOpen(true);
  }, [conversationId]);

  const handleDeleteConfirm = useCallback(async (deleteMemories: boolean) => {
    if (!conversationId) return;
    try {
      await deleteConversation(conversationId, deleteMemories);
      navigate('/chat');
    } catch (error) {
      console.error('Failed to delete:', error);
    }
  }, [conversationId, deleteConversation, navigate]);

  const handleInterrupt = useCallback(async () => {
    if (!conversationId) return;
    try {
      await interruptDiscussion(conversationId);
      // Don't reset UI immediately — wait for interrupt.acknowledged from the backend.
      // handleInterruptAcknowledged() will show "Stopping…" and
      // handleDiscussionComplete() will fully clear the state.
    } catch (error) {
      console.error('Failed to interrupt:', error);
      // On error, reset state so the user isn't stuck
      setAgentsResponding(false);
      setRespondingAgent(null);
      setInterruptPending(false);
      if (agentTimeoutRef.current) {
        clearTimeout(agentTimeoutRef.current);
        agentTimeoutRef.current = null;
      }
    }
  }, [conversationId, interruptDiscussion]);

  const handleRewind = useCallback(async (messageId: string) => {
    if (!conversationId) return;
    try {
      // Interrupt any ongoing discussion first
      if (agentsResponding) {
        await interruptDiscussion(conversationId);
        setAgentsResponding(false);
        setRespondingAgent(null);
        if (agentTimeoutRef.current) {
          clearTimeout(agentTimeoutRef.current);
          agentTimeoutRef.current = null;
        }
      }

      await rewindConversation(conversationId, messageId);

      // Scroll to the rewound message after a brief delay
      setTimeout(() => {
        if (parentRef.current) {
          parentRef.current.scrollTop = parentRef.current.scrollHeight;
        }
      }, 100);
    } catch (error) {
      console.error('Failed to rewind:', error);
      alert('Failed to rewind conversation. Please try again.');
    }
  }, [conversationId, rewindConversation, interruptDiscussion, agentsResponding]);

  const handleProposalVote = useCallback(async (vote: 'approve' | 'reject') => {
    if (!conversationId || !activeProposal) return;
    setProposalVoting(true);
    try {
      await conversationSettingsApi.voteOnProposal(conversationId, activeProposal.proposal_id, vote);
      // Banner will be cleared by handleProposalResolved via WebSocket
    } catch (error) {
      console.error('Failed to submit proposal vote:', error);
      setProposalVoting(false);
    }
  }, [conversationId, activeProposal]);

  const handleToggleHumanVotes = useCallback(async () => {
    if (!conversationId) return;
    const newValue = !humanVotesEnabled;
    setHumanVotesLoading(true);
    try {
      await conversationSettingsApi.update(conversationId, { human_votes_on_proposals: newValue });
      setHumanVotesEnabled(newValue);
    } catch (error) {
      console.error('Failed to update settings:', error);
    } finally {
      setHumanVotesLoading(false);
    }
  }, [conversationId, humanVotesEnabled]);

  const handleSummarize = useCallback(async () => {
    if (!conversationId) return;
    if (summary) {
      setSummary(null);
      return;
    }
    setSummaryLoading(true);
    try {
      const response = await chatApi.summarize(conversationId);
      setSummary(response.data.summary);
      addMessage(response.data.message);
    } catch (error) {
      console.error('Failed to summarize:', error);
    } finally {
      setSummaryLoading(false);
    }
  }, [conversationId, summary]);

  if (!conversationId) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-muted-foreground mb-4">No conversation selected</p>
          <Button onClick={handleCreateConversation}>
            <Plus className="h-4 w-4 mr-2" />
            Start New Conversation
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="border-b px-4 py-2 flex items-center gap-3 min-h-[52px]">
        {/* Title */}
        {isEditingTitle ? (
          <div className="flex items-center gap-1 flex-shrink-0">
            <input
              type="text"
              value={editedTitle}
              onChange={(e) => setEditedTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSaveTitle();
                if (e.key === 'Escape') handleCancelEdit();
              }}
              className="rounded border border-input bg-background px-2 py-1 text-sm font-semibold w-48"
              autoFocus
            />
            <Button onClick={handleSaveTitle} size="sm" variant="ghost" className="h-7 w-7 p-0">
              <Check className="h-3.5 w-3.5 text-green-600" />
            </Button>
            <Button onClick={handleCancelEdit} size="sm" variant="ghost" className="h-7 w-7 p-0">
              <X className="h-3.5 w-3.5 text-muted-foreground" />
            </Button>
          </div>
        ) : (
          <button
            onClick={handleStartEdit}
            className="font-semibold text-sm hover:opacity-70 transition-opacity flex-shrink-0 text-left max-w-[200px] truncate"
            title={currentConversation?.title || 'Untitled Conversation'}
          >
            {currentConversation?.title || 'Untitled Conversation'}
          </button>
        )}

        {/* Agent avatars */}
        <div className="flex items-center gap-1 flex-wrap flex-1 min-w-0">
          {participantAgents.length === 0 ? (
            <span className="text-xs text-muted-foreground">No agents</span>
          ) : (
            participantAgents.map((agent) => (
              <span
                key={agent.id}
                title={`${agent.name} — ${agent.expertise_domain}`}
                className="inline-flex items-center justify-center w-7 h-7 rounded-full text-white text-xs font-semibold flex-shrink-0 select-none"
                style={{ backgroundColor: agentColor(agent.name) }}
              >
                {agentInitials(agent.name)}
              </span>
            ))
          )}
        </div>

        {/* Connection dot */}
        <span
          className={`w-2 h-2 rounded-full flex-shrink-0 ${connected ? 'bg-green-500' : 'bg-orange-500'}`}
          title={connected ? 'Live' : 'Connecting…'}
        />

        {/* Stop Discussion — only when agents are active */}
        {agentsResponding && (
          <Button
            onClick={handleInterrupt}
            variant="destructive"
            size="sm"
            disabled={interruptPending}
            className="flex-shrink-0"
          >
            <X className="h-3.5 w-3.5 mr-1" />
            {interruptPending ? 'Stopping…' : 'Stop'}
          </Button>
        )}

        {/* Command palette trigger */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => setPaletteOpen(true)}
          className="flex-shrink-0 gap-1.5 text-muted-foreground"
          title="Open command palette (Ctrl+K)"
        >
          <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
          </svg>
          <kbd className="text-xs font-mono">Ctrl+K</kbd>
        </Button>
      </div>

      {/* Summary panel */}
      {summary && (
        <div className="border-b bg-muted/40 px-4 py-3">
          <div className="max-w-4xl mx-auto flex items-start gap-3">
            <FileText className="h-4 w-4 text-muted-foreground mt-0.5 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-muted-foreground mb-1">Summary since your last message</p>
              <div className="text-sm prose prose-sm max-w-none dark:prose-invert">
                <ReactMarkdown>{summary}</ReactMarkdown>
              </div>
            </div>
            <Button variant="ghost" size="sm" onClick={() => setSummary(null)} className="flex-shrink-0 h-6 w-6 p-0">
              <X className="h-3 w-3" />
            </Button>
          </div>
        </div>
      )}

      {/* Proposal vote banner */}
      {activeProposal && (
        <div className="border-b bg-amber-50 dark:bg-amber-950/20 px-4 py-3">
          <div className="max-w-4xl mx-auto flex items-start gap-3">
            <Vote className="h-4 w-4 text-amber-600 mt-0.5 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-amber-700 dark:text-amber-400 mb-1">
                Agent Proposal — vote required
              </p>
              <p className="text-sm font-medium">
                {activeProposal.proposal_type === 'propose_addition'
                  ? `Add ${activeProposal.target_agent_name} to the conversation?`
                  : `Remove ${activeProposal.target_agent_name} from the conversation?`}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Proposed by {activeProposal.proposer_name}: {activeProposal.rationale}
              </p>
              {Object.keys(activeProposal.agent_votes).length > 0 && (
                <div className="flex gap-2 mt-1 flex-wrap">
                  {Object.entries(activeProposal.agent_votes).map(([name, vote]) => (
                    <span
                      key={name}
                      className={`text-xs px-1.5 py-0.5 rounded ${vote === 'approve' ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'}`}
                    >
                      {name}: {vote}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <div className="flex gap-2 flex-shrink-0">
              <Button
                size="sm"
                variant="default"
                className="bg-green-600 hover:bg-green-700"
                onClick={() => handleProposalVote('approve')}
                disabled={proposalVoting}
              >
                <Check className="h-3 w-3 mr-1" />
                Approve
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={() => handleProposalVote('reject')}
                disabled={proposalVoting}
              >
                <X className="h-3 w-3 mr-1" />
                Reject
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Main content: messages + optional whiteboard panel */}
      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 flex flex-col overflow-hidden">

      {/* Messages */}
      <div ref={parentRef} className="flex-1 overflow-auto p-4">
        {loading && currentMessages.length === 0 ? (
          <div className="text-center py-12 text-muted-foreground">
            Loading messages...
          </div>
        ) : currentMessages.length === 0 ? (
          <div className="max-w-4xl mx-auto py-16 px-4">
            {participantAgents.length === 0 ? (
              <div className="text-center">
                <p className="text-sm text-muted-foreground mb-3">No agents in this conversation yet.</p>
                <button
                  onClick={() => setPaletteOpen(true)}
                  className="text-xs text-muted-foreground/60 border border-border/40 rounded-full px-3 py-1 hover:text-foreground hover:border-border transition-colors"
                >
                  <kbd className="font-mono">Ctrl+K</kbd> → Manage agents
                </button>
              </div>
            ) : (
              <div>
                <div className="flex flex-wrap gap-3 mb-10">
                  {participantAgents.map((agent) => (
                    <div key={agent.id} className="flex items-center gap-2.5 border border-border/50 rounded-lg px-3 py-2">
                      <span
                        className="inline-flex items-center justify-center w-7 h-7 rounded-full text-white text-xs font-semibold flex-shrink-0 select-none"
                        style={{ backgroundColor: agentColor(agent.name) }}
                      >
                        {agentInitials(agent.name)}
                      </span>
                      <div>
                        <p className="text-sm font-medium leading-tight">{agent.name}</p>
                        <p className="text-xs text-muted-foreground leading-tight">{agent.expertise_domain}</p>
                      </div>
                    </div>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground/50">Start the conversation below</p>
              </div>
            )}
          </div>
        ) : (
          <div className="max-w-4xl mx-auto">
            {MESSAGE_RENDER_LIMIT > 0 && (messages[conversationId]?.length || 0) > MESSAGE_RENDER_LIMIT && (
              <div className="text-center py-2 mb-4">
                <p className="text-xs text-muted-foreground">
                  Showing last {MESSAGE_RENDER_LIMIT} of {messages[conversationId]?.length} messages
                </p>
              </div>
            )}

            {/* Virtualized message list */}
            <div
              style={{
                height: `${virtualizer.getTotalSize()}px`,
                width: '100%',
                position: 'relative',
              }}
            >
              {virtualizer.getVirtualItems().map((virtualItem) => {
                const isLastMessage = virtualItem.index === currentMessages.length - 1;
                const canRewind = !isLastMessage; // Can rewind if there are messages after this one

                return (
                  <div
                    key={virtualItem.key}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      transform: `translateY(${virtualItem.start}px)`,
                    }}
                    data-index={virtualItem.index}
                    ref={virtualizer.measureElement}
                  >
                    <div className="mb-6">
                      <MessageItem
                        message={currentMessages[virtualItem.index]}
                        onRewind={handleRewind}
                        canRewind={canRewind}
                      />
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Agent responding indicator */}
            {agentsResponding && (
              <div className="flex gap-3 flex-row mt-4">
                <div className="flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center bg-secondary">
                  <Bot className="h-4 w-4 text-secondary-foreground" />
                </div>
                <div className="flex-1 max-w-[70%]">
                  <div className="inline-block rounded-lg px-4 py-2 bg-muted">
                    <div className="flex items-center gap-2">
                      <div className="flex gap-1">
                        <span className="w-2 h-2 bg-muted-foreground/60 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                        <span className="w-2 h-2 bg-muted-foreground/60 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                        <span className="w-2 h-2 bg-muted-foreground/60 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                      </div>
                      <span className="text-xs text-muted-foreground">
                        {interruptPending
                          ? 'Stopping…'
                          : statusText || (respondingAgent ? `${respondingAgent} is responding...` : 'Agents are responding...')}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Ready indicator */}
            {!agentsResponding && !sendingMessage && currentMessages.length > 0 && (
              <div className="flex justify-center my-3">
                <div className="text-xs text-muted-foreground/50 flex items-center gap-1.5 border border-border/40 rounded-full px-3 py-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-500/70" />
                  Ready
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t p-4 bg-background/95">
        <form onSubmit={handleSendMessage} className="max-w-4xl mx-auto">
          <div className="flex gap-2 items-end">
            <MentionInput
              ref={mentionInputRef}
              agents={participantAgents}
              onChange={setMessageInput}
              onSubmit={handleSendMessage}
              disabled={sendingMessage || agentsResponding}
              placeholder={
                agentsResponding
                  ? interruptPending
                    ? "Stopping…"
                    : statusText || (respondingAgent ? `${respondingAgent} is responding...` : "Agents are responding...")
                  : "Type a message... (@ to mention)"
              }
            />
            <Button type="submit" disabled={sendingMessage || agentsResponding || !messageInput.trim()}>
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </form>
      </div>

        </div>{/* end inner flex-col */}

        {/* Whiteboard panel */}
        {showWhiteboard && conversationId && (
          <div className="w-96 border-l flex-shrink-0 overflow-hidden">
            <WhiteboardPanel conversationId={conversationId} onClose={() => setShowWhiteboard(false)} />
          </div>
        )}
      </div>{/* end outer flex row */}

      <ManageParticipantsDialog
        open={showAddParticipant}
        onClose={() => setShowAddParticipant(false)}
        conversationId={conversationId}
        currentParticipantIds={currentConversation?.participant_ids || []}
      />

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        commands={[
          {
            id: 'whiteboard',
            label: 'Toggle whiteboard',
            icon: <StickyNote className="h-4 w-4" />,
            action: () => setShowWhiteboard(v => !v),
            badge: showWhiteboard ? 'ON' : 'OFF',
            badgeActive: showWhiteboard,
          },
          {
            id: 'summarize',
            label: summaryLoading ? 'Summarizing…' : summary ? 'Hide summary' : 'Summarize discussion',
            icon: summaryLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />,
            action: handleSummarize,
          },
          {
            id: 'manage-agents',
            label: 'Manage agents',
            icon: <Users className="h-4 w-4" />,
            action: () => setShowAddParticipant(true),
          },
          {
            id: 'human-votes',
            label: 'Human votes on proposals',
            icon: <Vote className="h-4 w-4" />,
            action: handleToggleHumanVotes,
            badge: humanVotesEnabled ? 'ON' : 'OFF',
            badgeActive: humanVotesEnabled,
          },
          {
            id: 'debug',
            label: 'Toggle debug console',
            icon: <Bug className="h-4 w-4" />,
            action: toggleDebug,
            badge: debugEnabled ? 'ON' : 'OFF',
            badgeActive: debugEnabled,
          },
          {
            id: 'delete',
            label: 'Delete conversation',
            icon: <Trash2 className="h-4 w-4" />,
            action: handleDelete,
            destructive: true,
          },
        ] satisfies PaletteCommand[]}
      />

      <DeleteConversationDialog
        open={deleteDialogOpen}
        onClose={() => setDeleteDialogOpen(false)}
        onConfirm={handleDeleteConfirm}
        title={currentConversation?.title}
      />

      <DebugConsole />
    </div>
  );
}
