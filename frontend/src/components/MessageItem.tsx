import { memo, useMemo, useState } from 'react';
import { RotateCcw, FileText } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Message } from '@/types';
import { Button } from '@/components/ui/button';

// Derive a consistent color from an agent name (same algorithm as Chat.tsx)
function agentColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue}, 60%, 42%)`;
}

// Preprocess @mentions into markdown links with a custom mention:// protocol.
function preprocessMentions(content: string): string {
  return content.replace(
    /@(?:"([^"]+)"|([A-Za-z0-9/][A-Za-z0-9/\s\-]*?))(?=[\s,!?.;:]|$)/g,
    (_match, quoted, unquoted) => {
      const name = (quoted ?? unquoted).trim();
      return `[@${name}](mention://${encodeURIComponent(name)})`;
    }
  );
}

interface MessageItemProps {
  message: Message;
  onRewind?: (messageId: string) => void;
  canRewind?: boolean;
}

export const MessageItem = memo(({ message, onRewind, canRewind = false }: MessageItemProps) => {
  const [isHovered, setIsHovered] = useState(false);

  const processedContent = useMemo(() => preprocessMentions(message.content), [message.content]);

  const handleRewind = () => {
    if (!onRewind) return;
    const confirmed = window.confirm(
      'Are you sure you want to rewind to this message?\n\nThis will delete all messages after this point and cannot be undone.'
    );
    if (confirmed) onRewind(message.id);
  };

  // Summary messages
  if (message.sender_type === 'system' && message.extra_data?.type === 'summary') {
    return (
      <div className="my-4 mx-auto max-w-2xl">
        <div className="border border-border/60 rounded-lg bg-muted/30 overflow-hidden">
          <div className="flex items-center gap-2 px-4 py-2 border-b border-border/40 bg-muted/50">
            <FileText className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-xs font-medium text-muted-foreground">Discussion Summary</span>
            <span className="ml-auto text-xs text-muted-foreground/60">
              {new Date(message.created_at).toLocaleTimeString()}
            </span>
          </div>
          <div className="px-4 py-3 font-quattro text-[0.9375rem] prose prose-sm max-w-none dark:prose-invert prose-p:my-1 prose-ul:my-1 prose-li:my-0">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          </div>
        </div>
      </div>
    );
  }

  // Generic system messages (event pills)
  if (message.sender_type === 'system') {
    return (
      <div className="flex justify-center my-4">
        <div className="bg-muted/50 rounded-full px-4 py-1.5 text-xs text-muted-foreground flex items-center gap-2">
          <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50" />
          {message.content}
          <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground/50" />
        </div>
      </div>
    );
  }

  // Human messages — left-aligned block with right accent border and subtle background
  if (message.sender_type === 'human') {
    return (
      <div
        className="group relative pl-3 pr-3 py-2 rounded-sm bg-primary/5 border border-border/60"
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        {/* Header */}
        <div className="flex items-baseline gap-2 mb-1">
          <span className="text-sm font-semibold text-primary">You</span>
          <span className="text-xs text-muted-foreground">
            {new Date(message.created_at).toLocaleTimeString()}
          </span>
        </div>

        {/* Content */}
        <div className="font-quattro text-[0.9375rem] prose prose-sm max-w-none dark:prose-invert prose-p:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0 prose-headings:my-2 prose-pre:overflow-x-auto prose-pre:max-w-full prose-code:break-words break-words">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href, children }) => {
                if (href?.startsWith('mention://')) {
                  return (
                    <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium not-prose bg-primary/10 text-primary">
                      {children}
                    </span>
                  );
                }
                return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
              },
              code: ({ children, className }) => {
                const isBlock = className?.includes('language-');
                if (isBlock) return <code className={className}>{children}</code>;
                return <code className="bg-muted/70 border border-border/40 rounded px-1 py-0.5 text-sm font-mono break-words not-prose">{children}</code>;
              },
            }}
          >
            {processedContent}
          </ReactMarkdown>
        </div>

        {canRewind && onRewind && isHovered && (
          <div className="absolute top-1 right-8">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 transition-opacity bg-background/80 backdrop-blur-sm hover:bg-destructive/10"
              onClick={handleRewind}
              title="Rewind to here"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </Button>
          </div>
        )}
      </div>
    );
  }

  // Agent messages — full-width with colored left border
  const color = message.sender_name ? agentColor(message.sender_name) : 'hsl(0,0%,60%)';

  return (
    <div
      className="group relative pl-3 py-0.5"
      style={{ borderLeft: `3px solid ${color}` }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Agent name + timestamp */}
      <div className="flex items-baseline gap-2 mb-1">
        <span className="text-sm font-semibold" style={{ color }}>
          {message.sender_name || 'Agent'}
        </span>
        <span className="text-xs text-muted-foreground">
          {new Date(message.created_at).toLocaleTimeString()}
        </span>
      </div>

      {/* Message content */}
      <div className="font-quattro text-[0.9375rem] prose prose-sm max-w-none dark:prose-invert prose-p:my-1.5 prose-ul:my-1.5 prose-ol:my-1.5 prose-li:my-0 prose-headings:my-2 prose-pre:overflow-x-auto prose-pre:max-w-full prose-code:break-words break-words">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => {
              if (href?.startsWith('mention://')) {
                return (
                  <span className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium not-prose bg-primary/10 text-primary">
                    {children}
                  </span>
                );
              }
              return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
            },
            code: ({ children, className }) => {
              const isBlock = className?.includes('language-');
              if (isBlock) return <code className={className}>{children}</code>;
              return <code className="bg-muted/70 border border-border/40 rounded px-1 py-0.5 text-sm font-mono break-words not-prose">{children}</code>;
            },
          }}
        >
          {processedContent}
        </ReactMarkdown>
      </div>

      {/* Rewind button */}
      {canRewind && onRewind && isHovered && (
        <div className="absolute top-1 right-2">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 w-7 p-0 opacity-0 group-hover:opacity-100 transition-opacity bg-background/80 backdrop-blur-sm hover:bg-destructive/10"
            onClick={handleRewind}
            title="Rewind to here"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}, (prevProps, nextProps) => {
  return prevProps.message.id === nextProps.message.id &&
         prevProps.message.content === nextProps.message.content &&
         prevProps.canRewind === nextProps.canRewind;
});

MessageItem.displayName = 'MessageItem';
