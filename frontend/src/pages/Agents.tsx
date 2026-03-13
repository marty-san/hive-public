import { useEffect, useState } from 'react';
import { useAgentStore } from '@/stores/agentStore';
import { Button } from '@/components/ui/button';
import { CreateAgentDialog } from '@/components/CreateAgentDialog';
import { Plus, Trash2, Edit } from 'lucide-react';
import type { Agent } from '@/types';

export function Agents() {
  const { agents, loading, fetchAgents, deleteAgent } = useAgentStore();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);

  useEffect(() => {
    fetchAgents();
  }, [fetchAgents]);

  const handleDelete = async (id: string) => {
    if (confirm('Are you sure you want to delete this agent?')) {
      await deleteAgent(id);
    }
  };

  const handleEdit = (agent: Agent) => {
    setEditingAgent(agent);
  };

  const handleCloseDialog = () => {
    setShowCreateForm(false);
    setEditingAgent(null);
  };

  return (
    <div className="p-8">
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Agents</h1>
          <p className="text-muted-foreground mt-2">
            Create and manage your AI agents
          </p>
        </div>
        <Button onClick={() => setShowCreateForm(true)}>
          <Plus className="h-4 w-4 mr-2" />
          Create Agent
        </Button>
      </div>

      {loading && agents.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground">
          Loading agents...
        </div>
      ) : agents.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-muted-foreground mb-4">No agents yet</p>
          <Button onClick={() => setShowCreateForm(true)}>
            <Plus className="h-4 w-4 mr-2" />
            Create Your First Agent
          </Button>
        </div>
      ) : (
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {agents.map((agent) => (
            <div
              key={agent.id}
              className="rounded-lg border bg-card p-6 hover:shadow-lg transition-shadow"
            >
              <div className="flex items-start justify-between mb-4">
                <div>
                  <h3 className="font-semibold text-lg">{agent.name}</h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    {agent.expertise_domain}
                  </p>
                  {agent.model && (
                    <p className="text-xs text-muted-foreground mt-1">
                      Model: {agent.model.includes('sonnet') ? 'Sonnet 4.5' :
                              agent.model.includes('opus') ? 'Opus 4.5' :
                              agent.model.includes('haiku') ? 'Haiku 3.5' : agent.model}
                    </p>
                  )}
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleEdit(agent)}
                  >
                    <Edit className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleDelete(agent.id)}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <p className="text-sm line-clamp-3">{agent.system_prompt}</p>
              <div className="mt-4 pt-4 border-t text-xs text-muted-foreground">
                Created {new Date(agent.created_at).toLocaleDateString()}
              </div>
            </div>
          ))}
        </div>
      )}

      <CreateAgentDialog
        open={showCreateForm || editingAgent !== null}
        onClose={handleCloseDialog}
        agent={editingAgent}
      />
    </div>
  );
}
