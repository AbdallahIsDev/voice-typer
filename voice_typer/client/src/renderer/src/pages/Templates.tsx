import { useState, useEffect, useCallback } from 'react'
import { usePython } from '@/hooks/usePython'
import { useSnackbar } from '@/hooks/useSnackbar'
import { HugeiconsIcon } from '@hugeicons/react'
import {
  File02Icon,
  Add01Icon,
  PencilEdit02Icon,
  Delete01Icon,
} from '@hugeicons/core-free-icons'
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

// Templates are stored client-side in localStorage because the Python Config
// dataclass has no `templates_data` field and the IPC server drops writes
// for unknown keys. See docs/agents/gap-fix-prompt.md and the user's brief.
const STORAGE_KEY = 'templates_data'

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
}

function loadTemplates(): Template[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) ?? '[]'
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as Template[]) : []
  } catch {
    return []
  }
}

// #6: saveTemplates now accepts an optional callFn for IPC persistence.
// Add/edit paths pass the IPC call function so the server is notified.
// Delete path also passes callFn so the server stays in sync.
function saveTemplates(items: Template[], callFn?: <T>(cmd: string, data?: Record<string, unknown>) => Promise<T>): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(items))
  // #6: fire-and-forget IPC save so the backend stays in sync
  if (callFn) {
    callFn('save_templates', { templates: items }).catch((err: unknown) => {
      console.error('IPC save_templates failed:', err)
    })
  }
}

function toRows(items: Template[]): TemplateRow[] {
  return items.map((t, i) => ({
    index: i,
    trigger: t.trigger ?? '',
    expansion: t.output ?? '',
    match_mode: t.match_mode ?? 'exact',
    variables: VARIABLES.filter((v) => (t.output ?? '').includes(v)).length,
  }))
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

  const loadRows = useCallback(() => {
    setTemplates(toRows(loadTemplates()))
    setLoading(false)
  }, [])

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
      const items = loadTemplates()
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
      loadRows()
    } catch (err) {
      console.error('Failed to save template', err)
      showSnack('Failed to save template', 'error')
    }
  }

  // #7: Request confirmation before deleting a template
  const requestDeleteTemplate = (t: TemplateRow) => {
    setDeleteTarget(t)
  }

  // #6: Delete now passes call to saveTemplates so IPC notify happens
  const confirmDeleteTemplate = () => {
    if (!deleteTarget) return
    try {
      const items = loadTemplates()
      items.splice(deleteTarget.index, 1)
      // #6: pass call so the backend is notified of deletion
      saveTemplates(items, call)
      showSnack(`Deleted: ${deleteTarget.trigger}`, 'warning')
      loadRows()
    } catch (err) {
      console.error('Failed to delete template', err)
      showSnack('Failed to delete template', 'error')
    } finally {
      setDeleteTarget(null)
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
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
            className="gap-2"
          >
            <HugeiconsIcon icon={Add01Icon} strokeWidth={1.625} className="h-4 w-4" />
            Add Template
          </Button>
        </PageHeading>

        <div>
          {templates.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-4 py-20">
              <HugeiconsIcon icon={File02Icon} strokeWidth={1.625} className="h-12 w-12 text-(--text-muted) opacity-30" />
              <p className="text-base font-medium text-(--text-muted)">No templates yet</p>
              <p className="text-sm text-(--text-muted) opacity-70">
                Say a phrase to trigger a text expansion
              </p>
              <Button variant="default" className="mt-2 gap-2" onClick={openAddDialog}>
                <HugeiconsIcon icon={Add01Icon} strokeWidth={1.625} className="h-4 w-4" />
                Create First Template
              </Button>
            </div>
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
                      <span className="text-[10px] text-(--text-muted) opacity-60">
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
                    >
                      <HugeiconsIcon icon={PencilEdit02Icon} strokeWidth={1.625} className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => requestDeleteTemplate(t)}
                      className="text-(--text-muted) hover:text-destructive"
                      title="Delete template"
                    >
                      <HugeiconsIcon icon={Delete01Icon} strokeWidth={1.625} className="h-4 w-4" />
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
            className={cn(
              'animate-scale-in w-105 rounded-xl border border-border',
              'bg-(--bg) p-6',
            )}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="mb-5 text-lg font-semibold text-(--text-primary)">
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
