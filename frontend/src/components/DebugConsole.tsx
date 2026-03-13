import { useState } from 'react';
import { useDebugStore } from '@/stores/debugStore';
import { Button } from '@/components/ui/button';
import { ChevronDown, ChevronUp, Trash2, Bug } from 'lucide-react';

export function DebugConsole() {
  const { events, enabled, clearDebugEvents, toggleDebug } = useDebugStore();
  const [collapsed, setCollapsed] = useState(false);
  const [filter, setFilter] = useState<string>('all');

  if (!enabled) return null;

  const filteredEvents = filter === 'all'
    ? events
    : events.filter(e => e.event_type === filter);

  const eventTypes = ['all', ...Array.from(new Set(events.map(e => e.event_type)))];

  return (
    <div className="fixed bottom-0 right-0 w-full md:w-[600px] bg-background border-l border-t shadow-lg z-50">
      {/* Header */}
      <div className="flex items-center justify-between p-2 border-b bg-muted">
        <div className="flex items-center gap-2">
          <Bug className="h-4 w-4 text-orange-500" />
          <span className="text-sm font-semibold">Debug Console</span>
          <span className="text-xs text-muted-foreground">
            ({filteredEvents.length} events)
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            onClick={clearDebugEvents}
            size="sm"
            variant="ghost"
            title="Clear events"
          >
            <Trash2 className="h-3 w-3" />
          </Button>
          <Button
            onClick={() => setCollapsed(!collapsed)}
            size="sm"
            variant="ghost"
          >
            {collapsed ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
          <Button
            onClick={toggleDebug}
            size="sm"
            variant="ghost"
            title="Close debug console"
          >
            ×
          </Button>
        </div>
      </div>

      {!collapsed && (
        <>
          {/* Filters */}
          <div className="flex gap-1 p-2 border-b overflow-x-auto">
            {eventTypes.map(type => (
              <Button
                key={type}
                onClick={() => setFilter(type)}
                size="sm"
                variant={filter === type ? 'default' : 'outline'}
                className="text-xs whitespace-nowrap"
              >
                {type}
              </Button>
            ))}
          </div>

          {/* Events */}
          <div className="h-[300px] overflow-y-auto p-2 space-y-2 font-mono text-xs">
            {filteredEvents.length === 0 ? (
              <div className="text-center text-muted-foreground py-8">
                No debug events yet
              </div>
            ) : (
              filteredEvents.slice().reverse().map((event) => (
                <DebugEventItem key={event.id} event={event} />
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}

function DebugEventItem({ event }: { event: any }) {
  const [expanded, setExpanded] = useState(false);

  const getEventColor = (type: string) => {
    switch (type) {
      case 'agent_selection':
        return 'text-blue-600 bg-blue-50 border-blue-200';
      case 'memory_retrieval':
        return 'text-purple-600 bg-purple-50 border-purple-200';
      case 'response_generated':
        return 'text-green-600 bg-green-50 border-green-200';
      case 'api_error':
        return 'text-red-600 bg-red-50 border-red-200';
      case 'bids_collected':
        return 'text-cyan-700 bg-cyan-50 border-cyan-200';
      case 'tool_call':
        return 'text-orange-600 bg-orange-50 border-orange-200';
      default:
        return 'text-gray-600 bg-gray-50 border-gray-200';
    }
  };

  const formatTime = (timestamp: string) => {
    try {
      return new Date(timestamp).toLocaleTimeString();
    } catch {
      return timestamp;
    }
  };

  return (
    <div className={`border rounded p-2 ${getEventColor(event.event_type)}`}>
      <div
        className="flex items-start justify-between cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <span className="font-semibold">{event.event_type}</span>
            {event.data?.agent_name && (
              <span className="text-xs opacity-75">• {event.data.agent_name}</span>
            )}
            <span className="text-xs opacity-75">
              {formatTime(event.timestamp)}
            </span>
          </div>
          {!expanded && (
            <div className="text-xs opacity-75 truncate mt-1">
              {getEventSummary(event)}
            </div>
          )}
        </div>
        <ChevronDown
          className={`h-4 w-4 transition-transform ${expanded ? 'rotate-180' : ''}`}
        />
      </div>

      {expanded && (
        <div className="mt-2 pt-2 border-t border-current/20">
          {event.event_type === 'bids_collected' && event.data?.bids ? (
            <div className="space-y-1">
              {event.data.bids.map((bid: any, i: number) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  <span className="font-medium w-28 truncate">{bid.agent_name}</span>
                  <BidTypePill turnType={bid.turn_type} />
                  <span className="opacity-60">{Math.round(bid.confidence * 100)}%</span>
                  {bid.target && <span className="opacity-60">→ {bid.target}</span>}
                  {bid.preview && <span className="opacity-75 truncate flex-1">{bid.preview}</span>}
                </div>
              ))}
            </div>
          ) : (
            <pre className="text-xs whitespace-pre-wrap overflow-x-auto">
              {JSON.stringify(event.data, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function getEventSummary(event: any): string {
  switch (event.event_type) {
    case 'agent_selection':
      return `Selected ${event.data?.selected_count || 0} agents from ${event.data?.total_agents || 0}`;
    case 'memory_retrieval':
      return `Retrieved ${event.data?.memory_count || 0} memories`;
    case 'response_generated':
      return `${event.data?.total_tokens || 0} tokens in ${event.data?.api_duration_seconds || 0}s`;
    case 'api_error':
      return `Error: ${event.data?.error_type || 'Unknown'}`;
    case 'bids_collected': {
      const bids: any[] = event.data?.bids || [];
      const summary = bids.map((b: any) => `${b.agent_name}: ${b.turn_type}`).join(' · ');
      return summary || '0 bids';
    }
    case 'tool_call':
      return `${event.data?.tool} (${event.data?.phase || event.data?.agent_name || ''})`;
    default:
      return JSON.stringify(event.data).substring(0, 100);
  }
}

const BID_TYPE_COLORS: Record<string, string> = {
  conveyance:       'bg-blue-100 text-blue-700',
  challenge:        'bg-red-100 text-red-700',
  question:         'bg-yellow-100 text-yellow-700',
  convergence:      'bg-green-100 text-green-700',
  pass:             'bg-gray-100 text-gray-500',
  backchannel:      'bg-gray-100 text-gray-500',
  propose_addition: 'bg-emerald-100 text-emerald-700',
  propose_removal:  'bg-rose-100 text-rose-700',
};

function BidTypePill({ turnType }: { turnType: string }) {
  const cls = BID_TYPE_COLORS[turnType] || 'bg-gray-100 text-gray-600';
  return (
    <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${cls}`}>
      {turnType}
    </span>
  );
}
