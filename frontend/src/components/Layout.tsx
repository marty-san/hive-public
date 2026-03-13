import { ReactNode, useEffect } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { Users, Plus } from 'lucide-react';
import { useConversationStore } from '@/stores/conversationStore';
import { Button } from '@/components/ui/button';

interface LayoutProps {
  children: ReactNode;
}

export function Layout({ children }: LayoutProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const { conversations, fetchConversations, createConversation } = useConversationStore();

  useEffect(() => {
    fetchConversations();
  }, [fetchConversations]);

  const sortedConversations = [...conversations].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
  );

  const activeConvId = location.pathname.startsWith('/chat/')
    ? location.pathname.split('/chat/')[1]
    : null;

  const handleNewConversation = async () => {
    const newConv = await createConversation({ title: 'New Conversation', initial_participants: [] });
    navigate(`/chat/${newConv.id}`);
  };

  return (
    <div className="flex h-screen bg-background">
      {/* Sidebar */}
      <aside className="w-56 border-r bg-card flex flex-col flex-shrink-0">
        {/* Logo */}
        <div className="flex h-13 items-center border-b px-4 gap-2 flex-shrink-0 py-3">
          <img src="/img/hive.png" alt="Hive icon" className="h-7 w-7" />
          <h1 className="text-lg font-bold tracking-tight">Hive</h1>
        </div>

        {/* Top actions */}
        <div className="px-3 pt-3 pb-2 flex-shrink-0 space-y-1.5">
          <Button
            onClick={handleNewConversation}
            variant="outline"
            size="sm"
            className="w-full justify-start gap-2 h-8 text-xs font-medium"
          >
            <Plus className="h-3.5 w-3.5" />
            New Conversation
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="w-full justify-start gap-2 h-8 text-xs font-medium"
            asChild
          >
            <Link to="/agents">
              <Users className="h-3.5 w-3.5" />
              Manage Agents
            </Link>
          </Button>
        </div>

        {/* Conversation list label */}
        <div className="px-4 pb-1 flex-shrink-0">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60">
            Conversations
          </span>
        </div>

        {/* Conversation list */}
        <nav className="flex-1 overflow-y-auto pb-2">
          {sortedConversations.length === 0 ? (
            <p className="text-xs text-muted-foreground px-4 py-2">No conversations yet</p>
          ) : (
            sortedConversations.map((conv) => {
              const isActive = conv.id === activeConvId;
              return (
                <Link
                  key={conv.id}
                  to={`/chat/${conv.id}`}
                  className={cn(
                    'block px-3 py-1.5 text-sm mx-2 rounded-md transition-colors truncate',
                    isActive
                      ? 'bg-accent text-accent-foreground font-medium'
                      : 'text-muted-foreground hover:bg-accent/50 hover:text-foreground'
                  )}
                  title={conv.title || 'Untitled Conversation'}
                >
                  {conv.title || 'Untitled Conversation'}
                </Link>
              );
            })
          )}
        </nav>

      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto min-w-0">
        {children}
      </main>
    </div>
  );
}
