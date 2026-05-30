/**
 * Tiny dependency-free toast store (sonner-style) backed by an external
 * store so it can be fired from anywhere — including react-query mutation
 * callbacks — without threading a hook through every caller.
 *
 * `toast.success/error/info/message(...)` push a toast; <Toaster/> subscribes
 * via useSyncExternalStore and renders them.
 */
export type ToastVariant = "default" | "success" | "error" | "info";

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastItem {
  id: number;
  title: string;
  description?: string;
  variant: ToastVariant;
  action?: ToastAction;
  duration: number; // ms; 0 = sticky
}

export type ToastOptions = Partial<
  Pick<ToastItem, "description" | "action" | "duration">
>;

let items: ToastItem[] = [];
const listeners = new Set<() => void>();
let nextId = 1;

function emit() {
  for (const l of listeners) l();
}

export function subscribeToasts(l: () => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

export function getToasts(): ToastItem[] {
  return items;
}

export function dismissToast(id: number): void {
  const next = items.filter((t) => t.id !== id);
  if (next.length !== items.length) {
    items = next;
    emit();
  }
}

function push(variant: ToastVariant, title: string, opts: ToastOptions = {}): number {
  const id = nextId++;
  const duration = opts.duration ?? (variant === "error" ? 8000 : 5000);
  items = [...items, { id, title, variant, duration, ...opts }];
  emit();
  if (duration > 0) {
    // setTimeout is fine here; the store is module-global, not React state.
    setTimeout(() => dismissToast(id), duration);
  }
  return id;
}

export const toast = {
  success: (title: string, opts?: ToastOptions) => push("success", title, opts),
  error: (title: string, opts?: ToastOptions) => push("error", title, opts),
  info: (title: string, opts?: ToastOptions) => push("info", title, opts),
  message: (title: string, opts?: ToastOptions) => push("default", title, opts),
};
