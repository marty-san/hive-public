import { useEffect } from 'react';
import { useConversationStore } from '@/stores/conversationStore';
import { useAgentStore } from '@/stores/agentStore';
import { Link, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { MessageSquare, Users, Plus, ArrowRight } from 'lucide-react';

export function Dashboard() {
  const { conversations, fetchConversations, createConversation } = useConversationStore();
  const { agents, fetchAgents } = useAgentStore();
  const navigate = useNavigate();

  useEffect(() => {
    fetchConversations();
    fetchAgents();
  }, [fetchConversations, fetchAgents]);

  const activeConversations = conversations.filter(c => c.status === 'active');

  const handleNewConversation = async () => {
    const newConv = await createConversation({ title: 'New Conversation', initial_participants: [] });
    navigate(`/chat/${newConv.id}`);
  };

  return (
    <div className="p-8 max-w-2xl">
      <div className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight">Good morning.</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          {activeConversations.length} conversation{activeConversations.length !== 1 ? 's' : ''} · {agents.length} agent{agents.length !== 1 ? 's' : ''}
        </p>
      </div>

      <div className="grid gap-4 grid-cols-2">
        {/* New Conversation */}
        <button
          onClick={handleNewConversation}
          className="group rounded-lg border bg-card p-5 text-left hover:border-primary/50 hover:bg-accent/30 transition-colors"
        >
          <div className="flex items-center justify-between mb-3">
            <div className="p-2 rounded-md bg-primary/10">
              <MessageSquare className="h-4 w-4 text-primary" />
            </div>
            <ArrowRight className="h-4 w-4 text-muted-foreground/40 group-hover:text-muted-foreground transition-colors" />
          </div>
          <p className="font-medium text-sm">New Conversation</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {activeConversations.length} active
          </p>
        </button>

        {/* Manage Agents */}
        <Link
          to="/agents"
          className="group rounded-lg border bg-card p-5 hover:border-primary/50 hover:bg-accent/30 transition-colors"
        >
          <div className="flex items-center justify-between mb-3">
            <div className="p-2 rounded-md bg-primary/10">
              <Users className="h-4 w-4 text-primary" />
            </div>
            <ArrowRight className="h-4 w-4 text-muted-foreground/40 group-hover:text-muted-foreground transition-colors" />
          </div>
          <p className="font-medium text-sm">Agents</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {agents.length} configured
          </p>
        </Link>
      </div>

      {agents.length === 0 && (
        <div className="mt-8 rounded-lg border border-dashed p-6 text-center">
          <p className="text-sm text-muted-foreground mb-3">
            Create your first agent to get started.
          </p>
          <Link to="/agents">
            <Button size="sm" variant="outline">
              <Plus className="h-3.5 w-3.5 mr-1.5" />
              Create Agent
            </Button>
          </Link>
        </div>
      )}
    </div>
  );
}
