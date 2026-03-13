import { useRef, useState, useEffect, useCallback, forwardRef, useImperativeHandle } from 'react';
import type { Agent } from '@/types';

interface MentionState {
  active: boolean;
  query: string;
  triggerNode: Node | null;
  triggerOffset: number;
}

export interface MentionInputHandle {
  getValue: () => string;
  clear: () => void;
  focus: () => void;
}

interface MentionInputProps {
  onChange?: (value: string) => void;
  onSubmit?: () => void;
  disabled?: boolean;
  placeholder?: string;
  agents: Agent[];
  className?: string;
}

/** Convert contenteditable DOM content → plain text with @"Name" mention markers */
function serializeContent(el: HTMLElement): string {
  let text = '';
  for (const node of Array.from(el.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent ?? '';
    } else if (node instanceof HTMLElement) {
      if (node.dataset.mention === 'true') {
        const name = node.dataset.agentName ?? '';
        text += name.includes(' ') ? `@"${name}"` : `@${name}`;
      } else if (node.tagName === 'BR') {
        text += '\n';
      } else if (node.tagName === 'DIV') {
        // Chrome wraps new lines in divs
        text += '\n' + serializeContent(node);
      } else {
        text += serializeContent(node);
      }
    }
  }
  return text;
}

export const MentionInput = forwardRef<MentionInputHandle, MentionInputProps>(
  ({ onChange, onSubmit, disabled, placeholder, agents, className }, ref) => {
    const editorRef = useRef<HTMLDivElement>(null);
    const [isEmpty, setIsEmpty] = useState(true);
    const [mention, setMention] = useState<MentionState>({
      active: false,
      query: '',
      triggerNode: null,
      triggerOffset: 0,
    });
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [dropdownLeft, setDropdownLeft] = useState(0);

    const filteredAgents = mention.active
      ? agents.filter(a => a.name.toLowerCase().includes(mention.query.toLowerCase()))
      : [];

    const closeMention = useCallback(() => {
      setMention({ active: false, query: '', triggerNode: null, triggerOffset: 0 });
      setSelectedIndex(0);
    }, []);

    const notifyChange = useCallback(() => {
      if (!editorRef.current) return;
      const value = serializeContent(editorRef.current);
      const hasContent = value.trim().length > 0;
      setIsEmpty(!hasContent);
      onChange?.(value);
    }, [onChange]);

    useImperativeHandle(ref, () => ({
      getValue: () => (editorRef.current ? serializeContent(editorRef.current) : ''),
      clear: () => {
        if (editorRef.current) {
          editorRef.current.innerHTML = '';
        }
        setIsEmpty(true);
        onChange?.('');
        closeMention();
      },
      focus: () => editorRef.current?.focus(),
    }), [onChange, closeMention]);

    const insertMention = useCallback((agent: Agent) => {
      const { triggerNode, triggerOffset, query } = mention;
      if (!triggerNode || !editorRef.current) return;

      const selection = window.getSelection();
      if (!selection) return;

      const textNode = triggerNode as Text;
      const endOffset = Math.min(triggerOffset + 1 + query.length, textNode.length);

      const range = document.createRange();
      range.setStart(textNode, triggerOffset);
      range.setEnd(textNode, endOffset);
      range.deleteContents();

      // Create the pill span (non-editable)
      const pill = document.createElement('span');
      pill.dataset.mention = 'true';
      pill.dataset.agentName = agent.name;
      pill.contentEditable = 'false';
      pill.className = [
        'inline-flex items-center',
        'rounded-full px-2 py-0.5',
        'text-xs font-medium',
        'bg-blue-100 text-blue-800',
        'dark:bg-blue-900 dark:text-blue-200',
        'mx-0.5 select-none cursor-default',
        'align-middle',
      ].join(' ');
      pill.textContent = `@${agent.name}`;

      // Space after pill for cursor to land on
      const space = document.createTextNode(' ');

      range.insertNode(space);
      range.insertNode(pill);

      // Move cursor after the space
      const newRange = document.createRange();
      newRange.setStartAfter(space);
      newRange.collapse(true);
      selection.removeAllRanges();
      selection.addRange(newRange);

      closeMention();
      notifyChange();
    }, [mention, closeMention, notifyChange]);

    const handleInput = useCallback(() => {
      notifyChange();

      const selection = window.getSelection();
      if (!selection || !selection.rangeCount) {
        closeMention();
        return;
      }

      const range = selection.getRangeAt(0);
      const node = range.startContainer;
      const offset = range.startOffset;

      if (node.nodeType !== Node.TEXT_NODE) {
        closeMention();
        return;
      }

      const text = node.textContent ?? '';
      const before = text.slice(0, offset);
      const atIndex = before.lastIndexOf('@');

      if (atIndex === -1) {
        closeMention();
        return;
      }

      // Only trigger if @ is at the start or preceded by whitespace
      const charBeforeAt = atIndex > 0 ? before[atIndex - 1] : ' ';
      if (!/\s/.test(charBeforeAt)) {
        closeMention();
        return;
      }

      const query = before.slice(atIndex + 1);
      // Close if there's a space in the query (user moved past mention)
      if (query.includes(' ')) {
        closeMention();
        return;
      }

      // Compute horizontal position of @ for dropdown alignment
      const atRange = document.createRange();
      atRange.setStart(node, atIndex);
      atRange.setEnd(node, atIndex + 1);
      const atRect = atRange.getBoundingClientRect();
      const editorRect = editorRef.current!.getBoundingClientRect();
      setDropdownLeft(Math.max(0, atRect.left - editorRect.left));

      setMention({ active: true, query, triggerNode: node, triggerOffset: atIndex });
      setSelectedIndex(0);
    }, [notifyChange, closeMention]);

    const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
      if (mention.active) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          if (filteredAgents.length > 0) {
            setSelectedIndex(i => (i + 1) % filteredAgents.length);
          }
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          if (filteredAgents.length > 0) {
            setSelectedIndex(i => (i - 1 + filteredAgents.length) % filteredAgents.length);
          }
          return;
        }
        if ((e.key === 'Enter' || e.key === 'Tab') && filteredAgents.length > 0) {
          e.preventDefault();
          insertMention(filteredAgents[selectedIndex]);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          closeMention();
          return;
        }
      }

      // Enter = submit (when not in mention mode)
      if (e.key === 'Enter' && !mention.active) {
        e.preventDefault();
        onSubmit?.();
      }
    }, [mention.active, filteredAgents, selectedIndex, insertMention, closeMention, onSubmit]);

    // Close mention when cursor moves outside an @query context
    useEffect(() => {
      if (!mention.active) return;
      const checkSelection = () => {
        const selection = window.getSelection();
        if (!selection?.rangeCount) { closeMention(); return; }

        const range = selection.getRangeAt(0);
        const node = range.startContainer;
        if (node.nodeType !== Node.TEXT_NODE) { closeMention(); return; }

        const before = (node.textContent ?? '').slice(0, range.startOffset);
        const atIndex = before.lastIndexOf('@');
        if (atIndex === -1 || before.slice(atIndex + 1).includes(' ')) {
          closeMention();
        }
      };
      document.addEventListener('selectionchange', checkSelection);
      return () => document.removeEventListener('selectionchange', checkSelection);
    }, [mention.active, closeMention]);

    // Paste as plain text to avoid importing styled HTML
    const handlePaste = useCallback((e: React.ClipboardEvent<HTMLDivElement>) => {
      e.preventDefault();
      const text = e.clipboardData.getData('text/plain');
      document.execCommand('insertText', false, text);
      notifyChange();
    }, [notifyChange]);

    return (
      <div className={`relative flex-1 ${className ?? ''}`}>
        {/* Placeholder text overlay */}
        {isEmpty && (
          <div
            className="absolute inset-0 px-3 py-2 text-sm text-muted-foreground pointer-events-none select-none"
            aria-hidden
          >
            {placeholder}
          </div>
        )}

        {/* Contenteditable editor */}
        <div
          ref={editorRef}
          role="textbox"
          aria-multiline="true"
          aria-label="Message input"
          contentEditable={disabled ? 'false' : 'true'}
          suppressContentEditableWarning
          onInput={handleInput}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onBlur={closeMention}
          className={[
            'min-h-[40px] max-h-[200px] overflow-y-auto',
            'px-3 py-2 text-sm',
            'rounded-md border border-input bg-background',
            'focus:outline-none focus:ring-1 focus:ring-ring',
            'break-words whitespace-pre-wrap',
            disabled ? 'opacity-50 cursor-not-allowed' : '',
          ].join(' ')}
        />

        {/* @mention dropdown */}
        {mention.active && filteredAgents.length > 0 && (
          <div
            className="absolute z-50 bg-popover border border-border rounded-md shadow-lg py-1 w-56 max-h-48 overflow-y-auto"
            style={{ bottom: 'calc(100% + 4px)', left: `${dropdownLeft}px` }}
          >
            {filteredAgents.map((agent, i) => (
              <button
                key={agent.id}
                type="button"
                onMouseDown={(e) => {
                  // Prevent blur so editor stays focused
                  e.preventDefault();
                  insertMention(agent);
                }}
                onMouseEnter={() => setSelectedIndex(i)}
                className={[
                  'w-full px-3 py-2 text-left text-sm',
                  'flex items-center gap-2',
                  i === selectedIndex
                    ? 'bg-accent text-accent-foreground'
                    : 'hover:bg-accent hover:text-accent-foreground',
                ].join(' ')}
              >
                <span className="font-medium truncate">{agent.name}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }
);

MentionInput.displayName = 'MentionInput';
