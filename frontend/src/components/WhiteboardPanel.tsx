import { useState, useEffect } from 'react';
import { useWhiteboardStore } from '@/stores/whiteboardStore';
import { whiteboardApi } from '@/services/api';
import type { WhiteboardEntry, WhiteboardLogEntry } from '@/types';
import { Button } from '@/components/ui/button';
import { ArrowLeft, Edit2, Trash2, Plus, Clock, X } from 'lucide-react';

const ENTRY_TYPES = ['goal', 'decision', 'constraint', 'open_question', 'strategy'] as const;
type EntryType = typeof ENTRY_TYPES[number];

const TYPE_COLORS: Record<EntryType, string> = {
  goal:          'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  decision:      'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  constraint:    'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  open_question: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
  strategy:      'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
};

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

interface EditState {
  key: string;
  value: string;
  entry_type: EntryType;
  reason: string;
}

interface AddState {
  key: string;
  value: string;
  entry_type: EntryType;
  reason: string;
}

interface Props {
  conversationId: string;
  onClose?: () => void;
}

export function WhiteboardPanel({ conversationId, onClose }: Props) {
  const { entries, setEntry, removeEntry } = useWhiteboardStore();
  const currentEntries = entries[conversationId] || [];

  const [historyKey, setHistoryKey] = useState<string | null>(null);
  const [historyEntries, setHistoryEntries] = useState<WhiteboardLogEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  const [editState, setEditState] = useState<EditState | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [removeReason, setRemoveReason] = useState('');

  const [showAdd, setShowAdd] = useState(false);
  const [addState, setAddState] = useState<AddState>({
    key: '',
    value: '',
    entry_type: 'strategy',
    reason: '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const openHistory = async (key: string) => {
    setHistoryKey(key);
    setHistoryLoading(true);
    try {
      const res = await whiteboardApi.getHistory(conversationId, key);
      setHistoryEntries(res.data);
    } catch {
      setHistoryEntries([]);
    } finally {
      setHistoryLoading(false);
    }
  };

  const handleSaveEdit = async () => {
    if (!editState) return;
    setSaving(true);
    setError(null);
    try {
      await setEntry(conversationId, editState.key, {
        entry_type: editState.entry_type,
        value: editState.value,
        reason: editState.reason || 'Edited by human',
      });
      setEditState(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async (key: string) => {
    setSaving(true);
    setError(null);
    try {
      await removeEntry(conversationId, key, removeReason || 'Removed by human');
      setRemoving(null);
      setRemoveReason('');
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to remove');
    } finally {
      setSaving(false);
    }
  };

  const handleAdd = async () => {
    if (!addState.key.trim() || !addState.value.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await setEntry(conversationId, addState.key.trim(), {
        entry_type: addState.entry_type,
        value: addState.value,
        reason: addState.reason || 'Added by human',
      });
      setAddState({ key: '', value: '', entry_type: 'strategy', reason: '' });
      setShowAdd(false);
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to add entry');
    } finally {
      setSaving(false);
    }
  };

  // History view
  if (historyKey !== null) {
    return (
      <div className="flex flex-col h-full">
        <div className="flex items-center gap-2 px-3 py-2 border-b">
          <Button variant="ghost" size="sm" onClick={() => setHistoryKey(null)}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <span className="text-sm font-medium">History: {historyKey}</span>
        </div>
        <div className="flex-1 overflow-auto p-3 space-y-2">
          {historyLoading ? (
            <p className="text-xs text-muted-foreground">Loading...</p>
          ) : historyEntries.length === 0 ? (
            <p className="text-xs text-muted-foreground">No history found.</p>
          ) : (
            historyEntries.map((log) => (
              <div key={log.id} className="rounded border p-2 text-xs space-y-1">
                <div className="flex items-center gap-2">
                  <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                    log.action === 'set'
                      ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                      : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                  }`}>
                    {log.action.toUpperCase()}
                  </span>
                  <span className="text-muted-foreground">{log.author_name}</span>
                  <span className="text-muted-foreground ml-auto">
                    {timeAgo(log.created_at)}
                  </span>
                </div>
                {log.old_value && (
                  <div className="text-muted-foreground line-through">{log.old_value}</div>
                )}
                {log.new_value && <div>{log.new_value}</div>}
                {log.reason && (
                  <div className="text-muted-foreground italic">{log.reason}</div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    );
  }

  // Main view
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 border-b">
        <span className="text-sm font-semibold">Whiteboard</span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { setShowAdd(v => !v); setError(null); }}
            title="Add entry"
          >
            <Plus className="h-4 w-4" />
          </Button>
          {onClose && (
            <Button variant="ghost" size="sm" onClick={onClose} title="Close whiteboard">
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>

      {error && (
        <div className="mx-3 mt-2 text-xs text-destructive bg-destructive/10 px-2 py-1 rounded">
          {error}
        </div>
      )}

      <div className="flex-1 overflow-auto p-3 space-y-2">
        {currentEntries.length === 0 && !showAdd ? (
          <div className="text-center py-8 text-xs text-muted-foreground space-y-1">
            <p>No entries yet.</p>
            <p>Agents can write shared goals, decisions,</p>
            <p>constraints, and strategies here.</p>
          </div>
        ) : (
          currentEntries.map((entry) => (
            <div key={entry.key}>
              {editState?.key === entry.key ? (
                <div className="rounded border p-2 space-y-2">
                  <select
                    className="w-full text-xs rounded border px-2 py-1 bg-background"
                    value={editState.entry_type}
                    onChange={(e) =>
                      setEditState({ ...editState, entry_type: e.target.value as EntryType })
                    }
                  >
                    {ENTRY_TYPES.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                  <textarea
                    className="w-full text-xs rounded border px-2 py-1 bg-background resize-none"
                    rows={3}
                    maxLength={240}
                    placeholder="Content (max 240 chars)"
                    value={editState.value}
                    onChange={(e) => setEditState({ ...editState, value: e.target.value })}
                  />
                  <input
                    className="w-full text-xs rounded border px-2 py-1 bg-background"
                    placeholder="Reason for change"
                    value={editState.reason}
                    onChange={(e) => setEditState({ ...editState, reason: e.target.value })}
                  />
                  <div className="flex gap-2">
                    <Button size="sm" onClick={handleSaveEdit} disabled={saving} className="flex-1 text-xs h-7">
                      Save
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setEditState(null)} className="flex-1 text-xs h-7">
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : removing === entry.key ? (
                <div className="rounded border p-2 space-y-2">
                  <p className="text-xs">Remove <strong>{entry.key}</strong>?</p>
                  <input
                    className="w-full text-xs rounded border px-2 py-1 bg-background"
                    placeholder="Reason (optional)"
                    value={removeReason}
                    onChange={(e) => setRemoveReason(e.target.value)}
                  />
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => handleRemove(entry.key)}
                      disabled={saving}
                      className="flex-1 text-xs h-7"
                    >
                      Remove
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => { setRemoving(null); setRemoveReason(''); }}
                      className="flex-1 text-xs h-7"
                    >
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="rounded border p-2 group hover:bg-muted/40 transition-colors">
                  <div className="flex items-start gap-2">
                    <span className={`shrink-0 mt-0.5 px-1.5 py-0.5 rounded text-xs font-medium ${TYPE_COLORS[entry.entry_type as EntryType] || ''}`}>
                      {entry.entry_type}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-medium text-muted-foreground mb-0.5">{entry.key.replace(/_/g, ' ')}</div>
                      <div className="text-sm break-words">{entry.value}</div>
                      <div className="text-xs text-muted-foreground mt-1">
                        — {entry.last_author_name} · {timeAgo(entry.updated_at)}
                      </div>
                    </div>
                    <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        title="View history"
                        onClick={() => openHistory(entry.key)}
                      >
                        <Clock className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0"
                        title="Edit"
                        onClick={() =>
                          setEditState({
                            key: entry.key,
                            value: entry.value,
                            entry_type: entry.entry_type as EntryType,
                            reason: '',
                          })
                        }
                      >
                        <Edit2 className="h-3 w-3" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 w-6 p-0 hover:text-destructive"
                        title="Remove"
                        onClick={() => setRemoving(entry.key)}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))
        )}

        {/* Add entry form */}
        {showAdd && (
          <div className="rounded border p-2 space-y-2 mt-2">
            <p className="text-xs font-medium">New entry</p>
            <input
              className="w-full text-xs rounded border px-2 py-1 bg-background"
              placeholder="Title (e.g. main_goal)"
              value={addState.key}
              onChange={(e) => setAddState({ ...addState, key: e.target.value })}
            />
            <select
              className="w-full text-xs rounded border px-2 py-1 bg-background"
              value={addState.entry_type}
              onChange={(e) =>
                setAddState({ ...addState, entry_type: e.target.value as EntryType })
              }
            >
              {ENTRY_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <textarea
              className="w-full text-xs rounded border px-2 py-1 bg-background resize-none"
              rows={3}
              maxLength={240}
              placeholder="Content (max 240 chars)"
              value={addState.value}
              onChange={(e) => setAddState({ ...addState, value: e.target.value })}
            />
            <input
              className="w-full text-xs rounded border px-2 py-1 bg-background"
              placeholder="Reason"
              value={addState.reason}
              onChange={(e) => setAddState({ ...addState, reason: e.target.value })}
            />
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={handleAdd}
                disabled={saving || !addState.key.trim() || !addState.value.trim()}
                className="flex-1 text-xs h-7"
              >
                Add
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => { setShowAdd(false); setError(null); }}
                className="flex-1 text-xs h-7"
              >
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
