import { useState, useEffect } from 'react';
import { useAgentStore } from '@/stores/agentStore';
import { Button } from '@/components/ui/button';
import { X } from 'lucide-react';
import type { Agent } from '@/types';

interface CreateAgentDialogProps {
  open: boolean;
  onClose: () => void;
  agent?: Agent | null;
}

export function CreateAgentDialog({ open, onClose, agent = null }: CreateAgentDialogProps) {
  const { createAgent, updateAgent, loading } = useAgentStore();
  const [formData, setFormData] = useState({
    name: '',
    expertise_domain: '',
    system_prompt: '',
    communication_style: '',
    model: 'claude-sonnet-4-5-20250929', // Default to Sonnet
  });
  const [error, setError] = useState('');

  // Pre-fill form when editing
  useEffect(() => {
    if (agent) {
      setFormData({
        name: agent.name,
        expertise_domain: agent.expertise_domain,
        system_prompt: agent.system_prompt,
        communication_style: agent.communication_style || '',
        model: agent.model || 'claude-sonnet-4-5-20250929',
      });
    } else {
      setFormData({
        name: '',
        expertise_domain: '',
        system_prompt: '',
        communication_style: '',
        model: 'claude-sonnet-4-5-20250929'
      });
    }
    setError('');
  }, [agent, open]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!formData.name || !formData.expertise_domain || !formData.system_prompt) {
      setError('All required fields must be filled');
      return;
    }

    try {
      // Prepare data for submission
      const submitData = {
        name: formData.name,
        expertise_domain: formData.expertise_domain,
        system_prompt: formData.system_prompt,
        communication_style: formData.communication_style || null,
        model: formData.model,
      };

      if (agent) {
        // Update existing agent
        await updateAgent(agent.id, submitData);
      } else {
        // Create new agent
        await createAgent(submitData);
      }
      setFormData({
        name: '',
        expertise_domain: '',
        system_prompt: '',
        communication_style: '',
        model: 'claude-sonnet-4-5-20250929'
      });
      onClose();
    } catch (err: any) {
      setError(err.response?.data?.detail || `Failed to ${agent ? 'update' : 'create'} agent`);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
      <div className="bg-background rounded-lg p-6 max-w-2xl w-full max-h-[90vh] overflow-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-2xl font-bold">{agent ? 'Edit Agent' : 'Create New Agent'}</h2>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="bg-destructive/10 text-destructive px-4 py-2 rounded-md text-sm">
              {error}
            </div>
          )}

          <div>
            <label className="block text-sm font-medium mb-2">
              Agent Name <span className="text-destructive">*</span>
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              placeholder="e.g., Research Assistant"
              disabled={loading}
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">
              LLM Model <span className="text-destructive">*</span>
            </label>
            <select
              value={formData.model}
              onChange={(e) => setFormData({ ...formData, model: e.target.value })}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              disabled={loading}
            >
              <optgroup label="Anthropic Claude">
                <option value="claude-sonnet-4-6">Claude Sonnet 4.6 (Recommended)</option>
                <option value="claude-opus-4-6">Claude Opus 4.6 (Most Capable)</option>
                <option value="claude-sonnet-4-5-20250929">Claude Sonnet 4.5</option>
                <option value="claude-opus-4-5-20251101">Claude Opus 4.5</option>
                <option value="claude-3-5-haiku-20241022">Claude 3.5 Haiku (Fast)</option>
                <option value="claude-3-5-sonnet-20241022">Claude 3.5 Sonnet (Previous)</option>
              </optgroup>
              <optgroup label="OpenAI">
                <option value="gpt-4o">GPT-4o (Multimodal)</option>
                <option value="gpt-4o-mini">GPT-4o Mini (Fast & Efficient)</option>
                <option value="o1">o1 (Advanced Reasoning)</option>
                <option value="o1-mini">o1-mini (Fast Reasoning)</option>
                <option value="gpt-4-turbo">GPT-4 Turbo</option>
              </optgroup>
            </select>
            <p className="text-xs text-muted-foreground mt-1">
              Choose the AI model that best fits your agent's role and requirements
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">
              Expertise Domain <span className="text-destructive">*</span>
            </label>
            <input
              type="text"
              value={formData.expertise_domain}
              onChange={(e) =>
                setFormData({ ...formData, expertise_domain: e.target.value })
              }
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              placeholder="e.g., Scientific research, literature review, data analysis"
              disabled={loading}
            />
            <p className="text-xs text-muted-foreground mt-1">
              What is this agent specialized in?
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">
              System Prompt <span className="text-destructive">*</span>
            </label>
            <textarea
              value={formData.system_prompt}
              onChange={(e) =>
                setFormData({ ...formData, system_prompt: e.target.value })
              }
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm min-h-[150px]"
              placeholder="You are a helpful assistant that specializes in..."
              disabled={loading}
            />
            <p className="text-xs text-muted-foreground mt-1">
              Instructions that define how the agent should behave
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">
              Communication Style (Optional)
            </label>
            <textarea
              value={formData.communication_style}
              onChange={(e) =>
                setFormData({ ...formData, communication_style: e.target.value })
              }
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm min-h-[100px]"
              placeholder="e.g., Use short paragraphs, no bullet points. Write conversationally. Start with direct observations."
              disabled={loading}
            />
            <p className="text-xs text-muted-foreground mt-1">
              Specific instructions for HOW this agent should communicate (format, tone, structure). All agents use a fixed JSON output format internally: {"{\"message\": \"...\"}"}
            </p>
          </div>

          <div className="flex gap-3 pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={loading}
              className="flex-1"
            >
              Cancel
            </Button>
            <Button type="submit" disabled={loading} className="flex-1">
              {loading ? (agent ? 'Updating...' : 'Creating...') : (agent ? 'Update Agent' : 'Create Agent')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
