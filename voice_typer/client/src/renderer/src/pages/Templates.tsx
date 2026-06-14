import { useState, useEffect, useCallback } from 'react'
import { HugeiconsIcon } from '@hugeicons/react'
import {
  File02Icon,
  Add01Icon,
  Edit01Icon,
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

function saveTemplates(items: Template[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(items))
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
  const [templates, setTemplates] = useState<TemplateRow[]>([])
  const [loading, setLoading] = useState(true)
  const [showDialog, setShowDialog] = useState(false)
  const [editingTemplate, setEditingTemplate] = useState<TemplateRow | null>(null)
  const [trigger, setTrigger] = useState('')
  const [expansion, setExpansion] = useState('')
  const [matchMode, setMatchMode] = useState<'exact' | 'contains'>('exact')
  const [snackbar, setSnackbar] = useState<{ message: string; type: 'success' | 'error' | 'warning' } | null>(null)

  const loadRows = useCallback(() => {
    setTemplates(toRows(loadTemplates()))
    setLoading(false)
  }, [])

  useEffect(() => {
    loadRows()
  }, [loadRows])

  const showSnack = (message: string, type: 'success' | 'error' | 'warning') => {
    setSnackbar({ message, type })
    setTimeout(() => setSnackbar(null), 3000)
  }

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
      saveTemplates(items)
      setShowDialog(false)
      loadRows()
    } catch (err) {
      console.error('Failed to save template', err)
      showSnack('Failed to save template', 'error')
    }
  }

  const deleteTemplate = (t: TemplateRow) => {
    try {
      const items = loadTemplates()
      items.splice(t.index, 1)
      saveTemplates(items)
      showSnack(`Deleted: ${t.trigger}`, 'warning')
      loadRows()
    } catch (err) {
      console.error('Failed to delete template', err)
      showSnack('Failed to delete template', 'error')
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="animate-fade-in-up mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 py-6">
      <PageHeading
        title="Templates"
        description="Create voice shortcuts that expand into full text"
      >
        <Button variant="default" className="gap-2" onClick={openAddDialog}>
          <HugeiconsIcon icon={Add01Icon} className="h-4 w-4" />
          Add Template
        </Button>
      </PageHeading>

      <div>
        {templates.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-4 py-20">
            <HugeiconsIcon icon={File02Icon} className="h-12 w-12 text-(--text-muted) opacity-30" />
            <p className="text-base font-medium text-(--text-muted)">No templates yet</p>
            <p className="text-sm text-(--text-muted) opacity-70">
              Say a phrase to trigger a text expansion
            </p>
            <Button variant="default" className="mt-2 gap-2" onClick={openAddDialog}>
              <HugeiconsIcon icon={Add01Icon} className="h-4 w-4" />
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
                  <button
                    onClick={() => openEditDialog(t)}
                    className="rounded p-1 text-(--text-muted) hover:text-(--text-secondary) transition-colors cursor-pointer bg-transparent border-none"
                    title="Edit template"
                  >
                    <HugeiconsIcon icon={Edit01Icon} className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => deleteTemplate(t)}
                    className="rounded p-1 text-(--text-muted) hover:text-destructive transition-colors cursor-pointer bg-transparent border-none"
                    title="Delete template"
                  >
                    <HugeiconsIcon icon={Delete01Icon} className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add/Edit Dialog */}
      {showDialog && (
        <div className="animate-fade-in fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div
            className={cn(
              'animate-scale-in w-105 rounded-xl border border-border',
              'bg-(--bg) p-6 shadow-2xl',
            )}
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
                  placeholder="e.g., my email"
                  className="w-full"
                />
              </div>

              <div>
                <label className="mb-1.5 block text-sm font-medium text-(--text-primary)">
                  Output text
                </label>
                <textarea
                  value={expansion}
                  onChange={(e) => setExpansion(e.target.value)}
                  placeholder="e.g., john.doe@example.com"
                  rows={3}
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

      {/* Snackbar */}
      {snackbar && (
        <div
          className={cn(
            'animate-slide-up fixed bottom-6 left-1/2 z-50 -translate-x-1/2',
            'rounded-lg px-4 py-2.5 text-sm shadow-lg',
            snackbar.type === 'success' && 'bg-primary text-primary-foreground',
            snackbar.type === 'error' && 'bg-destructive text-white',
            snackbar.type === 'warning' && 'bg-primary text-primary-foreground',
          )}
        >
          {snackbar.message}
        </div>
      )}
    </div>
  )
}
