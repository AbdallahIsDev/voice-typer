import { useState, useEffect, useCallback } from 'react'
import { usePython } from '@/hooks/usePython'
import { HugeiconsIcon } from '@hugeicons/react'
import {
  BookOpen02Icon,
  Add01Icon,
  PencilEdit02Icon,
  Delete01Icon,
} from '@hugeicons/core-free-icons'
import { Search01Icon } from '@hugeicons/core-free-icons'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import PageHeading from '@/components/PageHeading'
import ExportFormatMenu from '@/components/ExportFormatMenu'
import { cn } from '@/lib/utils'
import type { VocabularyData, VocabularyEntry } from '@/types/ipc'

// ── Backend categories (kept internally for save-back, hidden from UI) ──

const CATEGORIES = [
  'misspellings',
  'phrase_corrections',
  'extra_word_patterns',
  'technical_terms',
  'names',
  'products',
] as const

/** Flatten category-shaped VocabularyData into a flat array. */
function flattenEntries(data: VocabularyData): VocabularyEntry[] {
  const items: VocabularyEntry[] = []
  for (const cat of CATEGORIES) {
    const catData = (data as Record<string, unknown>)[cat]
    if (cat === 'misspellings' || cat === 'technical_terms' || cat === 'names' || cat === 'products') {
      if (typeof catData === 'object' && catData !== null) {
        for (const [key, val] of Object.entries(catData as Record<string, string>)) {
          items.push({ category: cat, original: key, correction: String(val) })
        }
      }
    } else if (cat === 'phrase_corrections' || cat === 'extra_word_patterns') {
      if (Array.isArray(catData)) {
        for (const entry of catData) {
          if (Array.isArray(entry) && entry.length >= 2) {
            items.push({ category: cat, original: entry[0] as string, correction: entry[1] as string })
          }
        }
      }
    }
  }
  return items
}

/** Auto-detect category: phrases (spaces) go to phrase_corrections, single words to misspellings. */
function detectCategory(trigger: string): 'misspellings' | 'phrase_corrections' {
  return trigger.includes(' ') ? 'phrase_corrections' : 'misspellings'
}

/** Rebuild category-shaped VocabularyData from a flat array for server save. */
function rebuildData(entries: VocabularyEntry[]): VocabularyData {
  const data: VocabularyData = {}
  for (const cat of CATEGORIES) {
    const filtered = entries.filter((e) => e.category === cat)
    if (cat === 'misspellings' || cat === 'technical_terms' || cat === 'names' || cat === 'products') {
      const dict: Record<string, string> = {}
      for (const e of filtered) {
        dict[e.original] = e.correction
      }
      data[cat] = dict
    } else {
      data[cat] = filtered.map((e) => [e.original, e.correction] as [string, string])
    }
  }
  return data
}

// ── Component ──────────────────────────────────────────────────────

export default function VocabularyPage() {
  const { call } =  usePython()
  const [entries, setEntries] = useState<VocabularyEntry[]>([])

  const [searchQuery, setSearchQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [showDialog, setShowDialog] = useState(false)
  const [editingEntry, setEditingEntry] = useState<VocabularyEntry | null>(null)
  const [trigger, setTrigger] = useState('')
  const [replacement, setReplacement] = useState('')
  const [snackbar, setSnackbar] = useState<{ message: string; type: 'success' | 'error' | 'warning' } | null>(null)

  const showSnack = (message: string, type: 'success' | 'error' | 'warning') => {
    setSnackbar({ message, type })
    setTimeout(() => setSnackbar(null), 3000)
  }

  const doExport = useCallback(async (format: 'json' | 'csv') => {
    try {
      const data = await call<VocabularyData>('get_vocabulary')
      // Flatten and strip internal categories before passing to the bridge
      const flatData = flattenEntries(data ?? {}).map(e => ({
        original: e.original,
        correction: e.correction,
      }))
      const bridge = window.window_ as any
      if (!bridge?.exportVocabulary) {
        toast.error('Export not available — please restart the app')
        return
      }
      const result = await bridge.exportVocabulary({ entries: flatData }, format)
      if (result.success) {
        const filename = result.path!.split(/[\\/]/).pop()!
        toast.success(`${filename} saved successfully`)
      }
    } catch (err) {
      console.error('Vocabulary export failed:', err)
      toast.error('Export failed')
    }
  }, [call])

  const loadVocabulary = useCallback(async () => {
    setLoading(true)
    try {
      const data = await call<VocabularyData>('get_vocabulary')
      setEntries(flattenEntries(data ?? {}))
    } catch (err) {
      console.error('Failed to load vocabulary:', err)
      setEntries([])
    } finally {
      setLoading(false)
    }
  }, [call])

  useEffect(() => {
    loadVocabulary()
  }, [loadVocabulary])

  const persistVocabulary = useCallback(async (updated: VocabularyEntry[]) => {
    const data = rebuildData(updated)
    setSaving(true)
    try {
      await call('save_vocabulary', data as unknown as Record<string, unknown>)
    } catch (err) {
      console.error('Failed to save vocabulary:', err)
      throw err
    } finally {
      setSaving(false)
    }
  }, [call])

  // ── Search ─────────────────────────────────────────────────────────

  const filtered = searchQuery.trim()
    ? entries.filter((e) =>
        e.original.toLowerCase().includes(searchQuery.toLowerCase()) ||
        e.correction.toLowerCase().includes(searchQuery.toLowerCase()),
      )
    : entries

  // ── Add / Edit dialog ─────────────────────────────────────────────

  const openAddDialog = () => {
    setEditingEntry(null)
    setTrigger('')
    setReplacement('')
    setShowDialog(true)
  }

  const openEditDialog = (entry: VocabularyEntry) => {
    setEditingEntry(entry)
    setTrigger(entry.original)
    setReplacement(entry.correction)
    setShowDialog(true)
  }

  const saveEntry = async () => {
    const t = trigger.trim()
    const r = replacement.trim()
    if (!t || !r) {
      showSnack('Please fill in both fields', 'warning')
      return
    }
    try {
      let updated: VocabularyEntry[]
      if (editingEntry) {
        updated = entries.map((e) =>
          e === editingEntry
            ? { category: detectCategory(t), original: t, correction: r }
            : e,
        )
      } else {
        updated = [...entries, { category: detectCategory(t), original: t, correction: r }]
      }
      await persistVocabulary(updated)
      setEntries(updated)
      setShowDialog(false)
      showSnack(
        editingEntry ? `Updated: ${t} → ${r}` : `Added: ${t} → ${r}`,
        'success',
      )
    } catch {
      showSnack('Failed to save entry', 'error')
    }
  }

  const deleteEntry = async (entry: VocabularyEntry) => {
    try {
      const updated = entries.filter((e) => e !== entry)
      await persistVocabulary(updated)
      setEntries(updated)
      showSnack(`Deleted: ${entry.original}`, 'warning')
    } catch {
      showSnack('Failed to delete entry', 'error')
    }
  }

  // ── Render ────────────────────────────────────────────────────────

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
          title="Custom Vocabulary"
          description="Add custom words and corrections to improve accuracy"
        >
          <div className="flex items-center gap-2">
            <ExportFormatMenu onExport={doExport} disabled={entries.length === 0} />
            <Button
              variant="outline"
              size="sm"
              onClick={openAddDialog}
              disabled={saving}
              className="gap-2"
            >
              <HugeiconsIcon icon={Add01Icon} strokeWidth={1.625} className="h-4 w-4" />
              Add Word
            </Button>
          </div>
        </PageHeading>

        {/* Search */}
        <div className="relative">
          <HugeiconsIcon icon={Search01Icon} strokeWidth={1.625} className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-(--text-muted) pointer-events-none" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search vocabulary..."
            className="pl-9 rounded-xl bg-(--bg-subtle)"
          />
        </div>

        {/* List */}
        <div className="mt-4">
          {entries.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-4 py-16">
              <HugeiconsIcon icon={BookOpen02Icon} strokeWidth={1.625} className="h-10 w-10 text-(--text-muted) opacity-30" />
              <p className="text-sm text-(--text-muted)">No vocabulary entries yet</p>
              <p className="text-xs text-(--text-muted) opacity-70">
                Add words or phrases that Voice Typer should correct
              </p>
              <Button variant="outline" className="mt-2 gap-2" onClick={openAddDialog}>
                <HugeiconsIcon icon={Add01Icon} strokeWidth={1.625} className="h-4 w-4" />
                Add Your First Word
              </Button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3">
              <HugeiconsIcon icon={BookOpen02Icon} strokeWidth={1.625} className="h-8 w-8 text-(--text-muted) opacity-30" />
              <p className="text-sm text-(--text-muted)">No results found</p>
            </div>
          ) : (
            <div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
              {filtered.map((entry, idx) => (
                <div
                  key={`${entry.original}-${entry.category}-${idx}`}
                  className="flex items-start gap-3 px-3.5 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2.5">
                      <span className="text-sm font-medium text-destructive">
                        {entry.original}
                      </span>
                      <span className="text-sm text-(--text-muted)">→</span>
                      <span className="text-sm font-semibold text-primary">
                        {entry.correction}
                      </span>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => openEditDialog(entry)}
                      className="text-(--text-muted) hover:text-accent"
                      title="Edit"
                    >
                      <HugeiconsIcon icon={PencilEdit02Icon} strokeWidth={1.625} className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => deleteEntry(entry)}
                      className="text-(--text-muted) hover:text-destructive"
                      title="Delete"
                    >
                      <HugeiconsIcon icon={Delete01Icon} strokeWidth={1.625} className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Count footer */}
        {entries.length > 0 && !searchQuery.trim() && (
          <p className="mt-3 text-[10px] text-(--text-muted) text-center opacity-50">
            {entries.length} entr{entries.length === 1 ? 'y' : 'ies'}
          </p>
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
      </div>        {/* Add/Edit Dialog — full-viewport backdrop with centered dialog */}
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
              {editingEntry ? 'Edit Vocabulary Entry' : 'Add Vocabulary Entry'}
            </h2>

            <div className="space-y-4">
              <div>
                <label className="mb-1.5 block text-sm font-medium text-(--text-primary)">
                  What you say
                </label>
                <Input
                  value={trigger}
                  onChange={(e) => setTrigger(e.target.value)}
                  placeholder="treat three, mynameis"
                  className="w-full"
                  autoFocus
                />
              </div>

              <div>
                <label className="mb-1.5 block text-sm font-medium text-(--text-primary)">
                  What gets typed instead
                </label>
                <Input
                  value={replacement}
                  onChange={(e) => setReplacement(e.target.value)}
                  placeholder="treat this, My Name Is"
                  className="w-full"
                />
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <Button variant="ghost" onClick={() => setShowDialog(false)}>
                Cancel
              </Button>
              <Button variant="default" onClick={saveEntry} disabled={!trigger.trim() || !replacement.trim()}>
                Save
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
