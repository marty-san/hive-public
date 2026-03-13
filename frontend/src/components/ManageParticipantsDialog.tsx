import { useState } from 'react';
import { useAgentStore } from '@/stores/agentStore';
import { useConversationStore } from '@/stores/conversationStore';
import { Button } from '@/components/ui/button';
import { X, Plus, Trash2, Users } from 'lucide-react';

interface ManageParticipantsDialogProps {
  open: boolean;
  onClose: () => void;
  conversationId: string;
  currentParticipantIds: string[];
}

export function ManageParticipantsDialog({
  open,
  onClose,
  conversationId,
  currentParticipantIds,
}: ManageParticipantsDialogProps) {
  const { agents } = useAgentStore();
  const { addParticipant, removeParticipant } = useConversationStore();
  const [selectedAgentIds, setSelectedAgentIds] = useState<Set<string>>(new Set());
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState('');

  const currentParticipants = agents.filter((a) =>
    currentParticipantIds.includes(a.id)
  );
  const availableAgents = agents.filter(
    (a) => !currentParticipantIds.includes(a.id)
  );

  const toggleAgentSelection = (agentId: string) => {
    const newSelection = new Set(selectedAgentIds);
    if (newSelection.has(agentId)) {
      newSelection.delete(agentId);
    } else {
      newSelection.add(agentId);
    }
    setSelectedAgentIds(newSelection);
  };

  const handleAddSelected = async () => {
    if (selectedAgentIds.size === 0) return;

    setProcessing(true);
    setError('');
    try {
      // Add all selected agents in parallel
      await Promise.all(
        Array.from(selectedAgentIds).map((agentId) =>
          addParticipant(conversationId, agentId)
        )
      );
      setSelectedAgentIds(new Set());
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to add agents');
    } finally {
      setProcessing(false);
    }
  };

  const handleRemoveAgent = async (agentId: string) => {
    setProcessing(true);
    setError('');
    try {
      await removeParticipant(conversationId, agentId);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to remove agent');
    } finally {
      setProcessing(false);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
      <div className="bg-background rounded-lg p-6 max-w-2xl w-full max-h-[80vh] overflow-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-bold flex items-center gap-2">
            <Users className="h-5 w-5" />
            Manage Conversation Agents
          </h2>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {error && (
          <div className="bg-destructive/10 text-destructive px-4 py-2 rounded-md text-sm mb-4">
            {error}
          </div>
        )}

        {/* Current Participants */}
        <div className="mb-6">
          <h3 className="text-sm font-semibold mb-3 text-muted-foreground">
            CURRENT AGENTS ({currentParticipants.length})
          </h3>
          {currentParticipants.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              No agents in this conversation yet
            </p>
          ) : (
            <div className="space-y-2">
              {currentParticipants.map((agent) => (
                <div
                  key={agent.id}
                  className="flex items-center justify-between p-3 border rounded-lg bg-muted/30"
                >
                  <div className="flex-1">
                    <p className="font-medium">{agent.name}</p>
                    <p className="text-sm text-muted-foreground">
                      {agent.expertise_domain}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => handleRemoveAgent(agent.id)}
                    disabled={processing}
                    className="text-destructive hover:text-destructive hover:bg-destructive/10"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Available Agents */}
        <div>
          <h3 className="text-sm font-semibold mb-3 text-muted-foreground">
            ADD AGENTS ({selectedAgentIds.size} selected)
          </h3>
          {availableAgents.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              All agents are already in this conversation
            </p>
          ) : (
            <>
              <div className="space-y-2 mb-4">
                {availableAgents.map((agent) => (
                  <div
                    key={agent.id}
                    onClick={() => toggleAgentSelection(agent.id)}
                    className={`flex items-center justify-between p-3 border rounded-lg cursor-pointer transition-colors ${
                      selectedAgentIds.has(agent.id)
                        ? 'border-primary bg-primary/10'
                        : 'hover:border-primary/50'
                    }`}
                  >
                    <div className="flex-1">
                      <p className="font-medium">{agent.name}</p>
                      <p className="text-sm text-muted-foreground">
                        {agent.expertise_domain}
                      </p>
                    </div>
                    <div
                      className={`w-5 h-5 rounded border-2 flex items-center justify-center ${
                        selectedAgentIds.has(agent.id)
                          ? 'border-primary bg-primary'
                          : 'border-muted-foreground'
                      }`}
                    >
                      {selectedAgentIds.has(agent.id) && (
                        <X className="h-3 w-3 text-primary-foreground" />
                      )}
                    </div>
                  </div>
                ))}
              </div>
              <Button
                onClick={handleAddSelected}
                disabled={processing || selectedAgentIds.size === 0}
                className="w-full"
              >
                <Plus className="h-4 w-4 mr-2" />
                Add {selectedAgentIds.size > 0 ? `${selectedAgentIds.size} ` : ''}
                Agent{selectedAgentIds.size !== 1 ? 's' : ''}
              </Button>
            </>
          )}
        </div>

        <div className="flex justify-end mt-6">
          <Button onClick={onClose} variant="outline">
            Done
          </Button>
        </div>
      </div>
    </div>
  );
}
