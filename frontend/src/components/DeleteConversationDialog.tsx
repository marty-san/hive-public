import * as Dialog from '@radix-ui/react-dialog';
import { Trash2, X } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface Props {
  open: boolean;
  onClose: () => void;
  onConfirm: (deleteMemories: boolean) => void;
  title?: string;
}

export function DeleteConversationDialog({ open, onClose, onConfirm, title }: Props) {
  return (
    <Dialog.Root open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <Dialog.Content className="fixed z-50 left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-sm bg-background border rounded-xl shadow-2xl p-6 focus:outline-none">
          <div className="flex items-start gap-3 mb-4">
            <div className="p-2 rounded-lg bg-destructive/10 flex-shrink-0">
              <Trash2 className="h-4 w-4 text-destructive" />
            </div>
            <div>
              <Dialog.Title className="text-sm font-semibold">
                Delete "{title || 'this conversation'}"?
              </Dialog.Title>
              <Dialog.Description className="text-sm text-muted-foreground mt-1">
                This permanently deletes the conversation and all messages.
                Agent memories from this conversation can be kept or also deleted.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button className="ml-auto flex-shrink-0 text-muted-foreground hover:text-foreground transition-colors">
                <X className="h-4 w-4" />
              </button>
            </Dialog.Close>
          </div>

          <div className="space-y-2">
            <Button
              variant="outline"
              className="w-full justify-start text-sm h-10"
              onClick={() => { onConfirm(false); onClose(); }}
            >
              Delete conversation, keep memories
            </Button>
            <Button
              variant="destructive"
              className="w-full justify-start text-sm h-10"
              onClick={() => { onConfirm(true); onClose(); }}
            >
              Delete conversation and memories
            </Button>
            <Button
              variant="ghost"
              className="w-full text-sm h-9"
              onClick={onClose}
            >
              Cancel
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
