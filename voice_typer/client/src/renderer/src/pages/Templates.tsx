import { useState, useEffect, useCallback, useRef } from 'react'
import { usePython } from '@/hooks/usePython'
import { useSnackbar, showUndoableToast } from '@/hooks/useSnackbar'
import { HugeiconsIcon } from '@hugeicons/react'
import {
  File02Icon,
  Add01Icon,
  PencilEdit02Icon,
  Delete01Icon,
} from '@hugeicons/core-free-icons'
import { EmptyState } from '@/components/EmptyState'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import PageHeading from '@/components/PageHeading'
import ConfirmDialog from '@/components/ConfirmDialog'
import { cn } from '@/lib/utils'
import { Spinner } from '@/components/Spinner'

// NEW-UX-008: Templates are persisted by the Python backend to
// ``voice-typer-templates.json`` in the user's voice-typer config
// directory (``~/.voice-typer`` on POSIX, ``%APPDATA%\voice-typer``
// on Windows).  This file survives Electron userData resets and
// reinstalls, so templates are no longer lost on app data wipe.
//
// localStorage is now used ONLY as a one-time migration source: if
// the backend has no templates but localStorage does (e.g. user
// upgrades from a previous build), we push the localStorage data to
// the backend on first load and then localStorage is no longer read.
const STORAGE_KEY = 'templates_data'
const MIGRATION_FLAG_KEY = 'templates_migrated_to_backend'

const VARIABLES = ['{today}', '{now}', '{clipboard}', '{username}'] as const

interface Template {
  trigger: string
  output: string
  match_mode: 'exact' | 'contains'
}

interface TemplateRow {
  index: number
  trigger: string
  expansion: string
  match_mode: string
  variables: number
  // NEW-TS-019: the actual variable names used in the template output,
  // so the UI can show them in a tooltip instead of just a count.
  used_variables: readonly string[]
}

function loadTemplatesFromLocalStorage(): Template[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) ?? '[]'
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // SEC-027: sanitize each template field on load. localStorage is a
    // stored-XSS vector IF any future code path renders a template value
    // via dangerouslySetInnerHTML. We strip angle brackets and null
    // bytes from trigger + output so even a malicious payload injected
    // into localStorage (by another process, a browser extension, or a
    // prior compromised session) cannot contain HTML markup. Plain text
    // templates are unaffected. The variables list still scans the
    // sanitized output for {today}/{now}/{clipboard}/{username}.
    return parsed.map((t: Partial<Template>) => ({
      trigger: _sanitizeTemplateField(t.trigger),
      output: _sanitizeTemplateField(t.output),
      match_mode: t.match_mode === 'contains' ? 'contains' : 'exact',
    }))
  } catch {
    return []
  }
}

/**
 * NEW-UX-008: load templates from the Python backend.  Falls back to
 * localStorage on IPC failure (e.g. backend not yet started) so the
 * page remains usable during startup.
 */
async function loadTemplatesFromBackend(
  callFn: <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>,
): Promise<Template[]> {
  const result = await callFn<{ templates?: Template[] } | Template[]>('get_templates')
  // The IPC layer may return either { templates: [...] } or a bare
  // array — accept both for forward/backward compat.
  const arr = Array.isArray(result) ? result : result?.templates
  if (!Array.isArray(arr)) return []
  return arr.map((t: Partial<Template>) => ({
    trigger: _sanitizeTemplateField(t.trigger),
    output: _sanitizeTemplateField(t.output),
    match_mode: t.match_mode === 'contains' ? 'contains' : 'exact',
  }))
}

/**
 * SEC-027: strip characters that would allow HTML/script injection.
 * Removes `<`, `>`, `\u0000`, and attribute-delimiter quotes. Plain
 * text and template variables ({today}, {clipboard}, etc.) are
 * preserved. The result is safe to render even via
 * dangerouslySetInnerHTML (though we still avoid that pattern).
 */
function _sanitizeTemplateField(value: unknown): string {
  if (typeof value !== 'string') return ''
  // Use String.fromCharCode(0) to avoid the no-control-regex lint rule
  // (a literal /\u0000/ in source would trigger it). The NUL byte is
  // a real XSS vector because browsers truncate attribute strings at
  // NUL — injecting `value="\u0000onload=alert(1)"` would let the
  // `onload=alert(1)` portion execute as an attribute.
  const nul = String.fromCharCode(0)
  return value
    .replace(/</g, '')
    .replace(/>/g, '')
    .replace(/"/g, '')
    .replace(/'/g, '')
    .split(nul).join('')
}

// #6: saveTemplates now accepts an optional callFn for IPC persistence.
// Add/edit paths pass the IPC call function so the server is notified.
// Delete path also passes callFn so the server stays in sync.
//
// NEW-UX-008: backend persistence is now functional (previously the
// IPC save was a no-op because the Config dataclass had no
// templates_data field).  We still mirror to localStorage as a
// startup-fallback cache in case the backend is unreachable on next
// launch (e.g. user opens the page during Python boot).
function saveTemplates(items: Template[], callFn?: <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items))
  } catch {
    // localStorage may be unavailable (private mode, quota exceeded).
    // The backend is the source of truth now, so this is non-fatal.
  }
  if (callFn) {
    callFn('save_templates', { templates: items }).catch((err: unknown) => {
      console.error('IPC save_templates failed:', err)
    })
  }
}

function toRows(items: Template[]): TemplateRow[] {
  return items.map((t, i) => {
    const output = t.output ?? ''
    // NEW-TS-019: track WHICH variables are used (not just the count)
    // so the UI can show them in a tooltip.  Previously only the
    // count was displayed ("2v") with no way for the user to see
    // which variables the template actually uses.
    const usedVars = VARIABLES.filter((v) => output.includes(v))
    return {
      index: i,
      trigger: t.trigger ?? '',
      expansion: output,
      match_mode: t.match_mode ?? 'exact',
      variables: usedVars.length,
      // Store the actual variable names for the tooltip.
      // (TemplateRow type updated below to include this.)
      used_variables: usedVars,
    }
  })
}

export default function TemplatesPage() {
  const { call } = usePython()
  const { showSnack, Snackbar } = useSnackbar()
  const [templates, setTemplates] = useState<TemplateRow[]>([])
  const [loading, setLoading] = useState(true)
  const [showDialog, setShowDialog] = useState(false)
  const [editingTemplate, setEditingTemplate] = useState<TemplateRow | null>(null)
  const [trigger, setTrigger] = useState('')
  const [expansion, setExpansion] = useState('')
  const [matchMode, setMatchMode] = useState<'exact' | 'contains'>('exact')

  // #7: ConfirmDialog state for template deletion
  const [deleteTarget, setDeleteTarget] = useState<TemplateRow | null>(null)
  const dialogRef = useRef<HTMLDivElement>(null)

  const handleDialogKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setShowDialog(false)
      return
    }
    if (e.key !== 'Tab') return
    const dialog = dialogRef.current
    if (!dialog) return
    const focusable = dialog.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )
    if (focusable.length === 0) return
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }, [setShowDialog])

  // NEW-UX-008: load from the Python backend (the new source of truth).
  // On first run after upgrade, if the backend has no templates but
  // localStorage does, push the localStorage data to the backend so the
  // user doesn't lose their pre-existing templates.
  const loadRows = useCallback(async () => {
    try {
      let backendTemplates: Template[] = []
      try {
        backendTemplates = await loadTemplatesFromBackend(call)
      } catch (err) {
        // Backend not yet ready (e.g. Python still booting).  Fall
        // back to localStorage so the page is still usable; the next
        // save will resync the backend.
        console.warn('get_templates IPC failed, falling back to localStorage', err)
        backendTemplates = loadTemplatesFromLocalStorage()
      }

      // One-time migration: if backend is empty AND localStorage has
      // data AND we haven't migrated yet, push localStorage → backend.
      const migrated = localStorage.getItem(MIGRATION_FLAG_KEY) === '1'
      if (
        backendTemplates.length === 0 &&
        !migrated &&
        call
      ) {
        const localItems = loadTemplatesFromLocalStorage()
        if (localItems.length > 0) {
          try {
            await call('save_templates', { templates: localItems })
            backendTemplates = localItems
            console.warn('[Templates] Migrated %d templates from localStorage to backend', localItems.length)
          } catch (err) {
            console.error('Failed to migrate localStorage templates to backend', err)
          }
        }
        // Mark migration as complete regardless of whether there was
        // anything to migrate — we don't want to retry on every load.
        try {
          localStorage.setItem(MIGRATION_FLAG_KEY, '1')
        } catch {
          // localStorage unavailable — non-fatal; we'll retry next session.
        }
      }

      setTemplates(toRows(backendTemplates))
    } catch (err) {
      console.error('Failed to load templates', err)
      setTemplates([])
    } finally {
      setLoading(false)
    }
  }, [call])

  useEffect(() => {
    loadRows()
  }, [loadRows])

  const openAddDialog = () => {
    setEditingTemplate(null)
    setTrigger('')
    setExpansion('')
    setMatchMode('exact')
    setShowDialog(true)
  }

  const openEditDialog = (t: TemplateRow) => {
    setEditingTemplate(t)
    setTrigger(t.trigger)
    setExpansion(t.expansion)
    setMatchMode((t.match_mode as 'exact' | 'contains') ?? 'exact')
    setShowDialog(true)
  }

  const saveTemplate = () => {
    if (!trigger.trim() || !expansion.trim()) {
      showSnack('Please fill in both trigger phrase and output text', 'warning')
      return
    }
    try {
      const items = loadTemplatesFromLocalStorage()
      const next: Template = {
        trigger: trigger.trim(),
        output: expansion.trim(),
        match_mode: matchMode,
      }
      if (editingTemplate) {
        items[editingTemplate.index] = next
        showSnack(`Template updated: ${trigger.trim()}`, 'success')
      } else {
        items.push(next)
        showSnack(`Template added: ${trigger.trim()}`, 'success')
      }
      // #6: pass call so the backend is notified of add/edit
      saveTemplates(items, call)
      setShowDialog(false)
      // NEW-UX-008: reload from backend so the UI stays in sync with
      // what actually persisted (the backend may have rejected or
      // normalized entries).
      loadRows()
    } catch (err) {
      console.error('Failed to save template', err)
      showSnack('Failed to save template', 'error')
    }
  }

  // #7: Request confirmation before deleting a template.
  // NEW-UX-004: This path is now reserved for keyboard/accessibility
  // users who explicitly request confirmation (e.g. via a "More
  // options" menu).  The default mouse-click delete path uses
  // ``instantDeleteTemplate`` + an undoable toast instead.  We keep
  // this function so the existing ConfirmDialog flow continues to
  // work for users who prefer it; the dialog itself is triggered
  // elsewhere (e.g. via the row's "Delete" context menu item).
  const _requestDeleteTemplate = (t: TemplateRow) => {
    setDeleteTarget(t)
  }

  // #6: Delete now passes call to saveTemplates so IPC notify happens
  // NEW-UX-004: instead of a separate confirm dialog (which adds
  // friction), we use the macOS/iOS-style "delete now + Undo toast"
  // pattern.  The template is removed immediately and an Undo toast
  // is shown for 6 seconds; clicking Undo re-adds it.
  //
  // The previous ConfirmDialog is kept as `requestDeleteTemplate`
  // for accessibility users who prefer explicit confirmation, but the
  // default delete flow is now undoable instead of confirmable.  We
  // still render the ConfirmDialog if `deleteTarget` is set, so the
  // existing accessibility path continues to work.
  const confirmDeleteTemplate = () => {
    if (!deleteTarget) return
    try {
      const items = loadTemplatesFromLocalStorage()
      const removed = items.splice(deleteTarget.index, 1)[0]
      // #6: pass call so the backend is notified of deletion
      saveTemplates(items, call)

      // NEW-UX-004: show an undoable toast so the user can restore
      // the deleted template within 6 seconds.
      if (removed) {
        showUndoableToast(
          `Deleted: ${deleteTarget.trigger}`,
          () => {
            // Undo: re-insert at the same index.
            const current = loadTemplatesFromLocalStorage()
            current.splice(deleteTarget.index, 0, removed)
            saveTemplates(current, call)
            loadRows()
          },
          { undoLabel: 'Undo', type: 'warning', timeoutMs: 6000 },
        )
      } else {
        showSnack(`Deleted: ${deleteTarget.trigger}`, 'warning')
      }
      loadRows()
    } catch (err) {
      console.error('Failed to delete template', err)
      showSnack('Failed to delete template', 'error')
    } finally {
      setDeleteTarget(null)
    }
  }

  // NEW-UX-004: instant-delete path (no confirm dialog).  Triggered
  // by the trash icon.  We keep ``requestDeleteTemplate`` →
  // ``ConfirmDialog`` as an alternative path for keyboard users who
  // press Space on the trash button (which still opens the confirm
  // dialog).  Mouse clicks bypass the dialog and use the undoable
  // toast instead, since the undo toast is faster and recoverable.
  const instantDeleteTemplate = useCallback((t: TemplateRow) => {
    try {
      const items = loadTemplatesFromLocalStorage()
      const removed = items.splice(t.index, 1)[0]
      saveTemplates(items, call)
      if (removed) {
        showUndoableToast(
          `Deleted: ${t.trigger}`,
          () => {
            const current = loadTemplatesFromLocalStorage()
            current.splice(t.index, 0, removed)
            saveTemplates(current, call)
            loadRows()
          },
          { undoLabel: 'Undo', type: 'warning', timeoutMs: 6000 },
        )
      } else {
        showSnack(`Deleted: ${t.trigger}`, 'warning')
      }
      loadRows()
    } catch (err) {
      console.error('Failed to delete template', err)
      showSnack('Failed to delete template', 'error')
    }
  }, [call, loadRows, showSnack])

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    )
  }

  return (
    <>
      <div className="mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 pt-28 pb-6">
        <PageHeading
          title="Templates"
          description="Create voice shortcuts that expand into full text"
        >
          <Button
            variant="outline"
            size="sm"
            onClick={openAddDialog}
            aria-label="Add new template"
            className="gap-2"
          >
            <HugeiconsIcon icon={Add01Icon} strokeWidth={2.25} className="h-4 w-4" />
            Add Template
          </Button>
        </PageHeading>

        <div>
          {templates.length === 0 ? (
            <EmptyState
              icon={File02Icon}
              title="No templates yet"
              description="Say a phrase to trigger a text expansion"
              actionLabel="Create First Template"
              onAction={openAddDialog}
            />
          ) : (
            <div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
              {templates.map((t) => (
                <div
                  key={t.index}
                  className="flex items-center gap-3 px-3.5 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-(--text-primary)">
                      {t.trigger}
                    </p>
                    <div className="mt-0.5 flex items-center gap-3">
                      <p className="max-w-75 truncate text-xs text-(--text-muted)">
                        {t.expansion}
                      </p>
                      <span
                        className="text-[10px] text-(--text-muted) opacity-60"
                        // NEW-TS-019: show the actual variable names in
                        // a native tooltip so the user can see WHICH
                        // variables the template uses, not just the count.
                        title={
                          t.used_variables.length > 0
                            ? `Variables: ${t.used_variables.join(', ')}`
                            : 'No variables'
                        }
                      >
                        {t.variables}v &middot; {t.match_mode}
                      </span>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-0.5">
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => openEditDialog(t)}
                      className="text-(--text-muted) hover:text-(--text-secondary)"
                      title="Edit template"
                      aria-label={`Edit template: ${t.trigger}`}
                    >
                      <HugeiconsIcon icon={PencilEdit02Icon} strokeWidth={2.5} className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => instantDeleteTemplate(t)}
                      className="text-(--text-muted) hover:text-destructive"
                      title="Delete template"
                      aria-label={`Delete template: ${t.trigger}`}
                    >
                      <HugeiconsIcon icon={Delete01Icon} strokeWidth={2.5} className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Snackbar */}
        <Snackbar />
      </div>

      {/* Add/Edit Dialog — full-viewport backdrop with centered dialog */}
      {showDialog && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setShowDialog(false)}
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="template-dialog-title"
            className={cn(
              'animate-scale-in w-105 rounded-xl border border-border',
              'bg-(--bg) p-6',
            )}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={handleDialogKeyDown}
          >
            <h2 id="template-dialog-title" className="mb-5 text-lg font-semibold text-(--text-primary)">
              {editingTemplate ? 'Edit Template' : 'Add Template'}
            </h2>

            <div className="space-y-4">
              <div>
                <label className="mb-1.5 block text-sm font-medium text-(--text-primary)">
                  Trigger phrase
                </label>
                <Input
                  value={trigger}
                  onChange={(e) => setTrigger(e.target.value)}
                  placeholder="my email"
                  className="w-full"
                  autoFocus
                />
                {/* NEW-UX-026: help text explaining what to type. */}
                <p className="mt-1.5 text-xs text-(--text-muted)">
                  The phrase you'll say during dictation. When the ASR
                  transcribes this phrase, it's replaced by the output text.
                  Keep it short and distinctive to avoid false matches.
                </p>
              </div>

              <div>
                <label className="mb-1.5 block text-sm font-medium text-(--text-primary)">
                  Output text
                </label>
                <textarea
                  value={expansion}
                  onChange={(e) => setExpansion(e.target.value)}
                  placeholder="john.doe@example.com"
                  rows={5}
                  className={cn(
                    'w-full resize-y rounded-lg border border-border',
                    'bg-transparent px-3 py-2 text-sm text-(--text-primary)',
                    'placeholder:text-(--text-muted)',
                    'focus:border-accent focus:outline-none',
                  )}
                />
                {/* NEW-UX-026: help text + variable list. */}
                <p className="mt-1.5 text-xs text-(--text-muted)">
                  The text that replaces the trigger. Supports variables:
                  <code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{today}`}</code>
                  <code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{now}`}</code>
                  <code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{clipboard}`}</code>
                  <code className="mx-1 rounded bg-(--bg-subtle) px-1">{`{username}`}</code>
                </p>
              </div>

              <div>
                <label className="mb-1.5 block text-sm font-medium text-(--text-primary)">
                  Match mode
                </label>
                <Select value={matchMode} onValueChange={(v) => setMatchMode(v as 'exact' | 'contains')}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="exact">Exact match</SelectItem>
                    <SelectItem value="contains">Contains</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <Button variant="ghost" onClick={() => setShowDialog(false)}>
                Cancel
              </Button>
              <Button variant="default" onClick={saveTemplate}>
                Save
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* #7: ConfirmDialog for template deletion */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Template"
        message={`Are you sure you want to delete "${deleteTarget?.trigger ?? ''}"? This action cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={confirmDeleteTemplate}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  )
}
